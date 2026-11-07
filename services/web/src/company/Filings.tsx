import { Fragment, useRef, useState, type ReactNode } from "react";

import { filingIndex, filings as fetchFilings, sectionChanges } from "../api";
import { day } from "../format";
import { href } from "../route";
import type { IndexedFiling, SectionChanges } from "../types";

export type ChangedSection = SectionChanges["sections"][number];
export type Change = ChangedSection["changes"][number];
/** "📌 Add to thesis" beside each changed paragraph (the Analysis tab's What changed step) */
export type PinChange = (change: Change, section: ChangedSection) => ReactNode;
import { eightKItems, filingHeadings, FORM_FILTERS, formsOf, formTag, parsedNote, type FormFilter } from "./data";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

export const INDEX_PAGE = 25;

/** EDGAR links only: the index's URLs come from the SEC's own submissions data. */
export function edgarLink(f: Pick<IndexedFiling, "document_url" | "filing_url">): string | null {
  const u = f.document_url ?? f.filing_url;
  return u?.startsWith("https://www.sec.gov/") ? u : null;
}

/** Every filing in the SEC index (public metadata, so free plans see it too), newest first,
 * filtered and paged by the Query API, reports first and headed by fiscal year so history is easy
 * to scan. Read opens the reader where the user can read the parsed text; What changed shows the
 * Risk Factors and MD&A paragraphs changed since the prior filing; everything else links to EDGAR. */
export function Filings({ token, companyId, name, canRead, initialDiff, fiscalYearEnd = null }: {
  token: string;
  companyId: string;
  name: string;
  /** the user's tiers include filings for this company */
  canRead: boolean;
  initialDiff?: string;
  /** MMDD, for the fiscal-year headings; without it they go by the year filed */
  fiscalYearEnd?: string | null;
}) {
  const [filter, setFilter] = useState<FormFilter>("reports");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [diff, setDiff] = useState<string | null>(initialDiff ?? null);
  const load = useLoad(`${companyId}|${filter}|${from}|${to}|${offset}`, () =>
    filingIndex(token, companyId, { forms: formsOf(filter), from, to, limit: INDEX_PAGE, offset }),
  );
  const rows = load?.state === "ok" ? load.data.filings : [];
  // 8-K items come from the parsed sections (filings access): the latest parsed filings
  const parsed = useLoad(canRead ? companyId : null, () => fetchFilings(token, companyId));
  const items = new Map(
    parsed?.state === "ok" ? parsed.data.map((f) => [f.accession_no, eightKItems(f.sections.map((s) => s.section_key))]) : [],
  );
  const refilter = (f: () => void) => {
    f();
    setOffset(0);
  };
  const changes = useRef<HTMLElement>(null);
  const showDiff = (accession: string) => {
    setDiff(accession);
    requestAnimationFrame(() => changes.current?.scrollIntoView?.({ block: "start" }));
  };
  const headings = filingHeadings(rows, fiscalYearEnd);

  return (
    <>
      <section className="card">
        <div className="card-h wrap">
          <h2>Filings</h2>
          <span className="sub">
            SEC filing index, newest first{fiscalYearEnd ? " · headed by fiscal year" : ""}{filter === "reports" ? " · 10-K, 10-Q, 8-K (All has insider forms and the rest)" : ""}
          </span>
          <span className="grow" />
          <span className="seg" role="group" aria-label="Form">
            {FORM_FILTERS.map(([f, label]) => (
              <button key={f} className={f === filter ? "on" : undefined} aria-pressed={f === filter}
                onClick={() => refilter(() => setFilter(f))}>
                {label}
              </button>
            ))}
          </span>
          <label className="row">
            <span className="faint">from</span>
            <input type="date" className="field narrow" aria-label="Filed from" value={from}
              onChange={(e) => refilter(() => setFrom(e.target.value))} />
          </label>
          <label className="row">
            <span className="faint">to</span>
            <input type="date" className="field narrow" aria-label="Filed to" value={to}
              onChange={(e) => refilter(() => setTo(e.target.value))} />
          </label>
        </div>
        <Gate load={load} name={name} what="Filings">
          {() =>
            rows.length === 0 ? (
              <p className="empty">
                No {filter === "all" ? "" : `${FORM_FILTERS.find(([f]) => f === filter)![1].toLowerCase()} `}filings{from || to ? " in these dates" : ""}.{" "}
                {filter !== "all" && (
                  <button className="btn sm" onClick={() => refilter(() => setFilter("all"))}>Show all filings</button>
                )}
              </p>
            ) : (
              <div className="tw">
                <table className="t filings">
                  <thead>
                    <tr>
                      <th>Filed</th>
                      <th>Form</th>
                      <th>Period</th>
                      <th>Items / sections</th>
                      <th>Accession</th>
                      <th><span className="visually-hidden">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((f, i) => {
                      const eightK = f.form.startsWith("8-K") ? (items.get(f.accession_no) ?? []) : [];
                      const periodic = /^10-[KQ]/.test(f.form);
                      const edgar = edgarLink(f);
                      const note = parsedNote(f);
                      return (
                        <Fragment key={f.accession_no}>
                        {headings[i] && (
                          <tr className="year-h">
                            <th colSpan={6} scope="rowgroup">{headings[i]}</th>
                          </tr>
                        )}
                        <tr className={diff === f.accession_no ? "sel" : undefined}>
                          <td className="muted">{day(f.filed, true)}</td>
                          <td>
                            <span className={`tag ${formTag(f.form)}`}>{f.form}</span>
                          </td>
                          <td className="muted">
                            {f.period_of_report ? (f.form.startsWith("8-K") ? `event ${day(f.period_of_report, true)}` : day(f.period_of_report, true)) : "—"}
                          </td>
                          <td className="muted items">
                            {eightK.length
                              ? eightK.join(" · ")
                              : f.sections
                                ? note
                                : note && <span className="faint">{note}</span>}
                          </td>
                          <td className="mono faint">{f.accession_no}</td>
                          <td className="n actions">
                            {f.sections_available ? (
                              <a className="btn sm ghost" href={href({ view: "filing", companyId, accessionNo: f.accession_no, section: null })}>
                                Read
                              </a>
                            ) : edgar ? (
                              <a className="btn sm ghost" href={edgar} target="_blank" rel="noopener noreferrer">
                                EDGAR ↗
                              </a>
                            ) : null}
                            {periodic && f.sections_available && (
                              <button className="btn sm ghost" onClick={() => showDiff(f.accession_no)}>
                                What changed
                              </button>
                            )}
                          </td>
                        </tr>
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          }
        </Gate>
        {load?.state === "ok" && (offset > 0 || load.data.has_more) && (
          <div className="pager">
            <span className="muted">
              {offset + 1}–{offset + rows.length}
            </span>
            <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - INDEX_PAGE))}>
              Newer
            </button>
            <button className="btn sm" disabled={!load.data.has_more} onClick={() => setOffset(offset + INDEX_PAGE)}>
              Older
            </button>
          </div>
        )}
        <div className="foot">
          The filing index is public SEC metadata.{" "}
          {canRead
            ? "Read opens the parsed text; filings Tributary hasn't parsed link to EDGAR."
            : "Reading parsed text needs filings access (the pro plan and a grant); until then each filing links to EDGAR."}
        </div>
      </section>
      <section className="card" ref={changes}>
        <div className="card-h">
          <h2>What changed</h2>
          {diff && <span className="sub mono">{diff}</span>}
        </div>
        {diff ? (
          <Changes key={diff} token={token} companyId={companyId} accessionNo={diff} name={name} />
        ) : (
          <p className="empty">
            Choose What changed on a 10-K or 10-Q to see the Risk Factors and MD&amp;A paragraphs
            that are new, removed or rewritten since the previous filing of the same type.
          </p>
        )}
      </section>
    </>
  );
}

/** What changed in one 10-K/10-Q's Risk Factors and MD&A (the Query API's /changes). `pin`
 * renders an action beside each changed paragraph. */
export function Changes({ token, companyId, accessionNo, name, pin }: { token: string; companyId: string; accessionNo: string; name: string; pin?: PinChange }) {
  const load = useLoad(accessionNo, () => sectionChanges(token, companyId, accessionNo));
  if (load?.state === "ok") return <ChangeList data={load.data} companyId={companyId} pin={pin} />;
  return (
    <Gate load={load} name={name} what="Filings">
      {() => null}
    </Gate>
  );
}

function ChangeList({ data, companyId, pin }: { data: SectionChanges; companyId: string; pin?: PinChange }) {
  if (!data.sections.length) return <p className="empty">{data.note ?? "No comparable earlier filing is indexed yet."}</p>;
  return (
    <div className="card-b diff">
      {data.sections.map((s) => (
        <div key={s.section_key} className="diff">
          <div className="row wrap">
            <b>{s.title ?? s.section_key}</b>
            <span className="faint">
              vs {s.compared_to.form} filed {day(s.compared_to.filed, true)}
            </span>
          </div>
          <div className="row wrap">
            <span className="tag q">{s.summary.new} new</span>
            <span className="tag e">{s.summary.rewritten} rewritten</span>
            <span className="tag warn">{s.summary.removed} removed</span>
            <span className="faint">· {s.summary.unchanged + s.summary.minor_edits + s.summary.numbers_only} unchanged or minor, hidden</span>
          </div>
          {s.stale ? (
            <p className="faint">This filing was re-parsed since the comparison was made; it will be recomputed.</p>
          ) : (
            s.changes.map((c, i) => (
              <p key={i} className={c.kind}>
                <b>{c.kind === "new" ? "New" : c.kind === "removed" ? "Removed" : "Rewritten"}:</b>{" "}
                {c.kind === "rewritten" && c.words
                  ? c.words.map(([op, text], j) =>
                      op === "+" ? <ins key={j}>{text} </ins> : op === "-" ? <del key={j}>{text} </del> : <span key={j}>{text} </span>,
                    )
                  : (c.text ?? c.prior_text)}
                {c.citation && (
                  <>
                    {" "}
                    <a href={href({ view: "filing", companyId, accessionNo: c.citation.accession_no, section: c.citation.section_key, at: c.citation.char_start })}>
                      {c.citation.section_key}
                    </a>
                  </>
                )}
                {pin && <> {pin(c, s)}</>}
              </p>
            ))
          )}
        </div>
      ))}
      <div className="faint small">Paragraph-level comparison; changes to numbers alone are not shown.</div>
    </div>
  );
}
