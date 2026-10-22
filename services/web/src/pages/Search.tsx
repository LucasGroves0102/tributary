import { useEffect, useState, type FormEvent } from "react";

import { searchFilings } from "../api";
import { formTag } from "../company/data";
import { useLoad } from "../company/useLoad";
import { Skeleton } from "../components/States";
import { day } from "../format";
import { snippetRuns } from "../reader/text";
import { go, href } from "../route";
import { groupHits, SEARCH_FORMS, SEARCH_PAGE, type SearchForm, type SearchState } from "../search/state";
import type { Company } from "../types";

interface Props {
  token: string;
  search?: SearchState;
  /** the user's companies (/v1/me), for the company filter and the coverage note */
  companies: Company[];
}

/** Full-text search over the parsed sections of the user's companies (/v1/search): the best
 * passage per section, grouped by company and filing, each opening the reader at the passage. */
export function Search({ token, search, companies }: Props) {
  const readable = companies.filter((c) => c.tiers.includes("filings"));
  const [draft, setDraft] = useState<SearchState>(search ?? { q: "" });
  useEffect(() => setDraft(search ?? { q: "" }), [search]);
  const page = search?.page ?? 0;
  const key = search && readable.length ? JSON.stringify(search) : null;
  const load = useLoad(key, () =>
    searchFilings(token, {
      q: search!.q,
      form: search!.form,
      from: search!.from,
      to: search!.to,
      company: search!.company,
      limit: SEARCH_PAGE,
      offset: page * SEARCH_PAGE,
    }),
  );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = draft.q.trim();
    if (!q) return;
    go({ view: "search", search: { ...draft, q, page: undefined } });
  };
  const set = (patch: Partial<SearchState>) => setDraft((d) => ({ ...d, ...patch }));
  const toPage = (p: number) => search && go({ view: "search", search: { ...search, page: p || undefined } });

  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">Search filings</h1>
      </div>
      <form className="card card-b search-form" role="search" onSubmit={submit}>
        <div className="row wrap">
          <input className="field grow" type="search" aria-label="Search words" maxLength={200}
            placeholder={'Words, "an exact phrase", or, -excluded'} value={draft.q}
            onChange={(e) => set({ q: e.target.value })} />
          <button className="btn primary" type="submit" disabled={!draft.q.trim()}>Search</button>
        </div>
        <div className="row wrap">
          <label className="row">
            <span className="faint">Form</span>
            <select className="field narrow" aria-label="Form" value={draft.form ?? ""}
              onChange={(e) => set({ form: (e.target.value || undefined) as SearchForm | undefined })}>
              <option value="">Any</option>
              {SEARCH_FORMS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className="row">
            <span className="faint">Company</span>
            <select className="field narrow" aria-label="Company" value={draft.company ?? ""}
              onChange={(e) => set({ company: e.target.value || undefined })}>
              <option value="">All you can read</option>
              {readable.map((c) => <option key={c.company_id} value={c.company_id}>{c.name ?? c.company_id}</option>)}
            </select>
          </label>
          <label className="row">
            <span className="faint">Filed from</span>
            <input className="field narrow" type="date" aria-label="Filed from" value={draft.from ?? ""}
              onChange={(e) => set({ from: e.target.value || undefined })} />
          </label>
          <label className="row">
            <span className="faint">to</span>
            <input className="field narrow" type="date" aria-label="Filed to" value={draft.to ?? ""}
              onChange={(e) => set({ to: e.target.value || undefined })} />
          </label>
        </div>
        <p className="faint small">
          Searches the parsed sections (Risk Factors, MD&amp;A, 8-K items and press releases…) of
          the companies whose filings you can read: {readable.length
            ? `${readable.length} ${readable.length === 1 ? "company" : "companies"} on your plan`
            : "none on your plan"}. Other companies' filings aren't searched, and results never say
          whether they match.
        </p>
      </form>

      {!search ? (
        <p className="empty">Type words to search, then press Enter.</p>
      ) : !readable.length ? (
        <p className="empty locked">
          Searching filing text needs filings access (the pro plan and a grant for a company).
          Every company's filing index is still on its Filings tab.
        </p>
      ) : !load || load.state === "loading" ? (
        <Skeleton lines={5} label="Searching" />
      ) : load.state === "denied" ? (
        <p className="empty locked">That company's filings aren't included in your access.</p>
      ) : load.state === "error" ? (
        <p className="empty error">{load.message}</p>
      ) : load.data.results.length === 0 ? (
        <p className="empty">No passages match “{search.q}”{page ? " on this page" : ""}. Try fewer or different words.</p>
      ) : (
        <section className="card results">
          {groupHits(load.data.results).map((c) => (
            <div key={c.companyId} className="result-co">
              <h2>
                <a href={href({ view: "company", companyId: c.companyId, tab: "filings" })}>{c.name}</a>
              </h2>
              {c.filings.map((f) => (
                <div key={f.accessionNo} className="result-filing">
                  <div className="row wrap">
                    <span className={`tag ${formTag(f.form)}`}>{f.form}</span>
                    <span className="muted">filed {day(f.filed, true)}</span>
                    <a className="mono faint" href={href({ view: "filing", companyId: c.companyId, accessionNo: f.accessionNo, section: null, q: search.q })}>
                      {f.accessionNo}
                    </a>
                  </div>
                  <ul>
                    {f.hits.map((h) => (
                      <li key={h.section_key}>
                        <a href={href({ view: "filing", companyId: c.companyId, accessionNo: f.accessionNo, section: h.section_key, at: h.citation.char_start, q: search.q })}>
                          <b>{h.section_key}</b> {h.title ?? ""}
                        </a>
                        {h.passages_matched > 1 && <span className="faint small"> · {h.passages_matched} passages match</span>}
                        <div className="snippet">
                          {snippetRuns(h.snippet).map((r, i) => (typeof r === "string" ? r : <mark key={i}>{r.mark}</mark>))}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ))}
          {(page > 0 || load.data.has_more) && (
            <div className="pager">
              <span className="muted">
                {page * SEARCH_PAGE + 1}–{page * SEARCH_PAGE + load.data.results.length}
              </span>
              <button className="btn sm" disabled={page === 0} onClick={() => toPage(page - 1)}>Previous</button>
              <button className="btn sm" disabled={!load.data.has_more} onClick={() => toPage(page + 1)}>Next</button>
            </div>
          )}
          <div className="foot">
            Ranked by how densely the words occur in each passage; one passage per section. Data
            as of {day(load.data.as_of, true)}.
          </div>
        </section>
      )}
    </div>
  );
}
