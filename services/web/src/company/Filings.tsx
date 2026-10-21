import { useState } from "react";

import { filings as fetchFilings, sectionChanges } from "../api";
import { day } from "../format";
import { href } from "../route";
import type { SectionChanges } from "../types";
import { eightKItems, formMatches, type FormFilter } from "./data";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

const FILTERS: FormFilter[] = ["all", "10-K", "10-Q", "8-K"];

/** Parsed filings (newest first) with form filters and 8-K items; Read opens the reader, What
 * changed shows the Risk Factors and MD&A paragraphs that changed since the prior filing. */
export function Filings({ token, companyId, name, initialDiff }: { token: string; companyId: string; name: string; initialDiff?: string }) {
  const [filter, setFilter] = useState<FormFilter>("all");
  const [diff, setDiff] = useState<string | null>(initialDiff ?? null);
  const load = useLoad(companyId, () => fetchFilings(token, companyId));
  const shown = load?.state === "ok" ? load.data.filter((f) => formMatches(f.form, filter)) : [];

  return (
    <div className="grid2">
      <section className="card">
        <div className="card-h">
          <h2>Filings</h2>
          <span className="sub">parsed filings, newest first</span>
          <span className="grow" />
          <span className="seg" role="group" aria-label="Form">
            {FILTERS.map((f) => (
              <button key={f} className={f === filter ? "on" : undefined} aria-pressed={f === filter} onClick={() => setFilter(f)}>
                {f === "all" ? "All" : f}
              </button>
            ))}
          </span>
        </div>
        <Gate load={load} name={name} what="Filings">
          {() =>
            shown.length === 0 ? (
              <p className="empty">No {filter === "all" ? "" : `${filter} `}filings parsed yet.</p>
            ) : (
              <div className="tw">
                <table className="t">
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
                    {shown.map((f) => {
                      const items = f.form.startsWith("8-K") ? eightKItems(f.sections.map((s) => s.section_key)) : [];
                      const periodic = /^10-[KQ]/.test(f.form);
                      return (
                        <tr key={f.accession_no} className={diff === f.accession_no ? "sel" : undefined}>
                          <td className="muted">{day(f.filed, true)}</td>
                          <td>
                            <span className={`tag ${f.form.startsWith("10-K") ? "k" : f.form.startsWith("10-Q") ? "q" : ""}`}>{f.form}</span>
                          </td>
                          <td className="muted">{f.period_of_report ? day(f.period_of_report, true) : "—"}</td>
                          <td className="muted">
                            {items.length ? items.join(" · ") : `${f.sections.length} section${f.sections.length === 1 ? "" : "s"}`}
                          </td>
                          <td className="mono faint">{f.accession_no}</td>
                          <td className="n actions">
                            <a className="btn sm ghost" href={href({ view: "filing", companyId, accessionNo: f.accession_no, section: null })}>
                              Read
                            </a>
                            {periodic && (
                              <button className="btn sm ghost" onClick={() => setDiff(f.accession_no)}>
                                What changed
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          }
        </Gate>
        <div className="foot">The last 20 filings Tributary has parsed into sections.</div>
      </section>
      <section className="card">
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
    </div>
  );
}

function Changes({ token, companyId, accessionNo, name }: { token: string; companyId: string; accessionNo: string; name: string }) {
  const load = useLoad(accessionNo, () => sectionChanges(token, companyId, accessionNo));
  if (load?.state === "ok") return <ChangeList data={load.data} companyId={companyId} />;
  return (
    <Gate load={load} name={name} what="Filings">
      {() => null}
    </Gate>
  );
}

function ChangeList({ data, companyId }: { data: SectionChanges; companyId: string }) {
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
                    <a href={href({ view: "filing", companyId, accessionNo: c.citation.accession_no, section: c.citation.section_key })}>
                      {c.citation.section_key}
                    </a>
                  </>
                )}
              </p>
            ))
          )}
        </div>
      ))}
      <div className="faint small">Paragraph-level comparison; changes to numbers alone are not shown.</div>
    </div>
  );
}
