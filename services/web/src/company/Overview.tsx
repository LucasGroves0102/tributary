import { useState, type ReactNode } from "react";

import { companyEvents, financialsView, historyKept, latestSection, perShare, recap as fetchRecap } from "../api";
import { excerptBlocks } from "../reader/text";
import { Folio, TodaysView } from "../components/Folio";
import { Mark, MarkLegend, type MarkKind } from "../components/Mark";
import { FigureButton, useSourceCard, type SourceInfo } from "../components/SourceCard";
import { onGridKey } from "../components/ledgerGrid";
import { ErrorPanel, sentence, TextShape } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { currencyOf, day, exactAmount, ledgerUnit, paren } from "../format";
import { href } from "../route";
import type { EventsResponse, FinancialRow, ScreenerRow, Tier, ValueSource } from "../types";
import {
  BANK_NOT_SHOWN, epsStat, financeArmNote, foreignReport, header, isBankRow, keyStats, longDay, recapAccession, shortQuarter, statementSection,
  statMark, yearStory,
  type KeyStat, type YearStory,
} from "./data";
import { EventsCard } from "./Events";
import { keyStatFlag, staleFlags, type StaleFlag } from "./events";
import { RecapTable } from "./RecapTable";
import { ResultsPending } from "./ResultsPending";
import { readerAt, rowCard, rowMark, statCard } from "./sources";
import { Gate } from "./Tables";
import { TextKpisCard } from "./TextKpis";
import { type Load, useLoad } from "./useLoad";
import { YourData } from "./YourData";

const EXCERPT_CHARS = 1500;
const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
/** the bars of "Revenue by quarter" (§6: eight) */
const BARS = 8;

/** Where a figure's filing opens: the reader, at its financial statements. */
export function sealHref(companyId: string, src: Pick<ValueSource, "accession_no" | "form">): string | null {
  return CIK.test(companyId) && ACCESSION.test(src.accession_no)
    ? href({ view: "filing", companyId, accessionNo: src.accession_no, section: statementSection(src.form) })
    : null;
}

/** The company's Overview (.interface-design/system.md §6): the lead column has the hero (twelve-month
 * revenue, its change), its footing (the four quarters that add up to it, each opening its own source),
 * the legend and the plain-English sentence; then the rest of the twelve months as a two-column ledger,
 * then the latest quarter against the prior and year-ago quarters. The side column has revenue by
 * quarter (eight bars, derived quarters hatched) and the 8-Ks since the last report. The business, text
 * figures and the user's own data follow. Every figure opens its source card; marks come from the API's
 * sources only. Lead and side stack when the page has less than 900px (a container query). */
export function Overview({
  token,
  companyId,
  name,
  row,
  access,
  asOf,
  events,
  resultsInHeader = false,
}: {
  token: string;
  companyId: string;
  name: string;
  row: ScreenerRow;
  access: Tier[];
  /** a past day (`?asof=`): the year and the key figures as they stood then; the rest is today's */
  asOf?: string;
  /** the 8-Ks since the last report (the page asks once: the header's notice, the flags and the list) */
  events?: Load<EventsResponse | null> | null;
  /** the company header's notice already says results are out: the figures don't say it again */
  resultsInHeader?: boolean;
}) {
  const canFin = access.includes("financials");
  const canRead = access.includes("filings");
  // a 20-F/40-F filer files no 10-Q, 10-K or 8-K: those sections say so instead (foreignReport)
  const foreign = foreignReport(row);
  // the page's request for the 8-Ks, or (rendered alone) its own
  const own = useLoad(events === undefined && canRead && !foreign ? `${companyId}|events` : null, () => companyEvents(token, companyId));
  if (events === undefined) events = own;
  // today's 8-Ks flag today's figures; as of a past day they say nothing about that day's
  const flags = asOf ? [] : staleFlags(events?.state === "ok" ? events.data : null);
  // as of a past day the key figures read gold's history of itself (086), which its catch-up builds
  // company by company: without it they'd all read "not reported" (Riley, test 10, finding 7)
  const history = useLoad(asOf && canFin ? `${companyId}|history|${asOf}` : null, () => historyKept(token, companyId, asOf));
  const notKept = !!asOf && history?.state === "ok" && history.data === false;
  // EPS for the twelve months like the other flows (finding 1): /per-share's TTM, or a year for a
  // company that reports once a year
  const settled = !asOf || (!!history && history.state !== "loading");
  const eps = useLoad(canFin && settled && !notKept ? `${companyId}|eps|${asOf ?? ""}` : null, async () => {
    const ttm = await perShare(token, companyId, "ttm", "fiscal", false, 1, asOf);
    if (ttm === null) return null;
    if (ttm.rows.some((r) => r.metric === "eps_diluted") || !foreign) return ttm.rows;
    return (await perShare(token, companyId, "annual", "fiscal", false, 1, asOf))?.rows ?? [];
  });
  const epsRows = !eps || eps.state === "loading" ? undefined : eps.state === "ok" ? eps.data : null;
  // today's quarters (the bars: a section of today's data, like the rest past the key figures); as of a past
  // day the footing reads its own quarters, as they stood then; the twelve months' own quarters (the footing)
  const quarters = useLoad(canFin ? `${companyId}|quarters` : null,
    () => financialsView(token, companyId, BARS, ["revenue"], "quarter").then((f) => f.rows));
  const then = useLoad(canFin && asOf && !notKept ? `${companyId}|quarters|${asOf}` : null,
    () => financialsView(token, companyId, BARS, ["revenue"], "quarter", "fiscal", false, false, asOf).then((f) => f.rows));
  const ttm = useLoad(canFin && !foreign && !notKept ? `${companyId}|ttm|${asOf ?? ""}` : null,
    () => financialsView(token, companyId, 1, ["revenue"], "ttm", "fiscal", false, false, asOf).then((f) => f.rows.find((r) => r.metric === "revenue") ?? null));
  // no 8-K since the last report says results are out (or none read yet): the earnings answer may
  // still have a release with no 10-Q or 10-K, said under the figures (once the 8-Ks are settled)
  const pending = !asOf && canRead && !foreign && !resultsInHeader && !!events && events.state !== "loading"
    ? <ResultsPending token={token} companyId={companyId} fiscalYearEnd={row.fiscal_year_end} /> : null;
  // a footing term outlines its bar (§4 Chart): the quarter in focus or under the pointer
  const [hl, setHl] = useState<string | null>(null);
  const stats = canFin && !notKept ? keyStats(row).map((s) => (s.key === "eps_diluted_q" ? epsStat(epsRows, s, asOf) : s))
    .map((s) => ({ ...s, flag: keyStatFlag(s.key, s.periodEnd, flags) })) : [];
  const quarterRows = quarters?.state === "ok" ? quarters.data.filter((r) => r.metric === "revenue") : null;
  const footingRows = asOf ? (then?.state === "ok" ? then.data.filter((r) => r.metric === "revenue") : null) : quarterRows;
  return (
    <div className="ov">
      <div className="ov-lead">
        {canFin && !notKept && (
          <Lead row={row} name={name} companyId={companyId} stats={stats} ttm={ttm} quarters={footingRows} hl={hl} onHl={setHl} />
        )}
        <Figures companyId={companyId} name={name} row={row} canFin={canFin} stats={stats} asOf={asOf} notKept={notKept} pending={pending} />
        <TodaysView.Provider value={asOf ?? null}>
          <Recap token={token} companyId={companyId} name={name} accession={canFin ? recapAccession(row) : null} canFin={canFin} foreign={foreign}
            asOf={notKept ? asOf : undefined} />
        </TodaysView.Provider>
      </div>
      <div className="ov-side">
        <TodaysView.Provider value={asOf ?? null}>
          {canFin && <RevenueBars companyId={companyId} name={name} load={quarters} rows={quarterRows} hl={asOf ? null : hl} foreign={foreign} asOf={asOf} />}
          {foreign ? (
            <Folio name="Since the last report" line="Files 6-Ks, not 8-Ks" className="events-card">
              <p className="empty foreign-note">
                {name} files as a foreign private issuer: current reports on Form 6-K, not 8-Ks, so there are no 8-K items here to say
                what may have changed since its last report. Its 6-Ks are on the <a href={href({ view: "company", companyId, tab: "filings" })}>Filings</a> tab.
              </p>
            </Folio>
          ) : canRead && <EventsCard token={token} companyId={companyId} name={name} load={events} folio />}
        </TodaysView.Provider>
      </div>
      <div className="ov-more">
        <TodaysView.Provider value={asOf ?? null}>
          <Business token={token} companyId={companyId} name={name} canRead={canRead} foreign={foreign} />
          <TextKpisCard token={token} companyId={companyId} name={name} canRead={canRead} folio />
          <YourData token={token} companyId={companyId} name={name} />
        </TodaysView.Provider>
      </div>
    </div>
  );
}

type Stat = KeyStat & { flag: StaleFlag | null };

/** The hero (§6): twelve-month revenue at 48px with its mark and its change, then its footing, the
 * legend, and the year in plain English. A company that reports once a year leads with its year. */
function Lead({ row, name, companyId, stats, ttm, quarters, hl, onHl }: {
  row: ScreenerRow;
  name: string;
  companyId: string;
  stats: Stat[];
  ttm: Load<FinancialRow | null> | null;
  quarters: FinancialRow[] | null;
  hl: string | null;
  onHl: (end: string | null) => void;
}) {
  const rev = stats.find((s) => s.key === "revenue_ttm");
  const growth = stats.find((s) => s.key === "revenue_growth_ttm");
  const story = yearStory(row);
  // another currency's figure is shown in it (its value stays out of sorts and medians, never out of sight)
  if (!rev || (rev.value === null && rev.status !== "other_currency") || !rev.source) return null;
  const mark = statMark(rev);
  const src = rev.source;
  const currency = header(row).currency ?? "USD";
  const annual = mark === "ok";
  const twelve = ttm?.state === "ok" ? ttm.data : null;
  const footing = mark === "sum" && twelve && Array.isArray(twelve.inputs?.quarters) && (twelve.inputs!.quarters as unknown[]).length > 1 ? twelve : null;
  const loadingFooting = mark === "sum" && (!ttm || ttm.state === "loading" || !quarters);
  const termMarks = footing && quarters ? footingTerms(footing, quarters).map((t) => t.mark).filter((m): m is MarkKind => !!m) : [];
  const g = growth && growth.value !== null && (!growth.status || growth.status === "other_currency") ? growth : null;
  return (
    <section className="ov-hero" aria-label={`${rev.label}, ${annual ? "the year" : "twelve months"}`}>
      <p className="ov-label">
        {rev.label} · {annual ? "the year" : "the twelve months"} to {day(src.period_end, true)} · {currency}
      </p>
      <div className="hero">
        <FigureButton text={rev.text} mark={mark} hero info={() => statCard(rev, companyId)} />
        {g && (
          <span className="hero-chg">
            <span className={g.value! >= 0 ? "chg gain" : "chg loss"}>{minus(g.text)}</span>
            <span className="pencil"> on the {annual ? "year" : "twelve months"} before</span>
          </span>
        )}
      </div>
      {footing && quarters ? (
        <Footing ttm={footing} quarters={quarters} companyId={companyId} label={rev.label} total={rev.text} hl={hl} onHl={onHl} />
      ) : loadingFooting ? (
        <div className="footing" data-loading="" aria-busy="true">
          {[0, 1, 2, 3, 4].map((i) => (
            <span key={i} className="term-blank"><span className="blank-v w-48" /><small>&nbsp;</small></span>
          ))}
        </div>
      ) : null}
      <MarkLegend kinds={[...(mark ? [mark] : []), ...termMarks]} />
      {story && <YearSentence story={story} name={name} />}
    </section>
  );
}

/** "-4.0%" with a true minus. */
const minus = (s: string) => s.replace(/^-/, "−");

interface Term {
  end: string;
  value: number;
  text: string;
  label: string;
  mark: MarkKind | null;
  info: () => SourceInfo;
}

/** The twelve months' quarters as the API gives them (the TTM row's `inputs.quarters`), each with its
 * own filing from /financials: ✓ when that quarter is reported at the same value, d when derived. */
function footingTerms(ttm: FinancialRow, quarters: FinancialRow[]): Term[] {
  const qs = (ttm.inputs?.quarters ?? []) as { period_end?: string; value?: number | string; accession_no?: string }[];
  const byEnd = new Map(quarters.map((r) => [r.period_end, r]));
  const unit = ledgerUnit([Number(ttm.value)], currencyOf(ttm.unit));
  return qs.flatMap((q) => {
    if (!q.period_end || q.value === undefined) return [];
    const value = Number(q.value);
    const r = byEnd.get(q.period_end);
    const same = r && Number(r.value) === value;
    return [{
      end: q.period_end,
      value,
      text: unit.text(value),
      label: shortQuarter(r?.fiscal_period) ?? day(q.period_end),
      mark: same ? rowMark(r!) : null,
      info: () => (same ? rowCard(r!, "Revenue", "") : {
        title: `Revenue · three months to ${day(q.period_end!, true)}`,
        value: exactAmount(value, ttm.unit),
        mark: null,
        where: q.accession_no ? [q.accession_no] : [],
      }),
    }];
  });
}

/** The footing (§5.3): the hero's arithmetic as one ledger line between hairlines, quarter labels under
 * each term and the unit under the total. Each term opens its own card and outlines its bar. The total
 * is the API's twelve months, never re-added here. */
function Footing({ ttm, quarters, companyId, label, total, hl, onHl }: {
  ttm: FinancialRow;
  quarters: FinancialRow[];
  companyId: string;
  label: string;
  total: string;
  hl: string | null;
  onHl: (end: string | null) => void;
}) {
  const unit = ledgerUnit([Number(ttm.value)], currencyOf(ttm.unit));
  const terms = footingTerms(ttm, quarters).map((t) => ({
    ...t,
    info: () => {
      const r = quarters.find((x) => x.period_end === t.end && Number(x.value) === t.value);
      return r ? rowCard(r, label, companyId) : t.info();
    },
  }));
  const sum: SourceInfo = {
    title: `${label} · twelve months to ${day(ttm.period_end, true)}`,
    value: exactAmount(Number(ttm.value), ttm.unit),
    mark: "sum",
    how: `Added up from ${terms.length} quarters: ${terms.map((t) => t.text).join(" + ")} (${unit.unit}), each marked on this line.`,
    where: [`To the ${ttm.citation.form} filed ${day(ttm.citation.filed, true)}`],
    reader: readerAt(companyId, ttm.citation),
  };
  return (
    <div className="footing landed" role="group" aria-label={`How ${total} adds up, ${unit.unit}`} onMouseLeave={() => onHl(null)}>
      {terms.map((t, i) => (
        <FootingTerm key={t.end} text={t.text} label={t.label} mark={t.mark} info={t.info} on={hl === t.end}
          onHl={(on) => onHl(on ? t.end : null)} after={i < terms.length - 1 ? "+" : "="} />
      ))}
      <FootingTerm text={unit.text(Number(ttm.value))} label={unit.unit} mark="sum" info={() => sum} total />
    </div>
  );
}

function FootingTerm({ text, label, mark, info, total = false, on = false, onHl, after }: {
  text: string;
  label: string;
  mark: MarkKind | null;
  info: () => SourceInfo;
  total?: boolean;
  on?: boolean;
  onHl?: (on: boolean) => void;
  after?: string;
}) {
  const { props, card } = useSourceCard(info);
  return (
    <>
      <button type="button" className={["term", mark === "d" && "derived", total && "tot", on && "on"].filter(Boolean).join(" ")} {...props}
        onMouseEnter={(e) => {
          onHl?.(true);
          props.onMouseEnter?.(e);
        }}
        onFocus={() => onHl?.(true)} onBlur={() => onHl?.(false)}>
        <span className="tv">
          <span className="v">{text}</span>
          {mark && <Mark kind={mark} />}
        </span>
        <small>{label}</small>
      </button>
      {after && <span className="op" aria-hidden="true">{after}</span>}
      {card}
    </>
  );
}

/** "In the twelve months to July 26, 2026, Nvidia reported revenue of $302.97 billion, up 83.4% on the
 * year before, and net income of $192.88 billion. It ended the period with $66.00 billion more in cash
 * and short-term investments than debt." The hero above carries the figure's mark. */
function YearSentence({ story, name }: { story: YearStory; name: string }) {
  return (
    <p className="story">
      In the twelve months to {longDay(story.end)}, {name} reported {story.revenueLabel} of <b>{story.revenue}</b>
      {story.growth && (
        story.growth.text === "flat"
          ? <>, about the same as the year before</>
          : <>, {story.growth.text} on the year before</>
      )}
      {story.netIncome && (
        story.netIncome.loss ? <>, and a net loss of <b>{story.netIncome.text}</b></> : <>, and net income of <b>{story.netIncome.text}</b></>
      )}
      .
      {story.balance && (
        story.balance.netCash
          ? <> It ended the period with <b>{story.balance.text}</b> more in cash and short-term investments than debt.</>
          : <> It ended the period with net debt of <b>{story.balance.text}</b>.</>
      )}
    </p>
  );
}

/** A reading letter for a figure that isn't a plain one (§4 Readings): the dash and an 11px letter,
 * explained on the line under the ledger. Words the API's readings (blanks.ts) only. */
const READINGS: Record<string, string> = {
  a: "not reported",
  b: "none filed since a date (the figure's card says when)",
  k: "a bank: the line doesn't describe one",
};

function readingOf(s: Stat, bank: boolean): string | null {
  if (s.value !== null || s.status === "not_meaningful" || s.status === "other_currency") return null;
  if (s.old) return "b";
  if (s.status === "not_applicable") return bank ? "k" : "a";
  return "a";
}

/** The rest of the twelve months (§6): the key figures but revenue as a two-column ledger, amounts then
 * rates, each figure a button that opens its card; a figure from another period says when. */
function Figures({ companyId, name, row, canFin, stats, asOf, notKept = false, pending = null }: {
  companyId: string;
  name: string;
  row: ScreenerRow;
  canFin: boolean;
  stats: Stat[];
  asOf?: string;
  /** as of a past day, no history of the company is kept yet (086): nothing to read from */
  notKept?: boolean;
  /** results announced before their 10-Q or 10-K (ResultsPending): why the figures end where they do */
  pending?: ReactNode;
}) {
  const currency = header(row).currency;
  // Ford's and GM's debt includes their finance arms' (migration 055, consolidated as built)
  const armNote = financeArmNote(companyId, name);
  if (!canFin) {
    return (
      <Folio name="Figures" line="Key figures from the filings">
        <p className="empty locked">Financials for {name} aren't included in your access; its filings and metadata are public.</p>
      </Folio>
    );
  }
  if (notKept && asOf) {
    const at = (tab: "statements" | "ratios") => href({ view: "company", companyId, tab, asOf });
    return (
      <Folio name="Figures" line={`As they stood on ${day(asOf, true)}`}>
        <p className="empty asof-not-kept" role="note">
          {name}'s figures as they stood on {day(asOf, true)} aren't kept here yet: Tributary is building each company's history of
          its figures, a few thousand companies a day, and {name}'s isn't built. Until it is, <a href={at("statements")}>Statements</a>{" "}
          and <a href={at("ratios")}>Ratios</a> work them out for that day from the filings filed by then (it takes a little longer).
        </p>
      </Folio>
    );
  }
  const bank = isBankRow(row);
  const rest = stats.filter((s) => s.key !== "revenue_ttm" && s.key !== "revenue_growth_ttm");
  // the period most of them share goes in the section's line; the others say their own
  const ends = rest.map((s) => s.periodEnd).filter((e): e is string => !!e);
  const common = ends.sort((a, b) => ends.filter((x) => x === b).length - ends.filter((x) => x === a).length)[0] ?? null;
  const flagged = [...new Map(rest.filter((s) => s.flag).map((s) => [s.flag!.short, s.flag!])).values()];
  const letters = [...new Set(rest.map((s) => readingOf(s, bank)).filter((l): l is string => !!l))];
  const marks = [...new Set(rest.map((s) => statMark(s)).filter((m): m is MarkKind => !!m))];
  const unmarked = rest.some((s) => (s.value !== null || s.status === "other_currency") && !statMark(s));
  const amounts = rest.filter((s) => s.kind === "usd" || s.kind === "eps");
  const rates = rest.filter((s) => !(s.kind === "usd" || s.kind === "eps"));
  return (
    <Folio name="The rest of the twelve months" className="ov-rest"
      line={common ? `As filed, to ${day(common, true)}` : "As filed"} aside={currency ?? "USD"}>
      <div className="ledger">
        <dl aria-label="Amounts">{amounts.map((s) => <LedgerRow key={s.key} s={s} common={common} companyId={companyId} armNote={armNote} bank={bank} />)}</dl>
        <dl aria-label="Rates">{rates.map((s) => <LedgerRow key={s.key} s={s} common={common} companyId={companyId} armNote={armNote} bank={bank} />)}</dl>
      </div>
      <MarkLegend kinds={marks} extra={[
        ...letters.map((l) => `${l} ${READINGS[l]}`),
        ...(unmarked ? ["Unmarked: a ratio or a difference of marked figures; select one for how"] : []),
      ]} />
      <div className="fnote">
        {pending}
        {flagged.map((f) => (
          <p key={f.short} role="note">
            <span className="mark" aria-hidden="true">*</span>
            <span>{f.asOf ? `At ${day(f.asOf, true)}, before` : "Before"} {f.short}, which may have changed it.</span>
          </p>
        ))}
        {bank && "net_interest_income_ttm" in row.values && <p className="bank-note">A bank's own figures. {BANK_NOT_SHOWN}</p>}
        {armNote && <p className="debt-note" role="note">{armNote}</p>}
      </div>
    </Folio>
  );
}

function LedgerRow({ s, common, companyId, armNote, bank }: { s: Stat; common: string | null; companyId: string; armNote: string | null; bank: boolean }) {
  const letter = readingOf(s, bank);
  const differs = s.old || (s.periodEnd && common && s.periodEnd !== common) || letter;
  const extra = [s.key === "net_debt_q" ? armNote : null, s.flag?.text].filter(Boolean).join("\n\n") || null;
  const mark = statMark(s);
  return (
    <div data-key={s.key} className={s.flag ? "later" : undefined}>
      <dt>
        {s.label}
        {differs && <span className="when">{s.period}</span>}
      </dt>
      <dd className={[letter && "blank", s.old && "old", s.flag && "later"].filter(Boolean).join(" ") || undefined}>
        {letter ? (
          <>
            <span className="figure">—</span>
            <sup className="rdg" title={READINGS[letter]}>{letter}</sup>
          </>
        ) : s.value === null && s.status !== "other_currency" ? (
          <span className="figure">{s.text}</span>
        ) : (
          <FigureButton text={s.kind === "usd" || s.kind === "eps" ? paren(s.text) : s.text} mark={mark}
            info={() => statCard(s, companyId, extra)}>
            {s.flag && <span className="flag-mark" aria-hidden="true" />}
          </FigureButton>
        )}
      </dd>
    </div>
  );
}

function Recap({ token, companyId, name, accession, canFin, foreign, asOf }: {
  token: string; companyId: string; name: string; accession: string | null; canFin: boolean;
  /** foreignReport: a 20-F/40-F filer, which files no 10-Q or 10-K */
  foreign: string | null;
  /** as of this past day with no history kept yet: which report was the latest then isn't known here */
  asOf?: string;
}) {
  const load = useLoad(accession, () => fetchRecap(token, accession!));
  const data = load?.state === "ok" ? load.data : null;
  return (
    <Folio name="Latest quarter" line={data ? `${data.form} filed ${day(data.filed, true)}` : foreign ? `Annual reports on Form ${foreign}` : "The latest 10-Q or 10-K"}
      aside={foreign ? undefined : <a href={href({ view: "company", companyId, tab: "statements" })}>Statements →</a>}>
      {!canFin ? (
        <p className="empty locked">The recap needs financials for {name}.</p>
      ) : !accession && asOf ? (
        <p className="empty">
          Which 10-Q or 10-K was {name}'s latest on {day(asOf, true)} is read from its history of figures, which isn't built yet.
          Its Filings tab lists every report with its date.
        </p>
      ) : !accession && foreign ? (
        <p className="empty foreign-note">
          {name} files as a foreign private issuer: annual reports on Form {foreign} and interim reports on Form 6-K, not 10-Qs or
          10-Ks, so there is no quarterly recap here. The figures above come from those reports, in their own currency; the Filings
          tab lists them.
        </p>
      ) : !accession ? (
        <p className="empty">No 10-Q or 10-K values for {name} yet. Its Filings tab lists what it has filed.</p>
      ) : (
        <Gate load={load} name={name} what="Financials" shape={<RecapShape />}>
          {() => (
            <>
              <RecapTable data={data!} label="Latest quarter" companyId={companyId} />
              {data!.flags && data!.flags.length > 0 && (
                <div className="recap-flags">
                  <b>Flagged in the filing text</b>
                  <ul className="flags">
                    {data!.flags.map((f, i) => (
                      <li key={i}>
                        {f.title ?? f.flag}: “{f.snippet}”{" "}
                        <a href={href({ view: "filing", companyId, accessionNo: f.citation.accession_no, section: f.citation.section_key })}>
                          {f.section_key}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="foot">
                {data!.flags_unavailable ? `${sentence(data!.flags_unavailable)} ` : ""}
                Each column is the figures as its own filing printed them; a restated value cites the restating filing. Margins
                change in basis points; a change in a balance is uncoloured.
              </p>
            </>
          )}
        </Gate>
      )}
    </Folio>
  );
}

/** The latest quarter's shape while it loads: its lines and columns, the figures blank. */
function RecapShape() {
  return (
    <div className="tw-skel">
    <table className="t led recap" aria-hidden="true">
      <thead>
        <tr><th className="first">Latest quarter</th>{[0, 1, 2, 3, 4].map((i) => <th key={i}><span className="blank-v w-48" /></th>)}</tr>
      </thead>
      <tbody>
        {["Revenue", "Gross profit", "Operating income", "Net income", "EPS (diluted)"].map((l) => (
          <tr key={l}><td className="first">{l}</td>{[0, 1, 2, 3, 4].map((i) => <td key={i} className="n"><span className="blank-v w-48" /></td>)}</tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

/** The first part of the latest 10-K's Item 1, cut at a sentence end. */
export function excerpt(text: string, max = 900): string {
  const t = text.replace(/\s+\n/g, "\n").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const end = cut.lastIndexOf(". ");
  return (end > max / 2 ? cut.slice(0, end + 1) : cut) + " …";
}

function Business({ token, companyId, name, canRead, foreign }: { token: string; companyId: string; name: string; canRead: boolean; foreign: string | null }) {
  const load = useLoad(canRead && !foreign ? companyId : null, () => latestSection(token, companyId, "1", "10-K", EXCERPT_CHARS));
  const section = load?.state === "ok" ? load.data : null;
  return (
    <Folio name="Business" line={section ? `10-K filed ${day(section.citation.filed, true)}` : foreign ? `The latest ${foreign}` : "The latest 10-K"}
      aside={foreign ? "Its business description" : "Item 1, the opening"}>
      {foreign ? (
        <p className="empty foreign-note">
          {name} describes its business in its {foreign} annual report. Tributary reads the text of 10-Ks, 10-Qs and 8-Ks only, so
          there is no excerpt here; the report is on the Filings tab, with its link to EDGAR.
        </p>
      ) : !canRead ? (
        <p className="empty locked">The description comes from the 10-K's text, which needs the pro plan and a grant for {name}.</p>
      ) : (
        <Gate load={load} name={name} what="Filings" shape={<TextShape />}>
          {() =>
            section ? (
              <div className="business">
                {/* the filing's own words, in the serif (§1: serif means the filing wrote it) */}
                {excerptBlocks(excerpt(section.text)).map((b, i) => (b.heading
                  ? <h3 key={i} className="excerpt-h">{b.text}</h3>
                  : <p key={i} className="excerpt">{b.text}</p>))}
                <a href={href({ view: "filing", companyId, accessionNo: section.citation.accession_no, section: section.section_key })}>
                  Read Item 1
                </a>
              </div>
            ) : (
              <p className="empty">No parsed 10-K Item 1 for {name} yet. Its latest 10-K is on the Filings tab.</p>
            )
          }
        </Gate>
      )}
    </Folio>
  );
}

/** Revenue by quarter (§4 Chart, §6): eight bars, the latest in ink, history a light fill with a 3:1
 * edge, a derived quarter hatched; hairlines at round values; the first and last bars labelled. Bars
 * appear at their value (no growing from zero). "View as table" lists the same figures, each opening
 * its card. A footing term outlines its bar. */
function RevenueBars({ companyId, name, load, rows, hl, foreign, asOf }: {
  companyId: string;
  name: string;
  load: Load<FinancialRow[]> | null;
  rows: FinancialRow[] | null;
  hl: string | null;
  foreign: string | null;
  asOf?: string;
}) {
  const [table, setTable] = useState(false);
  const bars = (rows ?? []).filter((r) => Number.isFinite(Number(r.value))).sort((a, b) => a.period_end.localeCompare(b.period_end)).slice(-BARS);
  const values = bars.map((r) => Number(r.value));
  const unit = ledgerUnit(values, currencyOf(bars[0]?.unit));
  const top = Math.max(0, ...values);
  const step = niceStep(top / 4);
  const ceiling = step ? Math.ceil(top / step) * step : 1;
  const lines = step ? Array.from({ length: Math.round(ceiling / step) }, (_, i) => (i + 1) * step) : [];
  const toStatements = href({ view: "company", companyId, tab: "statements", ...(asOf ? { asOf } : {}) });
  return (
    <Folio name="Revenue by quarter" className="ov-bars"
      line={bars.length ? `${unit.unit} · fiscal quarters` : "Fiscal quarters"}
      aside={bars.length ? (
        <button type="button" className="link view-table" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "View as chart" : "View as table"}
        </button>
      ) : undefined}>
      {!load || load.state === "loading" ? (
        <div className="qbars-blank" data-loading="" aria-busy="true"><span className="visually-hidden">Loading revenue</span></div>
      ) : load.state === "error" ? (
        <ErrorPanel what="revenue" detail={load.message} onRetry={load.retry} />
      ) : !bars.length ? (
        <p className="empty">
          {/* a once-a-year filer has no quarters to chart: said, not blank (Riley, test 10, finding 28) */}
          {foreign
            ? `${name} reports once a year (Form ${foreign}), so there is no quarterly revenue to chart. Statements shows its years.`
            : `No revenue reported: ${name}'s filings state no revenue figure yet, or none could be read. Statements shows what it does report.`}
        </p>
      ) : table ? (
        <TableScroll label="Revenue by quarter">
        <table className="led ov-bars-t landed" onKeyDown={onGridKey}>
          <thead><tr><th className="first">Quarter</th><th>{unit.unit}</th><th>Source</th></tr></thead>
          <tbody>
            {bars.map((r, i) => <BarRow key={r.period_end} r={r} text={unit.text(Number(r.value))} companyId={companyId} first={i === bars.length - 1} />)}
          </tbody>
        </table>
        </TableScroll>
      ) : (
        <div className="qbars landed" role="img"
          aria-label={`Revenue by quarter, ${unit.unit}: ${bars.map((r) => `${shortQuarter(r.fiscal_period) ?? day(r.period_end)} ${unit.text(Number(r.value))}${r.value_source === "derived" ? " derived" : ""}`).join(", ")}`}>
          <div className="qbars-plot">
            {lines.map((v) => (
              <span key={v} className="qbars-gl" data-at={v} ref={(el) => el?.style.setProperty("bottom", `${(v / ceiling) * 100}%`)}>
                <span>{unit.text(v).replace(/\.00$/, "")}</span>
              </span>
            ))}
            {bars.map((r, i) => (
              <span key={r.period_end}
                className={["qbar", r.value_source === "derived" && "dv", i === bars.length - 1 && "last", hl === r.period_end && "hl"].filter(Boolean).join(" ")}
                ref={(el) => el?.style.setProperty("height", `${Math.max(0, Number(r.value) / ceiling) * 100}%`)}>
                {(i === 0 || i === bars.length - 1) && <i>{unit.text(Number(r.value))}</i>}
              </span>
            ))}
          </div>
          <div className="qbars-x" aria-hidden="true">
            {bars.map((r) => {
              const [q, fy] = (shortQuarter(r.fiscal_period) ?? day(r.period_end)).split(" ");
              return <span key={r.period_end}>{q}{fy && <><br />{fy}</>}</span>;
            })}
          </div>
          <p className="qbars-key" aria-hidden="true">
            <span><i className="sw l" />Latest {bars.at(-1)?.citation.form}</span>
            <span><i className="sw" />As filed</span>
            {bars.some((r) => r.value_source === "derived") && <span><i className="sw dv" />Fiscal Q4, derived from the 10-K</span>}
          </p>
        </div>
      )}
      {/* the chart is today's (said so), but its way to the statements keeps the page's day (Morgan 10) */}
      <p className="qbars-more"><a href={toStatements}>The statements</a></p>
    </Folio>
  );
}

function BarRow({ r, text, companyId, first }: { r: FinancialRow; text: string; companyId: string; first: boolean }) {
  const { props, card } = useSourceCard(() => rowCard(r, "Revenue", companyId), { hover: false });
  return (
    <tr>
      <td className="first">{shortQuarter(r.fiscal_period) ?? day(r.period_end)}</td>
      <td className={r.value_source === "derived" ? "derived" : undefined} tabIndex={first ? 0 : -1} data-cell="" {...props}>
        <span className="v">{text}</span>
        {card}
      </td>
      <td className="code">{r.citation.form}{r.value_source === "derived" ? " d" : ""}</td>
    </tr>
  );
}

/** A round gridline step for a chart's axis (1, 2 or 5 times a power of ten): drawing only. */
function niceStep(rough: number): number {
  if (!(rough > 0)) return 0;
  const p = 10 ** Math.floor(Math.log10(rough));
  const f = rough / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
