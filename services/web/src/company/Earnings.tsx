import { useId, useState } from "react";

import { earnings, recap, releaseRecap } from "../api";
import { PinButton } from "../analysis/Pin";
import { quoteLines, quoteTable, type Passage } from "../analysis/project";
import { NO_DATA } from "../blanks";
import { day, sentenceCase } from "../format";
import { href } from "../route";
import type { EarningsQuarter, EarningsRelease, ReleasePassage } from "../types";
import { recapFromRelease, recapRows } from "./data";
import { firstQuarter, foreignForm, itemLabel, passageLines, quarterName } from "./events";
import { RecapTable } from "./RecapTable";
import { ReleaseParts } from "./ReleaseTable";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

/** The quarter's figures beside the release: the income-statement lines of the 10-Q/10-K recap. */
export const RELEASE_FIGURES = ["revenue", "gross_margin", "operating_margin", "net_margin", "operating_income", "net_income", "eps_diluted", "free_cash_flow"] as const;

/** Earnings, per quarter (Query API /earnings; rules in query_api/events.py and releases.py): the
 * earnings release (8-K Item 2.02, its first EX-99 exhibit) beside the quarter's reported figures
 * from the 10-Q/10-K (the recap route: gold's values with their n/m readings), the outlook the
 * release states under its own Outlook or Guidance headings, and management's adjustments (its
 * reconciliation tables, its notes with dollar amounts). Every passage is quoted as written, with a
 * link to it in the reader, and pins into an analysis's thesis with its quarter. A passage that
 * quotes one of the release's own tables shows it as a table (`parts`, parser v7: the Query API's
 * grid, columns as the release has them); one parsed before v7 keeps its lines (analyst test 4,
 * finding 31). Where the release has none of something, this says so. No beat or miss: Tributary has
 * no estimates. The company page's Earnings tab and the Analysis flow's Earnings step. */
export function EarningsView({ token, companyId, name, canReadFilings, canReadFinancials }: {
  token: string;
  companyId: string;
  name: string;
  canReadFilings: boolean;
  canReadFinancials: boolean;
}) {
  const load = useLoad(canReadFilings ? `${companyId}|earnings` : null, () => earnings(token, companyId, 4));
  const [pick, setPick] = useState<string | null>(null);
  if (!canReadFilings) {
    return <p className="empty locked card">The earnings release is filing text, which your access doesn't include for {name}.</p>;
  }
  return (
    <Gate load={load} name={name} what="Filings">
      {() => {
        const data = load?.state === "ok" ? load.data : null;
        if (data === null) return <p className="empty card">Earnings releases aren't available here yet. Each one is an 8-K on the Filings tab.</p>;
        if (!data.quarters.length) {
          return <p className="empty card">No 10-Q, 10-K or earnings release for {name} in the filing index in the last two years.</p>;
        }
        const shown = data.quarters.find((q) => q.period_end === pick) ?? firstQuarter(data.quarters)!;
        return (
          <>
            <div className="row wrap earnings-pick">
              <span className="seg" role="group" aria-label="Quarter">
                {data.quarters.map((q) => (
                  <button key={q.period_end} className={q === shown ? "on" : undefined} aria-pressed={q === shown}
                    onClick={() => setPick(q.period_end)} title={quarterName(q)}>
                    {day(q.period_end, true)}{q.release ? "" : " · no release"}
                  </button>
                ))}
              </span>
              <span className="faint small">quarters by their end date · no consensus or beat/miss in Tributary</span>
            </div>
            <Quarter key={shown.period_end} token={token} companyId={companyId} name={name} q={shown} canReadFinancials={canReadFinancials} />
            <p className="faint small earnings-method">{data.method}</p>
          </>
        );
      }}
    </Gate>
  );
}

function exhibitName(key: string | null): string {
  if (!key) return "the 8-K";
  if (key === "6-K") return "the 6-K itself";
  const ex = /^EX-(\d+(?:\.\d+)?)$/.exec(key);
  return ex ? `Exhibit ${ex[1]}` : `Item ${key}`;
}

/** "8-K filed Oct 28, 2026 · Results of operations" or, for a foreign filer (migration 095),
 * "6-K filed Oct 15, 2026 · results release": a 6-K has no items. */
function releaseLine(rel: EarningsRelease): string {
  if (rel.form === "6-K") return `6-K filed ${day(rel.filed, true)} · results release`;
  return `8-K filed ${day(rel.filed, true)} · ${rel.items.filter((i) => i.item !== "9.01").map(itemLabel).join(", ") || "Results of operations"}`;
}

/** What a pinned passage of this release says about where it's from (its quarter: H13). */
function passageOf(companyId: string, q: EarningsQuarter, rel: EarningsRelease, p: ReleasePassage, what: string): Passage {
  return {
    text: p.text, companyId, accessionNo: rel.accession_no, form: rel.form, filed: rel.filed,
    period: q.period_end, periodWord: "quarter",
    section: `Earnings release (${exhibitName(rel.section_key)}), ${what}${p.heading ? `: ${p.heading}` : ""}`,
  };
}

function Quarter({ token, companyId, name, q, canReadFinancials }: {
  token: string;
  companyId: string;
  name: string;
  q: EarningsQuarter;
  canReadFinancials: boolean;
}) {
  const id = useId();
  const rel = q.release;
  const readerAt = (p: ReleasePassage) => rel && rel.section_key
    ? href({ view: "filing", companyId, accessionNo: rel.accession_no, section: rel.section_key, at: p.citation.char_start ?? p.char_start })
    : null;
  const tables = q.adjustments.filter((a) => a.kind === "reconciliation");
  const notes = q.adjustments.filter((a) => a.kind === "note");
  return (
    <>
      <div className="grid2">
        <section className="card release-card" aria-labelledby={`${id}-rel`}>
          <div className="card-h">
            <h3 id={`${id}-rel`}>Earnings release</h3>
            <span className="sub">{quarterName(q)}</span>
          </div>
          {!rel ? (
            <p className="empty">
              {foreignForm(q.report?.form)
                ? `No results release (a 6-K) stored for this year: Tributary keeps the 6-Ks whose period or title says results. The ${q.report!.form} is the annual report.`
                : "No earnings release (an 8-K with Item 2.02) for this quarter in the filing index. Many smaller companies report only in the 10-Q or 10-K."}
            </p>
          ) : (
            <div className="card-b">
              {q.headline ? <blockquote className="release-headline">{q.headline.text}</blockquote> : null}
              <p className="small muted">
                {releaseLine(rel)}
                {rel.section_key ? ` · ${exhibitName(rel.section_key)}` : " · its text isn't stored here"}
              </p>
              <div className="row wrap">
                {rel.section_key && (
                  <a className="btn sm" href={href({ view: "filing", companyId, accessionNo: rel.accession_no, section: rel.section_key })}>
                    Read the release
                  </a>
                )}
                {(rel.document_url ?? rel.filing_url) && (
                  <a className="edgar-link" href={(rel.document_url ?? rel.filing_url)!} target="_blank" rel="noopener noreferrer"
                    title={`The ${rel.form} on EDGAR, sec.gov (${rel.accession_no})`}>EDGAR ↗</a>
                )}
              </div>
              {q.other_releases.length > 0 && (
                <p className="faint small">
                  {rel.form === "6-K" ? "Also stored for this period:" : "Also filed under Item 2.02 for this quarter:"}{" "}
                  {q.other_releases.map((o, i) => (
                    <span key={o.accession_no}>
                      {i ? ", " : ""}
                      {o.filing_url ? <a href={o.filing_url} target="_blank" rel="noopener noreferrer">{rel.form} of {day(o.filed, true)} ↗</a> : `${rel.form} of ${day(o.filed, true)}`}
                    </span>
                  ))}
                </p>
              )}
            </div>
          )}
        </section>
        <Figures token={token} companyId={companyId} name={name} q={q} canReadFinancials={canReadFinancials} />
      </div>

      <section className="card" aria-labelledby={`${id}-out`}>
        <div className="card-h wrap">
          <h3 id={`${id}-out`}>Outlook stated in the release</h3>
          <span className="sub">quoted from its Outlook or Guidance sections{rel ? " · pin a block to quote it in your thesis with its quarter" : ""}</span>
        </div>
        {!rel ? (
          <p className="empty">No release, so no outlook for this quarter.</p>
        ) : !rel.section_key ? (
          <p className="empty">The release's text isn't stored here yet; read it on EDGAR.</p>
        ) : q.outlook.length === 0 ? (
          <p className="empty">
            This release has no section headed Outlook or Guidance. Some companies give guidance elsewhere (a separate 8-K under
            Item 7.01, the call) or not at all; <a href={href({ view: "filing", companyId, accessionNo: rel.accession_no, section: rel.section_key })}>read the release</a> to be sure.
          </p>
        ) : (
          <div className="card-b release-blocks">
            {q.outlook.map((p) => (
              <Block key={p.char_start} p={p} at={readerAt(p)}
                make={async () => quoteLines(passageOf(companyId, q, rel, p, "outlook"))} what="outlook passage" />
            ))}
          </div>
        )}
      </section>

      <section className="card" aria-labelledby={`${id}-adj`}>
        <div className="card-h wrap">
          <h3 id={`${id}-adj`}>Management's adjustments</h3>
          <span className="sub">the release's reconciliations of its own measures to GAAP, and its notes on figures with a dollar amount</span>
        </div>
        {!rel || !rel.section_key ? (
          <p className="empty">No release text, so no adjustments for this quarter.</p>
        ) : !q.adjustments.length ? (
          <p className="empty">
            No reconciliation table and no note with a dollar amount in the forms Tributary reads: a table headed “Reconciliation”,
            a footnote such as “(1) … $”, or a lettered note under a non-GAAP heading. The release may report GAAP figures only, or
            reconcile them elsewhere, such as in a financial supplement filed as another exhibit; the release itself is in the reader.
          </p>
        ) : (
          <div className="card-b release-blocks">
            {notes.map((p) => (
              <Block key={p.char_start} p={p} at={readerAt(p)}
                make={async () => quoteLines(passageOf(companyId, q, rel, p, "note"))} what="note" />
            ))}
            {tables.map((p) => (
              <details key={p.char_start} className="release-table-box">
                <summary title={p.heading ?? undefined}>{p.heading ? sentenceCase(p.heading) : "Reconciliation"}</summary>
                {p.parts
                  ? <ReleaseParts parts={p.parts} heading={p.heading} label={p.heading ?? "Reconciliation"} skipHeading />
                  : <pre className="release-table" tabIndex={0}>{p.text.split("\n").slice(1).join("\n")}</pre>}
                <div className="row wrap">
                  {readerAt(p) && <a className="reader-link" href={readerAt(p)!}>Read it in the release</a>}
                  {p.truncated && <span className="faint small">cut here; the reader has all of it</span>}
                  <PinButton what="table" make={async () => quoteTable(passageOf(companyId, q, rel, p, "reconciliation"))} />
                </div>
              </details>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

/** One quoted block: its lines as the release wrote them (bullets as a list) and, when it quotes one
 * of the release's tables, that table; where to read it, a pin. */
function Block({ p, at, make, what }: { p: ReleasePassage; at: string | null; make: () => Promise<string>; what: string }) {
  const lines = passageLines(p);
  return (
    <div className="release-block pinnable">
      {p.parts ? <ReleaseParts parts={p.parts} heading={p.heading} label={p.heading ?? "Outlook"} /> : (
        <div className="release-lines">
          {lines.map((l, i) => (
            <p key={i} className={`rl ${l.kind}`}>{l.kind === "bullet" || l.kind === "sub" ? <span aria-hidden="true">• </span> : null}{l.text}</p>
          ))}
        </div>
      )}
      <div className="row wrap">
        {at && <a className="reader-link" href={at}>Read it in the release</a>}
        {p.truncated && <span className="faint small">cut here; the reader has the rest</span>}
        <PinButton compact what={what} make={make} />
      </div>
    </div>
  );
}

/** The quarter's reported figures from its 10-Q/10-K (gold, through the recap route), or why not. A
 * foreign filer's quarter with no annual report behind it (165): the figures gold read from its 6-K
 * results release's tables, through the same route (the quarter named, since the quarter's first 6-K
 * may not be the one its figures cite); none there, or an older API, and it says so as before. */
function Figures({ token, companyId, name, q, canReadFinancials }: {
  token: string; companyId: string; name: string; q: EarningsQuarter; canReadFinancials: boolean;
}) {
  const foreign = foreignForm(q.report?.form) || (!q.report && foreignForm(q.release?.form));
  const acc = q.report && canReadFinancials && !foreign ? q.report.accession_no : null;
  const load = useLoad(acc, () => recap(token, acc!));
  const data = load?.state === "ok" ? load.data : null;
  const empty = data ? recapRows(data, RELEASE_FIGURES).rows.every((r) => r.current === NO_DATA) : false;
  const sixK = !q.report && q.release?.form === "6-K" && canReadFinancials ? q.release : null;
  const rel = useLoad(sixK ? `${sixK.accession_no}|${q.period_end}` : null, () => releaseRecap(token, sixK!.accession_no, q.period_end));
  const relData = rel?.state === "ok" && rel.data && recapRows(rel.data, RELEASE_FIGURES).rows.some((r) => r.current !== NO_DATA) ? rel.data : null;
  const byYear = (
    <p className="empty">
      {name} files its annual report on {q.report?.form ?? "Form 20-F or 40-F"} and its results on 6-K, so its figures are by fiscal
      year, in its own currency, on the Financials tab. For {q.report ? "the year" : "this period"}, the release's own tables are in
      the reader.
    </p>
  );
  return (
    <section className="card">
      <div className="card-h">
        <h3>Reported figures</h3>
        <span className="sub">
          {q.report ? `${q.report.form} filed ${day(q.report.filed, true)} · vs the same quarter a year ago`
            : relData ? `${relData.form} filed ${day(relData.filed, true)}, against the same quarter a year ago` : "from the quarter's 10-Q or 10-K"}
        </span>
      </div>
      {!canReadFinancials ? (
        <p className="empty locked">The figures need financials for {name}, which your access doesn't include.</p>
      ) : sixK ? (
        <Gate load={rel} name={name} what="Financials">
          {() => relData ? (
            <>
              <RecapTable data={relData} only={RELEASE_FIGURES} label="Reported figures" yearAgo companyId={companyId} />
              <div className="foot">
                {recapFromRelease(relData)
                  ? "From the 6-K results release, read from its own tables where no XBRL covers the quarter, so those figures carry no " +
                    "tick; each card names the table and row. As currently reported (hover a row for its filings)."
                  : "From the 6-K, as currently reported (hover a row for its filings)."}
              </div>
            </>
          ) : byYear}
        </Gate>
      ) : foreign ? (
        byYear
      ) : !q.report ? (
        <p className="empty">
          The {q.estimated ? "10-Q or 10-K for this quarter isn't filed yet" : "quarter's 10-Q or 10-K isn't in the filing index"}, and the figures come
          from it: until then, the release's own tables (in the reader) are the only figures for the quarter.
        </p>
      ) : (
        <Gate load={load} name={name} what="Financials">
          {() => empty ? (
            <p className="empty">
              The {q.report!.form} is filed, but its figures haven't reached Tributary yet (the SEC's XBRL data runs a day or so behind
              EDGAR). The release's own tables are in the reader.
            </p>
          ) : (
            <>
              <RecapTable data={data!} only={RELEASE_FIGURES} label="Reported figures" yearAgo />
              <div className="foot">
                From the {q.report!.form}, as currently reported (hover a row for its filings). Margins change in points; EPS has no
                fiscal Q4 of its own (the 10-K reports the year).
              </div>
            </>
          )}
        </Gate>
      )}
    </section>
  );
}
