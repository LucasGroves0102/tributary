import { ApiError, expectedFilingsOf, screener } from "../api";
import { header, HEADER_COLUMNS, HEADER_COLUMNS_WITH_BANK } from "../company/data";
import { DataNotes } from "../company/DataNotes";
import { EarningsView } from "../company/Earnings";
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
import { placeName } from "../companies/places";
import { Folio } from "../components/Folio";
import { ErrorPanel, Skeleton } from "../components/States";
import { day } from "../format";
import { COMPANY_TABS, go, href, type CompanyTab } from "../route";
import { NewWork } from "../workspaces/NewWork";
import type { Saved } from "../workspaces/useSaved";
import { restoreProject } from "../analysis/project";
import { coRegistrantNote } from "../compare/peers";

interface Props {
  token: string;
  companyId: string;
  tab: CompanyTab;
  /** the Statements tab's choices from the address */
  statements?: StatementView;
  /** the Filings tab opens What changed on this 10-K/10-Q (an alert's link) */
  changes?: string;
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
}

/** The tearsheet: who the company is (public metadata), then its data by tab. Identity, the
 * filing index and expected dates are public; values need financials, text needs filings. */
export function Company({ token, companyId, tab, statements, changes, watched, onWatch, onChart, onChartMany, onOpenChart, onAsk, saved }: Props) {
  const meta = useLoad(companyId, () =>
    // a bank's key stats need its own columns (F4); an older API refuses them (422): the standard set
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS_WITH_BANK, sort: "name", limit: 1 }).catch((e) => {
      if (e instanceof ApiError && e.status === 422) return screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1 });
      throw e;
    }).then(
      (r) => r.rows.find((x) => x.company_id === companyId) ?? null,
    ),
  );
  const expected = useLoad(companyId, () => expectedFilingsOf(token, companyId));
  // the Price tab exists only when the server offers "Connect your data"
  const market = useMarketData(token);
  const tabs = COMPANY_TABS.filter(([t]) => t !== "price" || market?.enabled);

  if (!meta || meta.state === "loading") return <div className="page"><Skeleton lines={6} label="Loading the company" /></div>;
  if (meta.state === "error") {
    return (
      <div className="page">
        <ErrorPanel what="this company" detail={meta.message} onRetry={meta.retry} />
      </div>
    );
  }
  if (meta.state !== "ok" || meta.data === null) {
    return (
      <div className="page">
        <p className="empty">
          This company has no financials or filings here. Find one under{" "}
          <a href={href({ view: "companies" })}>Companies</a>, or search with Ctrl K.
        </p>
      </div>
    );
  }
  const row = meta.data;
  const h = header(row);
  // charts and saved work call it this: its ticker, or its name when it has none
  const ticker = h.tickers[0] ?? h.name;
  const next = expected?.state === "ok" ? (expected.data.expected[0] ?? null) : null;
  const joint = coRegistrantNote(h.name, row.co_registrants ?? [], companyId);
  // the SEC's code for where it is incorporated, in words ("Y9" is Jersey); a code EDGAR doesn't list isn't shown
  const place = placeName(h.state);
  const more = h.tickers.length > 1 ? h.tickers.slice(1) : [];

  return (
    <div className="page company-page">
      <Folio name={row.sector ?? "Company"} line={row.sector ? row.sector_industry : h.industry} lead className="co-head">
        <h1 className="co-name">{h.name}</h1>
        <p className="meta co-meta">
          {h.tickers[0] && (
            <span className="co-listing" title={more.length ? `Also listed: ${more.join(", ")}` : undefined}>
              <b>{h.tickers[0]}</b>
              {more.length > 0 && ` +${more.length} more`}
              {h.exchanges.length > 0 && ` on ${h.exchanges.join(", ")}`}
            </span>
          )}
          {h.industry && <span>{h.industry}{h.sic ? `, SIC ${h.sic}` : ""}</span>}
          {h.fye && <span>Fiscal year ends in {h.fye}</span>}
          {h.filer && <span>{h.filer} filer</span>}
          {place && <span title={`EDGAR's code: ${h.state}`}>Incorporated in {place}</span>}
          {h.currency && <span>Figures in {h.currency}</span>}
          <span>CIK {companyId}</span>
          <span className="tiers" title="What your access covers for this company">
            {row.access.length ? (
              row.access.map((t) => <span key={t} className={`tier ${t}`}>{t}</span>)
            ) : (
              <span className="tier none">metadata only</span>
            )}
          </span>
        </p>
        <p className="meta co-meta">
          {next?.last_filed && <span>Last periodic report filed {day(next.last_filed, true)}</span>}
          {next ? (
            <span title={expected?.state === "ok" ? expected.data.method : undefined}>
              Next {next.form}, for the period ending {day(next.period_end, true)}, expected
              {next.expected_date ? ` around ${day(next.expected_date, true)}` : ""}
              {" "}(an estimate{next.status !== "upcoming" ? `, ${next.status}` : ""})
            </span>
          ) : (
            <span>No expected filing date</span>
          )}
        </p>
        {joint && (
          <p className="meta co-meta joint-note" role="note">
            <span>
              {joint}{" "}
              {(row.co_registrants ?? []).map((c, i) => (
                <span key={c.company_id}>
                  {i > 0 && ", "}
                  <a href={href({ view: "company", companyId: c.company_id })}>Open {c.name}</a>
                </span>
              ))}
            </span>
          </p>
        )}
        <div className="co-acts">
          {saved && row.access.includes("financials") && <AnalysisLink saved={saved} companyId={companyId} ticker={ticker} />}
          {watched !== null && (
            <button className="btn" onClick={() => void onWatch(!watched)} aria-pressed={watched}
              title={row.access.includes("financials") ? undefined : "Watching follows its filings; it grants no access to values"}>
              {watched ? "✓ Watching" : "+ Watch"}
            </button>
          )}
          {row.access.includes("financials") && (
            <button className="btn" onClick={() => onChart()}>Chart</button>
          )}
          <button className="btn" onClick={() => go({ view: "compare", compare: { target: companyId } })}>Compare</button>
          <button className="btn" onClick={() => onAsk(`Summarize ${h.name}'s latest quarter.`)}>Ask</button>
          {saved && <NewWork token={token} saved={saved} company={{ id: companyId, name: ticker }} />}
        </div>
        <nav className="tabs co-tabs" aria-label="Company sections">
          {tabs.map(([t, label]) => (
            <a key={t} href={href({ view: "company", companyId, tab: t })} aria-current={t === tab ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </Folio>

      {tab === "overview" && <Overview token={token} companyId={companyId} name={h.name} row={row} access={row.access} />}
      {tab === "earnings" && (
        <EarningsView token={token} companyId={companyId} name={h.name} canReadFilings={row.access.includes("filings")}
          canReadFinancials={row.access.includes("financials")} />
      )}
      {tab === "statements" && <Statements token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={row.not_applicable} view={statements} />}
      {tab === "revenue" && (
        <Revenue token={token} companyId={companyId} name={h.name} ticker={ticker} saved={saved ?? null} onOpenChart={onOpenChart} />
      )}
      {tab === "segments" && <Segments token={token} companyId={companyId} name={h.name} />}
      {tab === "ratios" && <Ratios token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={row.not_applicable} />}
      {tab === "filings" && <Filings key={changes ?? ""} token={token} companyId={companyId} name={h.name} canRead={row.access.includes("filings")} fiscalYearEnd={row.fiscal_year_end} initialDiff={changes} />}
      {tab === "notes" && <DataNotes token={token} companyId={companyId} name={h.name} canReadFilings={row.access.includes("filings")} />}
      {tab === "peers" && <Peers token={token} companyId={companyId} name={h.name} onChart={onChartMany} />}
      {tab === "price" && <Price token={token} companyId={companyId} name={h.name} />}
    </div>
  );
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
