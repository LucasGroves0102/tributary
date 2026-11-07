import { useEffect, useMemo, useState, type FormEvent } from "react";

import { searchCompanies, searchFilings } from "../api";
import { formTag } from "../company/data";
import { useLoad } from "../company/useLoad";
import { CompanyPicker, type PickedCompany } from "../components/CompanySearch";
import { Skeleton } from "../components/States";
import { day, UNNAMED } from "../format";
import { snippetRuns } from "../reader/text";
import { go, href } from "../route";
import { datesInverted, groupHits, SEARCH_FORMS, SEARCH_PAGE, usableDate, type SearchForm, type SearchState } from "../search/state";
import type { Company } from "../types";

interface Props {
  token: string;
  search?: SearchState;
  /** the user's companies (/v1/me), for the company filter and the coverage note */
  companies: Company[];
  /** every company's filings are readable (the public catalog, access.ts) */
  open?: boolean;
  /** offered in the company filter with nothing typed (the watchlist) */
  suggestions?: PickedCompany[];
}

const NONE: PickedCompany[] = [];

/** Full-text search over the parsed sections of the user's companies (/v1/search): the best
 * passage per section, grouped by company and filing, each opening the reader at the passage. */
export function Search({ token, search, companies, open = false, suggestions = NONE }: Props) {
  const readable = companies.filter((c) => c.tiers.includes("filings"));
  const any = open || readable.length > 0;
  const [draft, setDraft] = useState<SearchState>(search ?? { q: "" });
  useEffect(() => setDraft(search ?? { q: "" }), [search]);
  const page = search?.page ?? 0;
  const inverted = datesInverted(draft);
  const key = search && any && !datesInverted(search) ? JSON.stringify(search) : null;
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
    if (!q || inverted) return;
    go({ view: "search", search: { ...draft, q, page: undefined } });
  };
  /** The words are searched on Enter or the button (each search is a ranking of every match);
   * a filter applies as soon as it changes, like every other filter in the app. */
  const filter = (patch: Partial<SearchState>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    const q = next.q.trim() || search?.q;
    if (q && !datesInverted(next) && usableDate(next.from) && usableDate(next.to)) {
      go({ view: "search", search: { ...next, q, page: undefined } });
    }
  };
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
            onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))} />
          <button className="btn primary" type="submit" disabled={!draft.q.trim()}>Search</button>
        </div>
        <div className="row wrap">
          <label className="row">
            <span className="faint">Form</span>
            <select className="field narrow" aria-label="Form" value={draft.form ?? ""}
              onChange={(e) => filter({ form: (e.target.value || undefined) as SearchForm | undefined })}>
              <option value="">Any</option>
              {SEARCH_FORMS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <div className="row search-company">
            <span className="faint">Company</span>
            <CompanyFilter token={token} value={draft.company} open={open} companies={readable} suggestions={suggestions}
              set={(company) => filter({ company })} />
          </div>
          <label className="row">
            <span className="faint">Filed from</span>
            <input className="field narrow" type="date" aria-label="Filed from" value={draft.from ?? ""}
              onChange={(e) => filter({ from: e.target.value || undefined })} />
          </label>
          <label className="row">
            <span className="faint">to</span>
            <input className="field narrow" type="date" aria-label="Filed to" value={draft.to ?? ""}
              onChange={(e) => filter({ to: e.target.value || undefined })} />
          </label>
        </div>
        {inverted && (
          <p className="error small" role="alert">
            “Filed from” is after “to”, so no filing could match.{" "}
            <button type="button" className="btn sm" onClick={() => filter({ from: draft.to, to: draft.from })}>
              Swap the dates
            </button>
          </p>
        )}
        <p className="faint small">
          Searches the parsed sections (Risk Factors, MD&amp;A, 8-K items and press releases…) of
          {open ? (
            "every company with parsed filings."
          ) : (
            <>
              the companies whose filings you can read: {readable.length
                ? `${readable.length} ${readable.length === 1 ? "company" : "companies"} on your plan`
                : "none on your plan"}. Other companies' filings aren't searched, and results never
              say whether they match.
            </>
          )}
        </p>
      </form>

      {!search ? (
        <p className="empty">Type words to search, then press Enter.</p>
      ) : datesInverted(search) ? (
        <p className="empty">Nothing searched yet: the dates above are the wrong way round.</p>
      ) : !any ? (
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
        <p className="empty">
          No passages match “{search.q}”{page ? " on this page" : ""}
          {search.form || search.company || search.from || search.to ? (
            <>
              {" "}with these filters.{" "}
              <button className="btn sm" onClick={() => go({ view: "search", search: { q: search.q } })}>Clear the filters</button>
            </>
          ) : (
            ". Try fewer or different words."
          )}
        </p>
      ) : (
        <section className="card results">
          {load.data.searched_from && (
            <p className="foot searched-from">
              Best matches in filings since {day(load.data.searched_from, true)}. Pick a company or
              dates to search further back.
            </p>
          )}
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

/** One company or all of them: the shared search, narrowed to what the user can read. A company
 * in the address (a link, Back) is named from the user's list or asked once. */
function CompanyFilter({ token, value, open, companies, suggestions, set }: {
  token: string;
  value: string | undefined;
  open: boolean;
  companies: Company[];
  suggestions: PickedCompany[];
  set: (company: string | undefined) => void;
}) {
  const [named, setNamed] = useState<PickedCompany | null>(null);
  const readable = useMemo(() => {
    const ids = new Set(companies.map((c) => c.company_id));
    return { has: (id: string) => open || ids.has(id) };
  }, [open, companies]);
  const mine = useMemo(
    () => companies.map((c): PickedCompany => ({ companyId: c.company_id, name: c.name ?? UNNAMED })),
    [companies],
  );
  const offered = useMemo(
    () => [...new Map([...suggestions, ...mine].map((c) => [c.companyId, c])).values()],
    [suggestions, mine],
  );
  useEffect(() => {
    if (!value || named?.companyId === value) return;
    const known = offered.find((c) => c.companyId === value);
    if (known) {
      setNamed(known);
      return;
    }
    let live = true;
    searchCompanies(token, value, false, 1)
      .then((rs) => {
        const r = rs.find((x) => x.company_id === value);
        if (live && r) setNamed({ companyId: value, name: r.name, ticker: r.tickers?.[0] ?? null });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [token, value, named, offered]);
  const shown = value ? (named?.companyId === value ? named : { companyId: value, name: value }) : null;
  return (
    <CompanyPicker token={token} label="Company" value={shown} emptyLabel={open ? "All companies" : "All you can read"}
      placeholder="Name or ticker" readable={readable} suggestions={offered}
      onChange={(c) => {
        setNamed(c);
        set(c.companyId);
      }}
      onClear={() => set(undefined)} />
  );
}
