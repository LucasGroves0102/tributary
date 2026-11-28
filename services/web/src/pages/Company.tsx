import * as Menu from "@radix-ui/react-dropdown-menu";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, companyEvents, expectedFilingsOf, screener } from "../api";
import { foreignReport, header, HEADER_COLUMNS, HEADER_COLUMNS_WITH_BANK, KEY_STATS, longDay, STATEMENTS } from "../company/data";
import { DataNotes } from "../company/DataNotes";
import { EarningsView } from "../company/Earnings";
import { resultsOut } from "../company/events";
import { Filings } from "../company/Filings";
import { Overview } from "../company/Overview";
import { Peers } from "../company/Peers";
import { Price } from "../company/Price";
import { useMarketData } from "../market/useMarketData";
import { Revenue } from "../company/Revenue";
import type { ChartState } from "../chart/model";
import type { StatementView } from "../company/statementView";
import { Ratios, Segments, Statements } from "../company/Tables";
import { useLoad } from "../company/useLoad";
import { useDirectory } from "../companies/directory";
import { placeName } from "../companies/places";
import { confidenceWords } from "../companies/rows";
import { Choices } from "../components/Choices";
import { Notice } from "../components/Notice";
import { Why } from "../components/Why";
import { ErrorPanel, Skeleton } from "../components/States";
import { day, lateFiling, localToday } from "../format";
import { reduced, viaKeyboard } from "../motion";
import { COMPANY_TABS, EARLIEST_AS_OF, asOfDay, go, href, type CompanyTab } from "../route";
import { NEW_WORK_TITLE, NewWorkForm, newWorkOffline } from "../workspaces/NewWork";
import type { WrittenKind } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { restoreProject } from "../analysis/project";
import { HoldingLine } from "../portfolio/HoldingLine";
import { coRegistrantNote } from "../compare/peers";
import { NotFound } from "./TickerPage";

interface Props {
  token: string;
  companyId: string;
  tab: CompanyTab;
  /** the Statements tab's choices from the address */
  statements?: StatementView;
  /** the Filings tab opens What changed on this 10-K/10-Q (an alert's link) */
  changes?: string;
  /** a past day (YYYY-MM-DD, `?asof=`): the figures as they stood at its end */
  asOf?: string;
  /** null while the watchlist loads or is unavailable */
  watched: boolean | null;
  onWatch: (watch: boolean) => Promise<void>;
  onChart: () => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm") => void;
  /** a Revenue view chart opened in the chart builder */
  onOpenChart?: (s: ChartState) => void;
  onAsk: (question: string) => void;
  /** saved work, for "New thesis" / "New model" about this company (null: unavailable) */
  saved?: Saved | null;
  /** the user's plan reads this company's filings (`/v1/me`): its 8-Ks are asked at once, beside the figures,
   * so the results notice is known before the page shows and never pushes it down (Marek 12) */
  readsFilingsHint?: boolean;
}

/** The company page (.interface-design/system.md §4 Company header, §6): who the company is (public
 * metadata) in a header that ends 270px from the top at 1440 (crumb, name with its code and exchange,
 * one meta row, the actions on the title's line, at most one notice, then the tabs with As of), and its
 * data by tab under it. The header is `page-head` and the tab's panel `content` in a view transition: on
 * a tab change the header stays put and only the 2px indicator slides. While the figures load, the
 * header shows the name it already knows and the panel its own shape with the values blank. Identity,
 * the filing index and expected dates are public; values need financials, text needs filings. */
export function Company({ token, companyId, tab, statements, changes, asOf, watched, onWatch, onChart, onChartMany, onOpenChart, onAsk, saved, readsFilingsHint = false }: Props) {
  const meta = useLoad(`${companyId}|${asOf ?? ""}`, () =>
    // a bank's key stats need its own columns (F4); an older API refuses them (422): the standard set
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS_WITH_BANK, sort: "name", limit: 1, ...(asOf ? { as_of: asOf } : {}) }).catch((e) => {
      if (e instanceof ApiError && e.status === 422) return screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1, ...(asOf ? { as_of: asOf } : {}) });
      throw e;
    }).then(
      (r) => r.rows.find((x) => x.company_id === companyId) ?? null,
    ),
  );
  // the confidence mark is today's (074): as of a past day the row has none, so today's row says it
  const todayRow = useLoad(asOf ? companyId : null, () =>
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1 })
      .then((r) => r.rows.find((x) => x.company_id === companyId) ?? null));
  const expected = useLoad(companyId, () => expectedFilingsOf(token, companyId));
  const row = meta?.state === "ok" ? meta.data : null;
  // the 8-Ks since the last report, asked once for the page: the header's results notice, the Overview's
  // flags and its list (a 20-F/40-F filer files none)
  const readsFilings = !!row?.access.includes("filings") && !foreignReport(row);
  // asked beside the figures when the plan says it will be read; else once the row says so
  const events = useLoad(readsFilings || (readsFilingsHint && !row) ? `${companyId}|events` : null, () => companyEvents(token, companyId));
  // the page shows once the notice is known (a results day's notice arriving after the page pushed the tabs
  // and the hero down 31px: CLS 0.176, Marek 12), waiting at most a moment for it
  const [waited, setWaited] = useState(false);
  const eventsPending = readsFilings && (!events || events.state === "loading");
  useEffect(() => {
    if (!eventsPending || meta?.state !== "ok") return;
    const id = window.setTimeout(() => setWaited(true), 1500);
    return () => window.clearTimeout(id);
  }, [eventsPending, meta?.state]);
  // New thesis / New model from the More menu: its form opens under the actions
  const [newKind, setNewKind] = useState<WrittenKind | null>(null);
  const opening = useRef(false);
  // the Price tab exists only when the server offers "Connect your data"
  const market = useMarketData(token);
  const tabs = COMPANY_TABS.filter(([t]) => t !== "price" || market?.enabled);
  // the name comes from the directory (identity only, one small request) while the figures load: a page
  // never sits nameless for seconds (Riley, test 10, finding 15)
  const directory = useDirectory(token);
  const known = directory.name(companyId);

  if (!meta || meta.state === "loading" || (meta.state === "ok" && eventsPending && !waited)) {
    return (
      <div className="page company-page">
        <header className="co-head vt-head" data-loading="">
          <p className="crumb"><a href={href({ view: "companies" })}>Companies</a> <span className="blank-v w-160" /></p>
          <div className="co-titlerow">
            <div className="co-title">
              <h1 className="co-name co-name-early" data-entity-title={companyId} tabIndex={-1}>{known ?? <span className="blank-v w-240" />}</h1>
            </div>
          </div>
          <p className="meta co-meta"><span className="blank-v w-480" /></p>
          <CompanyTabs companyId={companyId} tab={tab} tabs={tabs} asOf={asOf} statements={statements} />
        </header>
        <PanelSkeleton tab={tab} />
      </div>
    );
  }
  if (meta.state === "error") {
    return (
      <div className="page">
        <ErrorPanel what="this company" detail={meta.message} onRetry={meta.retry} />
      </div>
    );
  }
  if (meta.state !== "ok" || meta.data === null) {
    // the one not-found page (Marek 46)
    return (
      <NotFound title="No company here">
        No company with financials or filings here has the number {companyId}. Find one under{" "}
        <a href={href({ view: "companies" })}>Companies</a>, or search with Ctrl K.
      </NotFound>
    );
  }
  const r = meta.data;
  const h = header(r);
  // charts and saved work call it this: its ticker, or its name when it has none
  const ticker = h.tickers[0] ?? h.name;
  const next = expected?.state === "ok" ? (expected.data.expected[0] ?? null) : null;
  const joint = coRegistrantNote(h.name, r.co_registrants ?? [], companyId);
  // the SEC's code for where it is incorporated, in words ("Y9" is Jersey); a code EDGAR doesn't list isn't shown
  const place = placeName(h.state);
  const more = h.tickers.length > 1 ? h.tickers.slice(1) : [];
  const confidence = asOf ? (todayRow?.state === "ok" ? todayRow.data?.confidence ?? null : null) : r.confidence;
  // results out before their report (an 8-K with item 2.02 after the latest 10-Q or 10-K): the one notice
  const results = asOf ? null : resultsOut(events?.state === "ok" ? events.data : null);
  const canFin = r.access.includes("financials");

  return (
    <div className="page company-page">
      <header className="co-head vt-head">
        <p className="crumb">
          <a href={href({ view: "companies" })}>Companies</a>
          {(h.industry || r.sector) && <span className="sep" aria-hidden="true"> / </span>}
          {r.sector && <span title="Tributary's sector, its own grouping of SIC codes">{r.sector}{r.sector_industry ? `, ${r.sector_industry}` : ""}</span>}
          {r.sector && h.industry && <span className="sep" aria-hidden="true"> · </span>}
          {h.industry && <span title={h.sic ? `SIC ${h.sic}, the SEC's industry code` : undefined}>{h.industry}{h.sic ? ` · SIC ${h.sic}` : ""}</span>}
        </p>
        <div className="co-titlerow">
          <div className="co-title">
            <h1 className="co-name" data-entity-title={companyId} tabIndex={-1}>{h.name}</h1>
            {h.tickers[0] && (
              <span className="co-listing" title={more.length ? `Also listed: ${more.join(", ")}` : undefined}>
                <b className="code">{h.tickers[0]}</b>
                {h.exchanges.length > 0 && <span className="ex"> on {h.exchanges.join(", ")}</span>}
              </span>
            )}
          </div>
          {/* the actions on the title's line: Watching, Compare, ···, then the one primary (§4) */}
          <div className="co-acts">
            {watched !== null && (
              <button className="btn hide-narrow" onClick={() => void onWatch(!watched)} aria-pressed={watched}
                title={r.access.includes("financials") ? undefined : "Watching follows its filings; it grants no access to values"}>
                {watched ? "✓ Watching" : "+ Watch"}
              </button>
            )}
            <a className="btn hide-narrow" href={href({ view: "compare", compare: { target: companyId } })}>Compare</a>
            <Menu.Root modal={false}>
              <Menu.Trigger asChild>
                <button className="btn more-btn" aria-label="More actions"><span aria-hidden="true">···</span></button>
              </Menu.Trigger>
              <Menu.Portal>
                <Menu.Content className="menu" align="end" sideOffset={6} collisionPadding={12}
                  onCloseAutoFocus={(e) => {
                    // a new thesis or model's form takes the focus, not the More button
                    if (opening.current) e.preventDefault();
                    opening.current = false;
                  }}>
                  {watched !== null && (
                    <Menu.Item className="menu-item narrow-only" onSelect={() => void onWatch(!watched)}>{watched ? "Stop watching" : "Watch"}</Menu.Item>
                  )}
                  <Menu.Item className="menu-item narrow-only" onSelect={() => go({ view: "compare", compare: { target: companyId } })}>Compare</Menu.Item>
                  {canFin && <Menu.Item className="menu-item" onSelect={() => onChart()}>Chart</Menu.Item>}
                  <Menu.Item className="menu-item" onSelect={() => onAsk(`Summarize ${h.name}'s latest quarter.`)}>Ask</Menu.Item>
                  {saved && (
                    <>
                      <Menu.Separator className="menu-sep" />
                      {(["thesis", "model"] as const).map((k) => (
                        <Menu.Item key={k} className="menu-item" disabled={newWorkOffline(saved)}
                          title={newWorkOffline(saved) ? "Saving is unavailable right now" : NEW_WORK_TITLE[k]}
                          onSelect={() => {
                            opening.current = true;
                            setNewKind(k);
                          }}>
                          New {k}
                        </Menu.Item>
                      ))}
                    </>
                  )}
                </Menu.Content>
              </Menu.Portal>
            </Menu.Root>
            {saved && canFin && <AnalysisLink saved={saved} companyId={companyId} ticker={ticker} />}
          </div>
        </div>
        <p className="meta co-meta">
          {h.fye && <span>Fiscal year ends in {h.fye}</span>}
          {h.currency && <span>Figures in {h.currency}</span>}
          {/* the facts a phone can do without go quiet there (Riley, test 10, finding 94); `.co-minor` hides below 600px of page */}
          {h.filer && <span className="co-minor">{h.filer} filer</span>}
          {place && <span className="co-minor" title={`EDGAR's code: ${h.state}`}>Incorporated in {place}</span>}
          {/* the SEC's number for the company, said as such (Riley, test 10, finding 74: its hover was about access) */}
          <span className="co-cik co-minor" title="The SEC's number for this company (its Central Index Key)">CIK {companyId}</span>
          {/* 074: how far to trust the figures, quiet; its reasons open on a tap as well as a hover (Riley, test 10, finding 40) */}
          {confidence && (() => {
            const c = confidenceWords(confidence);
            return <Why className="co-confidence" label={c.label} why={c.hover} />;
          })()}
          {/* access only when it is short of both tiers: a full reader needs no word of it */}
          {r.access.length < 2 && <span className="co-access">{accessShort(r.access)}</span>}
          {next && !results && (
            <span className="co-next" title={expected?.state === "ok" ? expected.data.method : undefined}>
              {/* the filing index is today's: as of a past day it says so (Riley, test 10, finding 18) */}
              {asOf && <span className="asof-today-k">Today:</span>} {nextWords(next)}
            </span>
          )}
        </p>
        {/* the user's own holding, quietly (portfolio, app 021): nothing when they hold none */}
        <HoldingLine token={token} companyId={companyId} today={!!asOf} />
        {joint && (
          <p className="meta co-meta joint-note" role="note">
            <span>
              {joint}{" "}
              {(r.co_registrants ?? []).map((c, i) => (
                <span key={c.company_id}>
                  {i > 0 && ", "}
                  <a href={href({ view: "company", companyId: c.company_id })}>Open {c.name}</a>
                </span>
              ))}
            </span>
          </p>
        )}
        {saved && newKind && (
          <NewWorkForm key={newKind} kind={newKind} token={token} saved={saved} company={{ id: companyId, name: ticker }}
            onDone={() => setNewKind(null)} />
        )}
        {asOf ? (
          <Notice className="co-asof-note" label={`As of ${day(asOf, true)}:`}
            action={<a href={href({ view: "company", companyId, tab, ...(statements ? { statements } : {}) })}>Back to today</a>}>
            {" "}the Overview's year and key figures, Statements and Ratios read the filings filed by the end of that day. Every other tab
            and section, the header's dates and your holding are today's, and say so.
          </Notice>
        ) : results ? (
          <Notice className="results-out" role="status" label={`Results out ${day(results.filed)}`}
            action={<a href={href({ view: "filing", companyId, accessionNo: results.accession_no, section: results.release?.section_key ?? null })}>
              {results.release ? "Read the release" : "Read the 8-K"} →
            </a>}>
            {h.name} released its results on {longDay(results.filed)}. The figures below run
            {r.latest_period ? ` to ${day(r.latest_period, true)}` : " to its last report"} until its {next?.form ?? "10-Q or 10-K"} is
            filed{next ? whenFiled(next) : ""}.
          </Notice>
        ) : null}
        <CompanyTabs companyId={companyId} tab={tab} tabs={tabs} asOf={asOf} statements={statements} />
      </header>

      <HeldPanels tab={tab} render={(t) => (
        <>
          {asOf && !AS_OF_TABS.includes(t) && t !== "overview" && (
            <p className="asof-today tab-today" role="note">
              This tab shows today's {TODAY_WORDS[t] ?? "data"}, not as it stood on {day(asOf, true)}: only the Overview's year and key figures,
              Statements and Ratios follow the day. <a href={href({ view: "company", companyId, tab: t })}>Back to today</a>
            </p>
          )}
          {t === "overview" && <Overview token={token} companyId={companyId} name={h.name} row={r} access={r.access} asOf={asOf} events={events} resultsInHeader={!asOf && !!results} />}
          {t === "earnings" && (
            <EarningsView token={token} companyId={companyId} name={h.name} canReadFilings={r.access.includes("filings")}
              canReadFinancials={r.access.includes("financials")} />
          )}
          {t === "statements" && <Statements token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={r.not_applicable} view={statements} asOf={asOf}
            canRead={r.access.includes("filings")} fiscalYearEnd={r.fiscal_year_end} resultsInHeader={!asOf && !!results} />}
          {t === "revenue" && (
            <Revenue token={token} companyId={companyId} name={h.name} ticker={ticker} saved={saved ?? null} onOpenChart={onOpenChart} />
          )}
          {t === "segments" && <Segments token={token} companyId={companyId} name={h.name} />}
          {t === "ratios" && <Ratios token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={r.not_applicable} asOf={asOf} />}
          {t === "filings" && <Filings key={changes ?? ""} token={token} companyId={companyId} name={h.name} canRead={r.access.includes("filings")} fiscalYearEnd={r.fiscal_year_end} initialDiff={changes}
            foreign={foreignReport(r)} />}
          {t === "notes" && <DataNotes token={token} companyId={companyId} name={h.name} canReadFilings={r.access.includes("filings")} />}
          {t === "peers" && <Peers token={token} companyId={companyId} name={h.name} onChart={onChartMany} />}
          {t === "price" && <Price token={token} companyId={companyId} name={h.name} />}
        </>
      )} />
    </div>
  );
}

/** The tab's panel. A tab opened for the first time keeps the old one on screen until its own content is
 * in (nothing `aria-busy` left in it) or a second has passed, so a tab change cuts from content to content,
 * never through a 200ms flash of its loading shape (Marek 35, spec §7.6); the new one mounts at once,
 * hidden, so its requests start at the click. */
function HeldPanels({ tab, render }: { tab: CompanyTab; render: (t: CompanyTab) => ReactNode }) {
  const [shown, setShown] = useState(tab);
  const pending = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (shown === tab) return;
    const el = pending.current;
    const done = () => setShown(tab);
    const ready = () => !el?.querySelector('[aria-busy="true"]');
    if (!el || typeof MutationObserver === "undefined" || ready()) return done();
    const seen = new MutationObserver(() => {
      if (ready()) done();
    });
    seen.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-busy"] });
    const id = window.setTimeout(done, 1000);
    return () => {
      seen.disconnect();
      window.clearTimeout(id);
    };
  }, [tab, shown]);
  return (
    <>
      {(shown === tab ? [tab] : [shown, tab]).map((t) => (
        <div key={t} ref={t === shown ? undefined : pending} className={t === shown ? "co-panel vt-content" : "co-panel"} hidden={t !== shown}>
          {render(t)}
        </div>
      ))}
    </>
  );
}

/** The tabs (§4): links, one per route, 40px, the current one in ink over a 2px indicator that slides
 * 180ms to it (instant from the keyboard and under reduced motion); As of at the right, or on its own
 * line above the tabs where the page is narrower than 960px (a tablet; the assistant open at 1440).
 * Tabs that don't fit go into a "More" menu at the strip's end, never cut or hidden (Marek 7: at 768
 * "Filings" showed as "F" and Data notes and Peers were gone with no sign of them); the current tab,
 * if it's one of those, names the menu. */
function CompanyTabs({ companyId, tab, tabs, asOf, statements }: {
  companyId: string; tab: CompanyTab; tabs: (typeof COMPANY_TABS)[number][]; asOf?: string; statements?: StatementView;
}) {
  const nav = useRef<HTMLElement>(null);
  const ink = useRef<HTMLSpanElement>(null);
  const placed = useRef(false);
  // how many tabs show in the strip; the rest are in More
  const [fit, setFit] = useState(tabs.length);
  const widths = useRef<number[]>([]);
  useLayoutEffect(() => {
    const el = nav.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const links = [...el.querySelectorAll<HTMLElement>(":scope > a")];
      // a tab in More has no width now: it keeps the one it had in the strip
      links.forEach((a, i) => {
        if (a.offsetWidth) widths.current[i] = a.offsetWidth;
      });
      const gap = 2;
      const room = el.clientWidth;
      const each = widths.current.slice(0, tabs.length);
      const all = each.reduce((a, w) => a + w + gap, 0);
      if (all <= room + 1) return setFit(tabs.length);
      const more = el.querySelector<HTMLElement>(":scope > .tab-more")?.offsetWidth || 96;
      let used = more;
      let n = 0;
      for (const w of each) {
        if (used + w + gap > room) break;
        used += w + gap;
        n++;
      }
      setFit(Math.max(1, n));
    };
    measure();
    const seen = new ResizeObserver(measure);
    seen.observe(el);
    void document.fonts?.ready.then(measure);
    return () => seen.disconnect();
  }, [tabs.length]);
  const rest = tabs.slice(fit);
  const inMore = rest.find(([t]) => t === tab);
  useLayoutEffect(() => {
    const on = nav.current?.querySelector<HTMLElement>('a[aria-current="page"]:not([hidden]), .tab-more.on');
    const i = ink.current;
    if (!i || !on) return;
    const still = !placed.current || viaKeyboard() || reduced();
    if (still) i.classList.add("no-anim");
    i.style.width = `${on.offsetWidth}px`;
    i.style.transform = `translateX(${on.offsetLeft}px)`;
    if (still) {
      void i.offsetWidth;
      i.classList.remove("no-anim");
    }
    placed.current = true;
  }, [tab, tabs.length, fit]);
  const link = (t: CompanyTab) => href({ view: "company", companyId, tab: t, ...(asOf ? { asOf } : {}) });
  return (
    <div className="co-tabsrow">
      <nav className="tabs co-tabs" aria-label="Company sections" ref={nav}>
        {tabs.map(([t, label], i) => (
          <a key={t} href={link(t)} aria-current={t === tab ? "page" : undefined} hidden={i >= fit || undefined}>
            {label}
          </a>
        ))}
        {rest.length > 0 && (
          <Menu.Root modal={false}>
            <Menu.Trigger asChild>
              <button type="button" className={inMore ? "tab-more on" : "tab-more"}
                aria-label={inMore ? `More sections, showing ${inMore[1]}` : "More sections"}>
                {inMore ? inMore[1] : "More"}
                <span className="more-caret" aria-hidden="true" />
              </button>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu" align="end" sideOffset={6} collisionPadding={12}>
                {rest.map(([t, label]) => (
                  <Menu.Item key={t} asChild className="menu-item">
                    <a href={link(t)} aria-current={t === tab ? "page" : undefined}>{label}</a>
                  </Menu.Item>
                ))}
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        )}
        <span className="tab-ink" ref={ink} aria-hidden="true" />
      </nav>
      <AsOfControl companyId={companyId} tab={tab} asOf={asOf} statements={statements} />
    </div>
  );
}

/** The tab's own shape while its figures load (§7.6): real section titles, row labels and rules, the
 * values blank; after a second the blanks pulse slowly to say it's still working. */
function PanelSkeleton({ tab }: { tab: CompanyTab }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), 1000);
    return () => window.clearTimeout(id);
  }, []);
  const loading = slow ? "slow" : "";
  const title = (name: string, line?: ReactNode) => (
    <header className="rubric"><h2>{name}</h2>{line && <p>{line}</p>}</header>
  );
  const blanks = (n: number, w = 64) => Array.from({ length: n }, (_, i) => <span key={i} className={`blank-v w-${w}`} />);
  return (
    <div className="co-panel vt-content" data-loading={loading} aria-busy="true">
      <span className="visually-hidden" role="status">Loading the company</span>
      {tab === "overview" ? (
        <div className="ov">
          <div className="ov-lead">
            <section className="ov-hero">
              <p className="ov-label"><span className="blank-v w-240" /></p>
              <div className="hero"><span className="blank-v hero-blank" /></div>
              <div className="footing">{blanks(5, 64).map((b, i) => <span key={i} className="term-blank">{b}<small>&nbsp;</small></span>)}</div>
            </section>
            <section className="folio">
              {title("The rest of the twelve months")}
              <div className="ledger">
                {[KEY_STATS.slice(4, 11).filter((s) => s.kind === "usd" || s.kind === "eps"), KEY_STATS.filter((s) => s.kind === "pct" || s.kind === "x")].map((col, i) => (
                  <dl key={i}>
                    {col.map((s) => <div key={s.key}><dt>{s.label}</dt><dd><span className="blank-v w-64" /></dd></div>)}
                  </dl>
                ))}
              </div>
            </section>
            <section className="folio">{title("Latest quarter")}</section>
          </div>
          <div className="ov-side">
            <section className="folio">{title("Revenue by quarter")}<div className="qbars-blank" /></section>
            <section className="folio">{title("Since the last report")}</section>
          </div>
        </div>
      ) : tab === "statements" ? (
        <section className="folio statements">
          {title("Statement")}
          <div className="tw-skel">
          <table className="t led statement">
            <thead><tr><th className="first">{STATEMENTS.is.label}</th>{Array.from({ length: 8 }, (_, i) => <th key={i}><span className="blank-v w-48" /></th>)}</tr></thead>
            <tbody>
              {STATEMENTS.is.rows.slice(0, 12).map(([m, label, , opts]) => (
                <tr key={m} className={[opts?.sub && "sub", opts?.total && "total"].filter(Boolean).join(" ") || undefined}>
                  <td className="first">{label}</td>
                  {Array.from({ length: 8 }, (_, i) => <td key={i}><span className="blank-v w-48" /></td>)}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </section>
      ) : (
        <Skeleton lines={6} label="Loading the company" />
      )}
    </div>
  );
}

/** The tabs whose figures follow the As of day; the others show today's (each says so at its top; the
 * Overview, section by section). */
const AS_OF_TABS: readonly CompanyTab[] = ["statements", "ratios"];

/** What a today's tab shows, for its as-of note (Riley, test 10, finding 19: Peers showed today's
 * figures with no word of it). */
const TODAY_WORDS: Partial<Record<CompanyTab, string>> = {
  earnings: "earnings releases and reported figures", revenue: "revenue breakdowns", segments: "segments",
  filings: "filing list", notes: "data notes", peers: "peers and their figures", price: "prices",
};

/** "As of": Today, or a past day for the figures (point in time, query-api point_in_time.py), in the
 * address (`?asof=`) so it can be linked. A segmented Today | Pick a day; the day is typed in a field
 * (never the browser's own date input, §2.5). */
function AsOfControl({ companyId, tab, asOf, statements }: { companyId: string; tab: CompanyTab; asOf?: string; statements?: StatementView }) {
  const today = localToday();
  const [picking, setPicking] = useState(!!asOf);
  const [typed, setTyped] = useState(asOf ?? "");
  useEffect(() => {
    setTyped(asOf ?? "");
    if (asOf) setPicking(true);
  }, [asOf]);
  const set = (value: string) => {
    const picked = asOfDay(value);
    go({ view: "company", companyId, tab, ...(statements ? { statements } : {}), ...(picked && picked < today ? { asOf: picked } : {}) });
  };
  const valid = (() => {
    const d = asOfDay(typed);
    return !!d && d < today;
  })();
  return (
    <div className={asOf ? "co-asof on" : "co-asof"}>
      <span className="co-asof-k" aria-hidden="true">As of</span>
      <Choices label="As of" value={picking ? "day" : "today"} options={[["today", "Today"], ["day", "Pick a day"]]}
        set={(v) => {
          if (v === "today") {
            setPicking(false);
            if (asOf) set("");
          } else setPicking(true);
        }} />
      {picking && (
        <form className="co-asof-field" onSubmit={(e) => {
          e.preventDefault();
          if (valid) set(typed);
        }}>
          <input className="field narrow" inputMode="numeric" placeholder="YYYY-MM-DD" aria-label={`A past day, from ${EARLIEST_AS_OF}`}
            pattern="\d{4}-\d{2}-\d{2}" value={typed} onChange={(e) => setTyped(e.target.value)}
            title="A past day: the figures as they stood at its end, from the filings filed by then" autoFocus={!asOf} />
          <button type="submit" className="btn" disabled={!valid || typed === asOf}>Show</button>
        </form>
      )}
    </div>
  );
}

/** When the next report is expected, for the results notice: ", about Nov 25" while that's ahead; once
 * it has passed, that it's late (Marek 9), or past the SEC's deadline (the API's overdue). */
function whenFiled(next: { expected_date: string | null; deadline?: string | null; status: string }): string {
  if (next.status === "overdue") return `: it's past the SEC's deadline${next.deadline ? ` (${day(next.deadline)})` : ""}`;
  if (!next.expected_date) return "";
  return lateFiling(next) ? `: it's late, expected about ${day(next.expected_date)}` : `, about ${day(next.expected_date)}`;
}

/** The next periodic report in plain words (Riley, test 10, finding 53: "(an estimate, due)"): when it's
 * expected, and that the date is an estimate; a report past its date says so. */
export function nextWords(next: { form: string; period_end: string; expected_date: string | null; status: string }): string {
  const when = next.expected_date ? `, expected around ${day(next.expected_date, true)}` : "";
  const status = next.status === "overdue" ? ": past the SEC's deadline"
    : lateFiling(next) ? ": late, not filed yet"
      : next.status === "due" ? ": due about now (an estimated date)"
        : next.expected_date ? " (an estimated date)" : "";
  return `Next ${next.form}, for the period ending ${day(next.period_end, true)}${when}${status}`;
}

/** What a reader short of a tier can read, in a few words (the masthead says it only then). */
function accessShort(access: string[]): string {
  if (!access.length) return "Your plan: metadata only";
  return access.includes("financials") ? "Your plan: financials, not filing text" : "Your plan: filing text, not financials";
}

/** The two ways to start research, side by side where the user chooses (L5): an analysis (the
 * guided project, its thesis and model inside it) or, beside it, a single thesis or model in a
 * folder (NewWork). Opens the company's analysis if there is one. */
function AnalysisLink({ saved, companyId, ticker }: { saved: Saved; companyId: string; ticker: string }) {
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const project = list.find((w) => restoreProject(w.project)?.companyId === companyId);
  return project ? (
    <a className="btn primary" href={href({ view: "analysis", projectId: project.workspace_id })}
      title={`Your guided analysis of ${ticker}: business, numbers, earnings, what changed, peers, model, thesis and monitor`}>
      Open analysis
    </a>
  ) : (
    <a className="btn primary" href={href({ view: "analysis", start: companyId })}
      title={`Eight guided steps for ${ticker} (business, numbers, earnings, what changed, peers, model, thesis, monitor), with its thesis and model inside. New thesis and New model make a single item instead.`}>
      Start an analysis
    </a>
  );
}
