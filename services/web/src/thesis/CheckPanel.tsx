import { useId, useMemo } from "react";

import { ago, day, pct } from "../format";
import { renderAnswer } from "../markdown";
import { href, SECTION } from "../route";
import type { CheckCitation, CheckClaim, CheckFigure, CheckModel, ClaimStatus, ThesisCheck, ThesisCheckSummary } from "../types";
import { asCitations, points, problems, STATUS_LABELS, unverifiedFigures } from "./check";
import { diffChecks, flagWords, isQuiet, type CheckDiff } from "./revisit";
import type { ThesisChecks } from "./useThesisCheck";

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** The assistant's check of a thesis, beside it: claims with status badges and citations
 * (clicking a quote highlights it in the thesis), the models' assumptions against history,
 * what the thesis doesn't mention, the summary, and a history of checks. All model text goes
 * through markdown.ts renderAnswer. `stale`: the text changed since this check. */
export function CheckPanel({ checks, stale, onQuote, label }: {
  checks: ThesisChecks;
  stale: boolean;
  onQuote: (quote: string) => void;
  label: (companyId: string) => string;
}) {
  const id = useId();
  const { history, shown, run, show, stop } = checks;
  return (
    <aside className="check-panel card" aria-label="Thesis check">
      <div className="row wrap check-head">
        <h2>Check</h2>
        <span className="grow" />
        {history.length > 0 && (
          <>
            <label className="visually-hidden" htmlFor={`${id}-history`}>Earlier checks</label>
            <select id={`${id}-history`} className="field narrow" value={shown?.check_id ?? ""} onChange={(e) => show(e.target.value)}>
              {history.map((c) => (
                <option key={c.check_id} value={c.check_id}>{historyLabel(c)}</option>
              ))}
            </select>
          </>
        )}
      </div>
      {run.status === "running" && (
        <p className="report-status" role="status">
          Checking the thesis against the filings… {run.tools} lookup{run.tools === 1 ? "" : "s"} so far.{" "}
          <button className="btn sm ghost" onClick={stop}>Stop</button>
        </p>
      )}
      {run.status === "error" && <p className="report-status error" role="alert">{run.message}</p>}
      {stale && shown && (
        <p className="report-status check-stale" role="status">
          Stale: the thesis changed since this check. Check it again to see the current text checked.
        </p>
      )}
      {shown ? <CheckBody check={shown} previous={checks.previous} onQuote={onQuote} label={label} /> : run.status !== "running" && (
        <p className="empty">
          No check yet. “Check my thesis” has the assistant test each factual claim against the filings, compare your
          model's assumptions with history, and list what the latest 10-K or 10-Q says that you don't.
        </p>
      )}
    </aside>
  );
}

function historyLabel(c: ThesisCheckSummary): string {
  const n = c.counts;
  return `${day(c.created_at, true)} ${c.created_at.slice(11, 16)} · ${n.supported}✓ ${n.contradicted}✗ ${n.unverifiable}?`;
}

function CheckBody({ check, previous, onQuote, label }: {
  check: ThesisCheck;
  previous: ThesisCheck | null;
  onQuote: (q: string) => void;
  label: (c: string) => string;
}) {
  const r = check.result;
  const issues = problems(r);
  const diff = useMemo(() => (previous ? diffChecks(previous.result, r) : null), [previous, r]);
  return (
    <div className="check-body">
      <p className="faint small">
        Checked {ago(r.checked_at)} by {r.model}
        {r.cost_usd !== null ? ` · about $${r.cost_usd.toFixed(3)}` : ""} · counts as {r.questions} questions
        {r.as_of ? ` · data as of ${day(r.as_of, true)}` : ""}
      </p>
      {issues.length > 0 ? (
        <p className="badge warn" title="Flagged items are marked below">⚠ {issues.join(", ")}</p>
      ) : (
        r.verification.used_data && <p className="badge ok">✓ Every figure and filing traced to data the assistant read</p>
      )}
      {diff && previous && <Changes diff={diff} since={previous.created_at} onQuote={onQuote} />}
      {r.summary && <Text text={r.summary} figures={r.verification.figures} />}
      {r.notes.length > 0 && (
        <ul className="check-notes small">
          {r.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}

      <h3>Claims</h3>
      {r.claims.length ? (
        <ol className="claims">
          {r.claims.map((c, i) => <Claim key={i} claim={c} onQuote={onQuote} />)}
        </ol>
      ) : (
        <p className="empty">No factual claims found.</p>
      )}

      {r.assumptions.length > 0 && (
        <>
          <h3>Model assumptions</h3>
          {r.assumptions.map((m) => <Assumptions key={m.ref} model={m} label={label} />)}
        </>
      )}

      <h3>Not mentioned</h3>
      {r.not_mentioned.length ? (
        <ul className="not-mentioned">
          {r.not_mentioned.map((p, i) => (
            <li key={i}>
              <Text text={p.point} figures={p.figures} cites={p.citations} />
              <Cites cites={p.citations} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty">Nothing material from the latest filings that the thesis leaves out.</p>
      )}
    </div>
  );
}

/** What changed since the previous check (revisit.ts diffChecks). Quotes are plain text (React
 * escapes them; they are the thesis's own words), statuses are labels: no model prose here. */
export function Changes({ diff, since, onQuote }: { diff: CheckDiff; since: string; onQuote: (q: string) => void }) {
  const when = day(since, true);
  const badge = (s: ClaimStatus) => <span className={`status-badge ${s}`}>{STATUS_LABELS[s]}</span>;
  const quote = (q: string) => (
    <button className="claim-quote" onClick={() => onQuote(q)} title="Show this sentence in the thesis">“{q}”</button>
  );
  return (
    <section className="check-changes" aria-label={`Changes since ${when}`}>
      <h3>Changes since {when}</h3>
      {isQuiet(diff) ? (
        <p className="faint small">
          No claim changed status ({diff.unchanged} the same), no assumption flag changed.
        </p>
      ) : (
        <ul className="changes small">
          {diff.flipped.map((f, i) => (
            <li key={`f${i}`} className="change flipped">
              {badge(f.from)} → {badge(f.to)} {quote(f.quote)}
            </li>
          ))}
          {diff.added.map((c, i) => (
            <li key={`a${i}`} className="change added">
              <span className="muted">New:</span> {badge(c.status)} {quote(c.quote)}
            </li>
          ))}
          {diff.removed.map((c, i) => (
            <li key={`r${i}`} className="change removed">
              <span className="muted">No longer checked:</span> {badge(c.status)} <span className="claim-quote missing">“{c.quote}”</span>
            </li>
          ))}
          {diff.flags.map((f, i) => (
            <li key={`g${i}`} className="change flag">
              <span className="muted">{f.model} FY{f.fy}E {f.what}:</span> {flagWords(f.what, f.from)} → <b>{flagWords(f.what, f.to)}</b>
            </li>
          ))}
          {diff.modelsAdded.map((m) => <li key={`ma${m}`} className="change added"><span className="muted">Model now checked:</span> {m}</li>)}
          {diff.modelsRemoved.map((m) => <li key={`mr${m}`} className="change removed"><span className="muted">Model no longer checked:</span> {m}</li>)}
          {diff.unchanged > 0 && <li className="faint">{diff.unchanged} claim{diff.unchanged === 1 ? "" : "s"} unchanged</li>}
        </ul>
      )}
      {diff.notMentioned.before !== diff.notMentioned.after && (
        <p className="faint small">Not mentioned: {diff.notMentioned.before} → {diff.notMentioned.after} points.</p>
      )}
    </section>
  );
}

function Claim({ claim, onQuote }: { claim: CheckClaim; onQuote: (q: string) => void }) {
  return (
    <li className={`claim ${claim.status}`}>
      <div className="row wrap">
        <span className={`status-badge ${claim.status}`}>{STATUS_LABELS[claim.status]}</span>
        {claim.downgraded && <span className="faint small" title="The assistant gave no citation of data it read">downgraded</span>}
      </div>
      {claim.quote_found ? (
        <button className="claim-quote" onClick={() => onQuote(claim.quote)} title="Show this sentence in the thesis">
          “{claim.quote}”
        </button>
      ) : (
        <p className="claim-quote missing" title="Not found word for word in the thesis">“{claim.quote}”</p>
      )}
      <Text text={claim.explanation} figures={claim.figures} cites={claim.citations} />
      <Cites cites={claim.citations} />
    </li>
  );
}

function Assumptions({ model, label }: { model: CheckModel; label: (c: string) => string }) {
  const s = model.stats;
  return (
    <div className="check-model">
      <p className="small">
        <a href={href({ view: "model", itemId: model.item_id })}>{model.name}</a>
        <span className="muted"> · {label(model.company_id)}</span>
        {s && (
          <span className="muted">
            {" "}· {s.cagr !== null ? `${s.cagr_years}-yr revenue CAGR ${pct(s.cagr)}` : "no growth history"}
            {s.avg_margin !== null ? ` · ${s.margin_years}-yr avg margin ${pct(s.avg_margin)}` : ""}
            {model.margin_min != null && model.margin_max != null ? ` (range ${pct(model.margin_min)} to ${pct(model.margin_max)})` : ""}
          </span>
        )}
      </p>
      {model.error ? (
        <p className="empty">{model.error}</p>
      ) : (
        <div className="table-wrap">
          <table className="data check-assumptions">
            <thead>
              <tr>
                <th scope="col">Year</th>
                <th scope="col" className="num">Growth</th>
                <th scope="col" className="num">vs CAGR</th>
                <th scope="col" className="num">Margin</th>
                <th scope="col">Comment</th>
              </tr>
            </thead>
            <tbody>
              {model.years.map((y) => (
                <tr key={y.fy} className={y.growth_flag || y.margin_flag ? "flagged" : undefined}>
                  <th scope="row">FY{y.fy}E</th>
                  <td className={y.growth_flag ? "num flag" : "num"} title={y.growth_flag ? `More than 10 points ${y.growth_flag} the CAGR` : undefined}>
                    {y.growth.toFixed(1)}%{y.growth_flag ? " ⚠" : ""}
                  </td>
                  <td className="num">{y.growth_vs_cagr === null ? "–" : points(y.growth_vs_cagr)}</td>
                  <td className={y.margin_flag ? "num flag" : "num"} title={y.margin_flag ? `${y.margin_flag === "above" ? "Above" : "Below"} the 5-year range` : undefined}>
                    {y.margin.toFixed(1)}%{y.margin_flag ? " ⚠" : ""}
                  </td>
                  <td>
                    {y.commentary ? <Text text={y.commentary} figures={y.figures} cites={y.citations} /> : <span className="faint">–</span>}
                    <Cites cites={y.citations} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Model text: Markdown through renderAnswer (sanitized; accession numbers link only when a
 * tool returned them; unverified figures marked). */
function Text({ text, figures, cites = [] }: { text: string; figures: CheckFigure[]; cites?: CheckCitation[] }) {
  const html = useMemo(() => renderAnswer(text, asCitations(cites), unverifiedFigures(figures)), [text, figures, cites]);
  return <div className="answer check-text" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** A claim's filings: EDGAR link, the reader at the cited section, the figure; flagged when no
 * tool returned the filing. */
function Cites({ cites }: { cites: CheckCitation[] }) {
  if (!cites.length) return null;
  return (
    <ul className="check-cites small">
      {cites.map((c, i) => (
        <li key={`${c.accession_no}-${i}`} className={c.verified ? undefined : "unverified"}>
          {c.verified ? (
            <>
              <span className="form">{c.form ?? "filing"}</span>
              {c.filed && <span className="muted"> {c.filed}</span>}{" "}
              {c.url ? (
                <a href={c.url} target="_blank" rel="noopener noreferrer" className="accession">{c.accession_no}</a>
              ) : (
                <span className="accession">{c.accession_no}</span>
              )}
              {c.section_key && SECTION.test(c.section_key) && c.company_id && CIK.test(c.company_id) && ACCESSION.test(c.accession_no) && (
                <a className="reader-link" href={href({ view: "filing", companyId: c.company_id, accessionNo: c.accession_no, section: c.section_key })}
                  title={`Open ${c.section_key} in the filing reader`}>
                  {" "}§ {c.section_key}
                </a>
              )}
              {c.figure && (
                <span className={c.figure_status === "unverified" ? "figure unverified" : "muted"} title={c.figure_status ?? undefined}> · {c.figure}</span>
              )}
            </>
          ) : (
            <span className="accession unverified" title="The assistant never read this filing: not verified">
              {c.accession_no} (not verified)
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
