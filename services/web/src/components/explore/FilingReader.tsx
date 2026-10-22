import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import {
  filingIndex,
  parsedFiling,
  searchFilings,
  section as fetchSection,
  sectionChanges,
} from "../../api";
import { edgarLink } from "../../company/Filings";
import { formTag } from "../../company/data";
import { useLoad, type Load } from "../../company/useLoad";
import { ErrorPanel, Skeleton } from "../States";
import { day } from "../../format";
import {
  comparableForm,
  countMatches,
  highlight,
  outlineGroups,
  paragraphAt,
  paragraphs,
  priorComparable,
  searchTerms,
  snippetRuns,
  type Run,
} from "../../reader/text";
import { go, href } from "../../route";
import type { SectionChanges, SectionHit } from "../../types";

interface Props {
  token: string;
  companyId: string;
  accessionNo: string;
  sectionKey: string | null;
  /** a document character offset to scroll to (a citation's char_start) */
  at?: number;
  /** words to highlight, and to search the filing for */
  q?: string;
  /** the company's name, when known */
  name?: string | null;
  onAsk?: (question: string) => void;
  /** called once the filing's text is readable, and again for each section opened (Home's
   * "Continue where you left off") */
  onRead?: (sectionKey: string | null) => void;
}

const SEARCH_FORMS = /^(10-K|10-Q|8-K)(\/A)?$/;

/** A parsed filing: its outline on the left (grouped by Part, with search matches), the selected
 * section on the right, highlighted and scrolled to a cited passage. A 10-K or 10-Q also links
 * to the same section of the previous one and shows What changed side by side. */
export function FilingReader({ token, companyId, accessionNo, sectionKey, at, q, name, onAsk, onRead }: Props) {
  const filing = useLoad(`${companyId}|${accessionNo}`, () => parsedFiling(token, companyId, accessionNo));
  const f = filing?.state === "ok" ? filing.data : null;
  const active = f ? (f.sections.find((s) => s.section_key === sectionKey) ?? f.sections[0] ?? null) : null;
  const readRef = useRef(onRead);
  readRef.current = onRead;
  const readKey = f ? `${accessionNo}|${active?.section_key ?? ""}` : null;
  useEffect(() => {
    if (readKey) readRef.current?.(readKey.split("|")[1] || null);
  }, [readKey]);
  const text = useLoad(active ? `${accessionNo}|${active.section_key}` : null, () =>
    fetchSection(token, companyId, active!.section_key, accessionNo),
  );
  const form = f ? comparableForm(f.form) : null;
  const changes = useLoad(form ? accessionNo : null, () => sectionChanges(token, companyId, accessionNo));
  const prior = useLoad(form && f ? accessionNo : null, () =>
    filingIndex(token, companyId, { forms: [form!], to: f!.filed, limit: 5 }).then((r) => priorComparable(r.filings, f!)),
  );

  const [draft, setDraft] = useState(q ?? "");
  const [query, setQuery] = useState(q ?? "");
  useEffect(() => {
    setDraft(q ?? "");
    setQuery(q ?? "");
  }, [q, accessionNo]);
  const hits = useLoad(query && f ? `${accessionNo}|${query}` : null, () =>
    searchFilings(token, {
      q: query,
      company: companyId,
      form: SEARCH_FORMS.test(f!.form) ? f!.form : undefined,
      from: f!.filed,
      to: f!.filed,
      limit: 50,
    }).then((r) => r.results.filter((h) => h.citation.accession_no === accessionNo)),
  );
  const terms = searchTerms(query);
  // the section whose What changed is open (closes when another section opens)
  const [diffFor, setDiffFor] = useState<string | null>(null);

  const back = (
    <a className="back" href={href({ view: "company", companyId, tab: "filings" })}>
      ← {name ?? "Company"} filings
    </a>
  );
  if (!filing || filing.state === "loading") return <div className="page">{back}<Skeleton lines={8} label="Loading the filing" /></div>;
  if (filing.state === "denied") {
    return (
      <div className="page">
        {back}
        <p className="empty locked">
          Filing text needs filings access (the pro plan and a grant for this company). The filing
          index on the company's Filings tab links every filing to EDGAR.
        </p>
      </div>
    );
  }
  if (filing.state === "error" || !f) {
    return (
      <div className="page">
        {back}
        {filing.state === "error" ? (
          <ErrorPanel what="this filing" detail={`The Query API said: ${filing.message}.`} onRetry={filing.retry} />
        ) : (
          <p className="empty">
            Filing {accessionNo} hasn't been parsed into sections yet. The company's Filings tab
            links it on EDGAR in the meantime.
          </p>
        )}
      </div>
    );
  }

  const diff = changes?.state === "ok" ? changes.data.sections.find((s) => s.section_key === active?.section_key) : undefined;
  const diffOn = !!active && diffFor === active.section_key;
  const hitsBySection = new Map<string, SectionHit>(hits?.state === "ok" ? hits.data.map((h) => [h.section_key, h]) : []);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const words = draft.trim();
    go({ view: "filing", companyId, accessionNo, section: active?.section_key ?? null, ...(words ? { q: words } : {}) });
    setQuery(words);
  };
  const index = f.sections.findIndex((s) => s.section_key === active?.section_key);
  const neighbour = (i: number) => f.sections[i] && (
    <a href={href({ view: "filing", companyId, accessionNo, section: f.sections[i]!.section_key, ...(query ? { q: query } : {}) })}>
      {i < index ? "← " : ""}{f.sections[i]!.section_key} · {f.sections[i]!.title ?? "Untitled"}{i > index ? " →" : ""}
    </a>
  );

  return (
    <div className="page reader">
      {back}
      <header className="page-header">
        <div>
          <h1>
            <span className={`tag ${formTag(f.form)}`}>{f.form}</span> {name ? `${name}, ` : ""}filed {day(f.filed, true)}
          </h1>
          <p className="muted">
            <span className="mono">{accessionNo}</span>
            {f.period_of_report && ` · period ending ${day(f.period_of_report, true)}`}
          </p>
        </div>
        <div className="row wrap">
          {f.document_url?.startsWith("https://www.sec.gov/") && (
            <a className="btn sm ghost external" href={f.document_url} target="_blank" rel="noopener noreferrer">
              Original on EDGAR ↗
            </a>
          )}
          {onAsk && active && (
            <button className="btn sm" onClick={() => onAsk(`Summarize ${active.title ?? `section ${active.section_key}`} in this ${f.form}.`)}>
              Ask about this section
            </button>
          )}
        </div>
      </header>
      <div className="reader-tools row wrap">
        <form className="row grow" role="search" onSubmit={submit}>
          <input className="field" type="search" aria-label="Search this filing" placeholder="Search this filing…"
            value={draft} maxLength={200} onChange={(e) => setDraft(e.target.value)} />
          <button className="btn sm" type="submit">Search</button>
        </form>
        {form && active && <PriorLink prior={prior} form={form} companyId={companyId} sectionKey={active.section_key} />}
      </div>
      <div className="reader-body">
        <nav className="toc" aria-label="Sections">
          {outlineGroups(f.sections).map((g) => (
            <div key={g.label} className="grp">
              <div className="grp-h">{g.label}</div>
              {g.sections.map((s) => {
                const hit = hitsBySection.get(s.section_key);
                return (
                  <a
                    key={s.section_key}
                    href={href({ view: "filing", companyId, accessionNo, section: s.section_key, ...(query ? { q: query } : {}) })}
                    aria-current={s.section_key === active?.section_key ? "page" : undefined}
                  >
                    <span className="key">{s.section_key}</span>
                    <span className="grow">{s.title ?? "Untitled"}</span>
                    {hit ? (
                      <span className="cnt" title={`${hit.passages_matched} matching passage${hit.passages_matched === 1 ? "" : "s"}`}>
                        {hit.passages_matched}
                      </span>
                    ) : (
                      <span className="size faint">{size(s.chars)}</span>
                    )}
                  </a>
                );
              })}
            </div>
          ))}
          {query && <SearchHits hits={hits} query={query} companyId={companyId} accessionNo={accessionNo} />}
        </nav>
        <article className="section-text">
          {sectionKey && active && active.section_key !== sectionKey && (
            <p className="faint small">This filing has no section {sectionKey}; showing {active.section_key}.</p>
          )}
          {active === null && <p className="empty">This filing has no parsed sections.</p>}
          {active && (
            <div className="row wrap section-h">
              <h2 className="grow">
                {active.section_key} · {active.title ?? "Untitled"}
              </h2>
              {terms.length > 0 && text?.state === "ok" && text.data && (
                <span className="faint">
                  {countMatches(text.data.text, terms)} match{countMatches(text.data.text, terms) === 1 ? "" : "es"} for “{query}”
                </span>
              )}
              {diff && (
                <button className="btn sm" aria-pressed={diffOn} onClick={() => setDiffFor(diffOn ? null : active.section_key)}>
                  {diffOn ? "Back to text" : `What changed (${diff.summary.new + diff.summary.removed + diff.summary.rewritten})`}
                </button>
              )}
            </div>
          )}
          {active && diffOn && diff ? (
            <SideBySide diff={diff} companyId={companyId} />
          ) : (
            active && <SectionBody load={text} terms={terms} at={at} />
          )}
          {active && (
            <div className="row section-nav">
              <span className="grow">{neighbour(index - 1)}</span>
              <span>{neighbour(index + 1)}</span>
            </div>
          )}
        </article>
      </div>
    </div>
  );
}

function size(chars: number): string {
  return chars >= 1000 ? `${Math.round(chars / 1000)}k` : String(chars);
}

function Marked({ runs }: { runs: Run[] }) {
  return <>{runs.map((r, i) => (typeof r === "string" ? r : <mark key={i}>{r.mark}</mark>))}</>;
}

function SectionBody({ load, terms, at }: { load: Load<Awaited<ReturnType<typeof fetchSection>>> | null; terms: string[]; at?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const section = load?.state === "ok" ? load.data : null;
  const ps = section ? paragraphs(section.text) : [];
  const start = section?.citation.char_start;
  const target = at !== undefined && start !== undefined ? paragraphAt(ps, at - start) : -1;
  const beyond = section && at !== undefined && start !== undefined && at - start >= section.text.length;
  useEffect(() => {
    ref.current?.querySelector(".hit")?.scrollIntoView?.({ block: "center" });
  }, [section, target]);

  if (!load || load.state === "loading") return <Skeleton lines={6} label="Loading the section" />;
  if (load.state === "denied") return <p className="empty locked">This section isn't included in your access.</p>;
  if (load.state === "error" || !section) {
    return <ErrorPanel what="this section" onRetry={load.state === "error" ? load.retry : undefined} />;
  }
  return (
    <div ref={ref}>
      {ps.map((p, i) => (
        <p key={p.start} className={i === target ? "hit" : undefined}>
          <Marked runs={highlight(p.text, terms)} />
        </p>
      ))}
      {section.truncated && (
        <p className="muted small">
          Showing the first {section.text.length.toLocaleString()} of {section.total_chars.toLocaleString()} characters
          {beyond ? "; the cited passage is further on, in the original filing" : "; the rest is in the original filing"}.
        </p>
      )}
    </div>
  );
}

function SearchHits({ hits, query, companyId, accessionNo }: { hits: Load<SectionHit[]> | null; query: string; companyId: string; accessionNo: string }) {
  let body: ReactNode;
  if (!hits || hits.state === "loading") body = <p className="faint small">Searching…</p>;
  else if (hits.state === "denied") body = <p className="faint small">Searching needs filings access.</p>;
  else if (hits.state === "error") body = <p className="faint small error">{hits.message}</p>;
  else if (!hits.data.length) body = <p className="faint small">No passage in this filing matches. Search covers the indexed sections.</p>;
  else {
    body = (
      <ul className="hits">
        {hits.data.map((h) => (
          <li key={h.section_key}>
            <a href={href({ view: "filing", companyId, accessionNo, section: h.section_key, at: h.citation.char_start, q: query })}>
              <b>{h.section_key}</b> {h.title ?? ""}
            </a>
            <div className="snippet"><Marked runs={snippetRuns(h.snippet)} /></div>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="grp search-hits">
      <div className="grp-h">Matches for “{query}”</div>
      {body}
    </div>
  );
}

function PriorLink({ prior, form, companyId, sectionKey }: { prior: Load<Awaited<ReturnType<typeof priorComparable>>> | null; form: string; companyId: string; sectionKey: string }) {
  if (prior?.state !== "ok") return null;
  const p = prior.data;
  if (!p) return <span className="faint small">No earlier {form} on file</span>;
  const label = `Same section in the prior ${form} (filed ${day(p.filed, true)})`;
  if (p.sections_available) {
    return <a className="btn sm ghost" href={href({ view: "filing", companyId, accessionNo: p.accession_no, section: sectionKey })}>{label} →</a>;
  }
  const edgar = edgarLink(p);
  return edgar ? (
    <a className="btn sm ghost" href={edgar} target="_blank" rel="noopener noreferrer" title="Not parsed in Tributary">
      Prior {form} on EDGAR ↗
    </a>
  ) : null;
}

type DiffSection = SectionChanges["sections"][number];

/** What changed in this section since the comparable filing: prior on the left, this filing on
 * the right; rewritten paragraphs mark removed words on the left and added words on the right. */
function SideBySide({ diff, companyId }: { diff: DiffSection; companyId: string }) {
  const c = diff.compared_to;
  return (
    <div className="sbs-wrap">
      <div className="row wrap">
        <span className="tag q">{diff.summary.new} new</span>
        <span className="tag e">{diff.summary.rewritten} rewritten</span>
        <span className="tag warn">{diff.summary.removed} removed</span>
        <span className="faint">
          · {diff.summary.unchanged + diff.summary.minor_edits + diff.summary.numbers_only} unchanged or minor, hidden
        </span>
      </div>
      {diff.stale ? (
        <p className="faint">This filing was re-parsed since the comparison was made; it will be recomputed.</p>
      ) : diff.changes.length === 0 ? (
        <p className="empty">No substantive paragraph changes in this section.</p>
      ) : (
        <div className="sbs" role="table" aria-label="What changed">
          <div className="sbs-h" role="row">
            <span role="columnheader">
              <a href={href({ view: "filing", companyId, accessionNo: c.accession_no, section: c.section_key })}>
                Prior {c.form}, filed {day(c.filed, true)}
              </a>
            </span>
            <span role="columnheader">This filing</span>
          </div>
          {diff.changes.map((ch, i) => (
            <div key={i} className={`sbs-r ${ch.kind}`} role="row">
              <div role="cell" className="old">
                {ch.kind === "new" ? (
                  <span className="faint">not in the prior filing</span>
                ) : ch.kind === "rewritten" && ch.words ? (
                  ch.words.filter(([op]) => op !== "+").map(([op, t], j) => (op === "-" ? <del key={j}>{t} </del> : <span key={j}>{t} </span>))
                ) : (
                  ch.prior_text
                )}
              </div>
              <div role="cell" className="new">
                {ch.kind === "removed" ? (
                  <span className="faint">removed</span>
                ) : ch.kind === "rewritten" && ch.words ? (
                  ch.words.filter(([op]) => op !== "-").map(([op, t], j) => (op === "+" ? <ins key={j}>{t} </ins> : <span key={j}>{t} </span>))
                ) : (
                  ch.text
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="faint small">Paragraph-level comparison; changes to numbers alone are not shown.</div>
    </div>
  );
}
