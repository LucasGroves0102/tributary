import { useRef } from "react";

import { companyEvents, financials, latestSection, recap as fetchRecap } from "../api";
import { LayerChart } from "../chart/LayerChart";
import { financialSeries } from "../chart/series";
import { Folio } from "../components/Folio";
import { Seal, SealLink } from "../components/Seal";
import { ErrorPanel, sentence, Skeleton } from "../components/States";
import { day, paren } from "../format";
import { go, href } from "../route";
import type { ScreenerRow, Tier, ValueSource } from "../types";
import {
  BANK_NOT_SHOWN, financeArmNote, foreignReport, header, isAmount, isBankRow, keyStats, longDay, recapAccession, statementSection,
  yearStory,
  type KeyStat, type YearStory,
} from "./data";
import { EventsCard } from "./Events";
import { keyStatFlag, staleFlags, type StaleFlag } from "./events";
import { RecapTable } from "./RecapTable";
import { Gate } from "./Tables";
import { TextKpisCard } from "./TextKpis";
import { useLoad } from "./useLoad";

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
}: {
  token: string;
  companyId: string;
  name: string;
  row: ScreenerRow;
  access: Tier[];
}) {
  const canFin = access.includes("financials");
  const canRead = access.includes("filings");
  // a 20-F/40-F filer files no 10-Q, 10-K or 8-K: those sections say so instead (foreignReport)
  const foreign = foreignReport(row);
  const events = useLoad(canRead && !foreign ? `${companyId}|events` : null, () => companyEvents(token, companyId));
  const flags = staleFlags(events?.state === "ok" ? events.data : null);
  const story = canFin ? yearStory(row) : null;
  return (
    <div className="folios">
      {story && (
        <Folio name="The year" line={`Twelve months to ${day(story.end, true)}`} lead>
          <YearSentence story={story} name={name} companyId={companyId} />
        </Folio>
      )}
      <Figures companyId={companyId} name={name} row={row} canFin={canFin} flags={flags} lead={!story} />
      {foreign ? (
        <Folio name="Since the last report" line="Current reports on Form 6-K" className="events-card">
          <p className="empty foreign-note">
            {name} files as a foreign private issuer: current reports on Form 6-K, not 8-Ks, so there are no 8-K items here to say
            what may have changed since its last report. Its 6-Ks are on the Filings tab.
          </p>
        </Folio>
      ) : canRead && <EventsCard token={token} companyId={companyId} name={name} load={events} folio />}
      <Recap token={token} companyId={companyId} name={name} accession={canFin ? recapAccession(row) : null} canFin={canFin} foreign={foreign} />
      <Business token={token} companyId={companyId} name={name} canRead={canRead} foreign={foreign} />
      {canFin && <Revenue token={token} companyId={companyId} />}
      <TextKpisCard token={token} companyId={companyId} name={name} canRead={canRead} folio />
    </div>
  );
}

/** "In the twelve months to July 26, 2026, Nvidia reported revenue of $302.97 billion, up 83.4% on
 * the year before, and net income of $192.88 billion. It ended the period with $66.00 billion more in
 * cash and short-term investments than debt." Then the seal: the filing it is taken from. */
function YearSentence({ story, name, companyId }: { story: YearStory; name: string; companyId: string }) {
  const src = story.source;
  const link = sealHref(companyId, src);
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
      ) : (
        <Seal size="lead" label={taken} />
      )}
    </p>
  );
}

/** The key figures as a ledger: amounts in the first column, rates in the second, each value after a
 * red rule with the seal of its filing. The period they share is in the margin; a figure from another
 * says when. */
function Figures({ companyId, name, row, canFin, flags, lead }: {
  companyId: string;
  name: string;
  row: ScreenerRow;
  canFin: boolean;
  flags: StaleFlag[];
  lead: boolean;
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
  const stats = keyStats(row).map((s) => ({ ...s, flag: keyStatFlag(s.key, s.periodEnd, flags) }));
  // the period most of them share goes in the margin; the others say their own
  const ends = stats.map((s) => s.periodEnd).filter((e): e is string => !!e);
  const common = ends.sort((a, b) => ends.filter((x) => x === b).length - ends.filter((x) => x === a).length)[0] ?? null;
  const sources = new Set(stats.filter((s) => s.source && !s.status).map((s) => `${s.source!.form} filed ${day(s.source!.filed, true)}`));
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
        {sources.size > 0 && (
          <p>
            <Seal />
            <span>
              Sealed: taken from the {[...sources].slice(0, 2).join(" and the ")}
              {sources.size > 2 ? ` and ${sources.size - 2} more` : ""}. A seal opens the filing at its statements.
            </span>
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
  const link = s.source && !blank ? sealHref(companyId, s.source) : null;
  const differs = s.old || (s.periodEnd && common && s.periodEnd !== common) || blank;
  const title = [s.title, s.key === "net_debt_q" ? armNote : null, s.flag?.text].filter(Boolean).join("\n\n");
  const classes = [blank && "blank", s.old && "old", s.flag && "later"].filter(Boolean).join(" ") || undefined;
  const taken = s.source ? `the ${s.source.form} filed ${day(s.source.filed, true)}` : "";
  return (
    <div data-key={s.key}>
      <dt>
        {s.label}
        {differs && <span className="when">{s.period}</span>}
      </dt>
      <dd className={classes} title={link ? undefined : title}>
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

function Recap({ token, companyId, name, accession, canFin, foreign }: {
  token: string; companyId: string; name: string; accession: string | null; canFin: boolean;
  /** foreignReport: a 20-F/40-F filer, which files no 10-Q or 10-K */
  foreign: string | null;
}) {
  const load = useLoad(accession, () => fetchRecap(token, accession!));
  const data = load?.state === "ok" ? load.data : null;
  return (
    <Folio name="Latest quarter" line={data ? `${data.form} filed ${day(data.filed, true)}` : foreign ? `Annual reports on Form ${foreign}` : "The latest 10-Q or 10-K"}
      aside={foreign ? undefined : "Against the prior quarter and a year earlier; no consensus estimates"}>
      {!canFin ? (
        <p className="empty locked">The recap needs financials for {name}.</p>
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
                <p className="excerpt">{excerpt(section.text)}</p>
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
function Revenue({ token, companyId }: { token: string; companyId: string }) {
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
  return (
    <Folio name={heading} line="Fiscal quarters, the last twelve"
      aside={<>{without && <>{without}. </>}<a href={href({ view: "company", companyId, tab: "statements" })}>The statements</a></>}>
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
          <p className="empty">No quarterly revenue to chart. Statements shows what this company reports.</p>
        )
      )}
    </Folio>
  );
}
