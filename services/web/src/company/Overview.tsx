import { useContext, useRef, type ReactNode } from "react";

import { companyEvents, financials, historyKept, latestSection, perShare, recap as fetchRecap } from "../api";
import { LayerChart } from "../chart/LayerChart";
import { excerptBlocks } from "../reader/text";
import { financialSeries } from "../chart/series";
import { Folio, TodaysView } from "../components/Folio";
import { Seal, SealLink } from "../components/Seal";
import { ErrorPanel, sentence, Skeleton } from "../components/States";
import { day, paren } from "../format";
import { go, href } from "../route";
import type { FilingEvent, FinancialRow, ScreenerRow, Tier, ValueSource } from "../types";
import {
  BANK_NOT_SHOWN, epsStat, financeArmNote, foreignReport, header, howFiled, isAmount, isBankRow, keyStats, longDay, recapAccession, statementSection,
  statFiled, yearStory,
  type KeyStat, type YearStory,
} from "./data";
import { EventsCard } from "./Events";
import { keyStatFlag, resultsOut, staleFlags, type StaleFlag } from "./events";
import { RecapTable } from "./RecapTable";
import { ResultsPending } from "./ResultsPending";
import { Gate } from "./Tables";
import { TextKpisCard } from "./TextKpis";
import { useLoad } from "./useLoad";
import { YourData } from "./YourData";

const EXCERPT_CHARS = 1500;
const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** Where a figure's seal goes: the filing in the reader, at its financial statements. */
export function sealHref(companyId: string, src: Pick<ValueSource, "accession_no" | "form">): string | null {
  return CIK.test(companyId) && ACCESSION.test(src.accession_no)
    ? href({ view: "filing", companyId, accessionNo: src.accession_no, section: statementSection(src.form) })
    : null;
}

/** The company's Overview, a Seal page: the year as a sentence (sealed to its filing), the key figures
 * as a ledger (each sealed; a balance a later 8-K may have changed is red with *, never recomputed),
 * the 8-Ks since the last report as red-letter days, the latest quarter against the prior and year-ago
 * quarters, the business (10-K Item 1), quarterly revenue, and backlog and orders as the filings state
 * them in words (F8b). Each section's rubric is in the margin. */
export function Overview({
  token,
  companyId,
  name,
  row,
  access,
  asOf,
}: {
  token: string;
  companyId: string;
  name: string;
  row: ScreenerRow;
  access: Tier[];
  /** a past day (`?asof=`): the year and the key figures as they stood then; the rest is today's */
  asOf?: string;
}) {
  const canFin = access.includes("financials");
  const canRead = access.includes("filings");
  // a 20-F/40-F filer files no 10-Q, 10-K or 8-K: those sections say so instead (foreignReport)
  const foreign = foreignReport(row);
  const events = useLoad(canRead && !foreign ? `${companyId}|events` : null, () => companyEvents(token, companyId));
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
  const results = asOf ? null : resultsOut(events?.state === "ok" ? events.data : null);
  // no 8-K since the last report says results are out (or none read yet): the earnings answer may
  // still have a release with no 10-Q or 10-K, said under the figures (once the 8-Ks are settled)
  const pending = !asOf && !results && canRead && !foreign && !!events && events.state !== "loading"
    ? <ResultsPending token={token} companyId={companyId} fiscalYearEnd={row.fiscal_year_end} slot /> : null;
  const story = canFin && !notKept ? yearStory(row) : null;
  return (
    <div className="folios">
      {results && <ResultsOut companyId={companyId} name={name} event={results} lead={!!results} />}
      {story && (
        <Folio name="The year" lead={!results}
          line={howFiled("revenue_ttm", story.source).filed ? `The year to ${day(story.end, true)}, as filed` : `Four quarters to ${day(story.end, true)}`}>
          <YearSentence story={story} name={name} companyId={companyId} />
        </Folio>
      )}
      <Figures companyId={companyId} name={name} row={row} canFin={canFin} flags={flags} lead={!story && !results} eps={epsRows}
        asOf={asOf} notKept={notKept} pending={pending} />
      <TodaysView.Provider value={asOf ?? null}>
      {foreign ? (
        <Folio name="Since the last report" line="Current reports on Form 6-K" className="events-card">
          <p className="empty foreign-note">
            {name} files as a foreign private issuer: current reports on Form 6-K, not 8-Ks, so there are no 8-K items here to say
            what may have changed since its last report. Its 6-Ks are on the Filings tab.
          </p>
        </Folio>
      ) : canRead && <EventsCard token={token} companyId={companyId} name={name} load={events} folio />}
      <Recap token={token} companyId={companyId} name={name} accession={canFin ? recapAccession(row) : null} canFin={canFin} foreign={foreign}
        asOf={notKept ? asOf : undefined} />
      <Business token={token} companyId={companyId} name={name} canRead={canRead} foreign={foreign} />
      {canFin && <Revenue token={token} companyId={companyId} name={name} foreign={foreign} />}
      <TextKpisCard token={token} companyId={companyId} name={name} canRead={canRead} folio />
      <YourData token={token} companyId={companyId} name={name} />
      </TodaysView.Provider>
    </div>
  );
}

/** Results day (Riley, test 10, finding 21): an 8-K with results (item 2.02) after the latest
 * 10-Q or 10-K, said at the top with the release, and that the figures below are the last report's
 * until the next 10-Q or 10-K's arrive (the release's own tables are on Earnings). */
function ResultsOut({ companyId, name, event, lead }: { companyId: string; name: string; event: FilingEvent; lead: boolean }) {
  const read = href({ view: "filing", companyId, accessionNo: event.accession_no, section: event.release?.section_key ?? null });
  return (
    <Folio name="Results out" line={`8-K filed ${day(event.filed, true)}`} lead={lead} className="results-out">
      <p className="story results-line" role="status">
        {name} released its results on {longDay(event.filed)}.{" "}
        <a href={read}>{event.release ? "Read the press release" : "Read the 8-K"}</a>, or its figures on{" "}
        <a href={href({ view: "company", companyId, tab: "earnings" })}>Earnings</a>.
      </p>
      <p className="foot">
        The figures below are from the last 10-Q or 10-K until the report for this quarter is filed and its XBRL figures arrive;
        the release's own tables are on Earnings.
      </p>
    </Folio>
  );
}

/** "In the twelve months to July 26, 2026, Nvidia reported revenue of $302.97 billion, up 83.4% on
 * the year before, and net income of $192.88 billion. It ended the period with $66.00 billion more in
 * cash and short-term investments than debt." Then the seal: the filing it is taken from. */
function YearSentence({ story, name, companyId }: { story: YearStory; name: string; companyId: string }) {
  const src = story.source;
  // sealed only when the twelve months are the annual report's own year; four quarters added up are not
  // a figure the filing states (Riley 32), so then the sentence carries no seal
  const filed = howFiled("revenue_ttm", src).filed;
  const link = filed ? sealHref(companyId, src) : null;
  const taken = `Taken from the ${src.form} filed ${day(src.filed, true)}`;
  return (
    <p className="story">
      In the twelve months to {longDay(story.end)}, {name} reported {story.revenueLabel} of <b>{story.revenue}</b>
      {story.growth && (
        story.growth.text === "flat"
          ? <>, about the same as the year before</>
          : <>, {story.growth.rise ? <span className="rise">{story.growth.text}</span> : story.growth.text} on the year before</>
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
      {link ? (
        <SealLink size="lead" href={link} label={`${taken}: open it`} source={`${taken}, ${src.accession_no}. Revenue for the twelve months to ${day(story.end, true)}.`} />
      ) : filed ? (
        <Seal size="lead" label={taken} />
      ) : null}
    </p>
  );
}

/** The key figures as a ledger: amounts in the first column, rates in the second, each value after a
 * red rule with the seal of its filing. The period they share is in the margin; a figure from another
 * says when. */
function Figures({ companyId, name, row, canFin, flags, lead, eps, asOf, notKept = false, pending = null }: {
  companyId: string;
  name: string;
  row: ScreenerRow;
  canFin: boolean;
  flags: StaleFlag[];
  lead: boolean;
  /** /per-share's EPS rows (epsStat): undefined while loading, null when unavailable */
  eps?: FinancialRow[] | null;
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
      <Folio name="Figures" line="Key figures from the filings" lead={lead}>
        <p className="empty locked">Financials for {name} aren't included in your access; its filings and metadata are public.</p>
      </Folio>
    );
  }
  if (notKept && asOf) {
    const at = (tab: "statements" | "ratios") => href({ view: "company", companyId, tab, asOf });
    return (
      <Folio name="Figures" line={`As they stood on ${day(asOf, true)}`} lead={lead}>
        <p className="empty asof-not-kept" role="note">
          {name}'s figures as they stood on {day(asOf, true)} aren't kept here yet: Tributary is building each company's history of
          its figures, a few thousand companies a day, and {name}'s isn't built. Until it is, <a href={at("statements")}>Statements</a>{" "}
          and <a href={at("ratios")}>Ratios</a> work them out for that day from the filings filed by then (it takes a little longer).
        </p>
      </Folio>
    );
  }
  // EPS over the twelve months, from /per-share (finding 1); the screener's quarter only as a fallback
  const stats = keyStats(row)
    .map((s) => (s.key === "eps_diluted_q" ? epsStat(eps, s, asOf) : s))
    .map((s) => ({ ...s, flag: keyStatFlag(s.key, s.periodEnd, flags) }));
  // the period most of them share goes in the margin; the others say their own
  const ends = stats.map((s) => s.periodEnd).filter((e): e is string => !!e);
  const common = ends.sort((a, b) => ends.filter((x) => x === b).length - ends.filter((x) => x === a).length)[0] ?? null;
  const sealed = stats.filter((s) => s.source && !s.status && statFiled(s).filed);
  const sources = new Set(sealed.map((s) => `${s.source!.form} filed ${day(s.source!.filed, true)}`));
  const computed = stats.some((s) => s.value !== null && !statFiled(s).filed);
  const flagged = [...new Map(stats.filter((s) => s.flag).map((s) => [s.flag!.short, s.flag!])).values()];
  return (
    <Folio name="Figures" lead={lead}
      line={common ? `As filed, to ${day(common, true)}` : "As filed"}
      aside={`${currency ?? "US dollars"}; twelve months, balances at the end`}>
      <div className="ledger">
        <dl aria-label="Amounts">{stats.filter(isAmount).map((s) => <LedgerRow key={s.key} s={s} common={common} companyId={companyId} armNote={armNote} />)}</dl>
        <dl aria-label="Rates">{stats.filter((s) => !isAmount(s)).map((s) => <LedgerRow key={s.key} s={s} common={common} companyId={companyId} armNote={armNote} />)}</dl>
      </div>
      <div className="fnote">
        {pending}
        {sources.size > 0 && (
          <p>
            <Seal />
            <span>
              Sealed: stated as shown in the {[...sources].slice(0, 2).join(" and the ")}
              {sources.size > 2 ? ` and ${sources.size - 2} more` : ""}. A seal opens the filing at its statements.
            </span>
          </p>
        )}
        {computed && (
          <p className="computed-note">
            <span className="seal-slot" aria-hidden="true" />
            <span>No seal: worked out from the filings (four quarters added up, a ratio or a difference). Hover or tap one for how.</span>
          </p>
        )}
        {flagged.map((f) => (
          <p key={f.short} role="note">
            <span className="mark" aria-hidden="true">*</span>
            <span>{f.asOf ? `At ${day(f.asOf, true)}, before` : "Before"} {f.short}, which may have changed it.</span>
          </p>
        ))}
        {isBankRow(row) && "net_interest_income_ttm" in row.values && <p className="bank-note">A bank's own figures. {BANK_NOT_SHOWN}</p>}
        {armNote && <p className="debt-note" role="note">{armNote}</p>}
      </div>
    </Folio>
  );
}

function LedgerRow({ s, common, companyId, armNote }: { s: KeyStat & { flag: StaleFlag | null }; common: string | null; companyId: string; armNote: string | null }) {
  // an old figure reads as not reported since its date (blanks.ts): no seal, the last figure in the hover
  const blank = s.value === null && s.status !== "other_currency";
  // a seal says "taken from the filing": only a figure the filing states has one (Riley 32); a computed
  // one says in its hover what it is worked out from
  const filing = statFiled(s);
  const link = s.source && !blank && filing.filed ? sealHref(companyId, s.source) : null;
  const differs = s.old || (s.periodEnd && common && s.periodEnd !== common) || blank;
  const how = !blank && !filing.filed ? `${s.label}, ${filing.how}.` : null;
  const title = [how, s.title, s.key === "net_debt_q" ? armNote : null, s.flag?.text].filter(Boolean).join("\n\n");
  const classes = [blank && "blank", s.old && "old", s.flag && "later"].filter(Boolean).join(" ") || undefined;
  const taken = s.source ? `the ${s.source.form} filed ${day(s.source.filed, true)}` : "";
  return (
    <div data-key={s.key}>
      <dt>
        {s.label}
        {differs && <span className="when">{s.period}</span>}
      </dt>
      {/* a computed figure's how opens on focus too (a tap, Tab), not only a mouse's hover (Riley, test 10, finding 55) */}
      <dd className={classes} title={link ? undefined : title} tabIndex={how ? 0 : undefined}>
        <span className="figure">{isAmount(s) ? paren(s.text) : s.text}</span>
        {link ? (
          <SealLink href={link} label={`${s.label}: taken from ${taken}`} source={title} />
        ) : (
          <span className="seal-slot" />
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
      aside={foreign ? undefined : "Against the prior quarter and a year earlier; no consensus estimates"}>
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
        <Gate load={load} name={name} what="Financials">
          {() => (
            <>
              <RecapTable data={data!} label="Latest quarter" />
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
                Margins change in basis points. Values as currently reported; a restated value cites the restating filing.
              </p>
            </>
          )}
        </Gate>
      )}
    </Folio>
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
        <Gate load={load} name={name} what="Filings">
          {() =>
            section ? (
              <div className="business">
                {/* its own headings as headings, not one run of text (Riley, test 10, finding 76); the words are the filing's */}
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

/** Quarterly revenue (bars) and operating margin (line, right axis) on fiscal quarters, in the
 * chart builder's chart: hover shows each value's filing. */
function Revenue({ token, companyId, name, foreign }: { token: string; companyId: string; name: string; foreign: string | null }) {
  const svg = useRef<SVGSVGElement>(null);
  const load = useLoad(companyId, () => financials(token, companyId, 12, ["revenue", "operating_margin"]));
  const series = load?.state === "ok"
    ? [
        financialSeries(load.data.rows, "revenue", { id: 1, name: "Revenue", color: 0, type: "bar", axis: "L" }, companyId),
        financialSeries(load.data.rows, "operating_margin", { id: 2, name: "Operating margin", color: 1, type: "line", axis: "R" }, companyId),
      ].filter((s) => s.points.length)
    : [];
  // the heading says what's drawn: a bank reports no operating margin, so it gets revenue alone
  const heading = series.length === 1 ? series[0]!.name : "Revenue and operating margin";
  const without = series.length === 1 ? (series[0]!.id === 1 ? "No operating margin reported" : "No revenue reported") : undefined;
  // the chart is today's (said so), but its way to the statements keeps the page's day (Morgan 10: it went to today)
  const asOf = useContext(TodaysView);
  return (
    <Folio name={heading} line="Fiscal quarters, the last twelve"
      aside={<>{without && <>{without}. </>}<a href={href({ view: "company", companyId, tab: "statements", ...(asOf ? { asOf } : {}) })}>The statements</a></>}>
      {series.length ? (
        <div className="overview-chart">
          <LayerChart series={series} title="" footnote={[]} svgRef={svg}
            onOpen={(src) => go({ view: "filing", companyId, accessionNo: src.accessionNo, section: null })} />
        </div>
      ) : (
        !load || load.state === "loading" ? (
          <Skeleton chart label="Loading revenue" />
        ) : load.state === "error" ? (
          <ErrorPanel what="revenue" onRetry={load.retry} />
        ) : (
          <p className="empty">
            {/* a once-a-year filer has no quarters to chart: said, not blank (Riley, test 10, finding 28) */}
            {foreign
              ? `${name} reports once a year (Form ${foreign}), so there is no quarterly revenue to chart. Statements shows its years.`
              // a company before revenue (Terrestrial Energy, Deep Fission) has figures, only no revenue: said
              // as such, never as no data (the assistant read the blank as "no financial data")
              : `No revenue reported: ${name}'s filings state no revenue figure yet, or none could be read. Statements shows what it does report.`}
          </p>
        )
      )}
    </Folio>
  );
}
