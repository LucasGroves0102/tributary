/** The full-text search page's state, kept in the address ("#/search?q=export+controls&form=10-K")
 * so results can be linked and the back button works. */

import type { SectionHit } from "../types";

export const SEARCH_FORMS = ["10-K", "10-Q", "8-K"] as const;
export type SearchForm = (typeof SEARCH_FORMS)[number];

export interface SearchState {
  q: string;
  form?: SearchForm;
  /** filed on or after / on or before, YYYY-MM-DD */
  from?: string;
  to?: string;
  /** one company_id */
  company?: string;
  /** 0-based */
  page?: number;
}

export const SEARCH_PAGE = 20;
const MAX_Q = 200;
const MAX_PAGE = 50; // the API's offset cap (1000) / SEARCH_PAGE
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const CIK = /^\d{10}$/;

/** Query string → state; invalid or empty values are dropped (null: no query at all). */
export function parseSearch(query: string): SearchState | null {
  const p = new URLSearchParams(query);
  const q = p.get("q")?.trim().slice(0, MAX_Q);
  if (!q) return null;
  const out: SearchState = { q };
  const form = p.get("form");
  if (form && (SEARCH_FORMS as readonly string[]).includes(form)) out.form = form as SearchForm;
  for (const k of ["from", "to"] as const) {
    const v = p.get(k);
    if (v && DATE.test(v)) out[k] = v;
  }
  const company = p.get("company");
  if (company && CIK.test(company)) out.company = company;
  const page = Number(p.get("page"));
  if (Number.isInteger(page) && page > 0 && page < MAX_PAGE) out.page = page;
  return out;
}

export function searchQuery(s: SearchState): string {
  const p = new URLSearchParams({ q: s.q });
  if (s.form) p.set("form", s.form);
  if (s.from) p.set("from", s.from);
  if (s.to) p.set("to", s.to);
  if (s.company) p.set("company", s.company);
  if (s.page) p.set("page", String(s.page));
  return p.toString();
}

export interface FilingGroup {
  accessionNo: string;
  form: string;
  filed: string;
  hits: SectionHit[];
}

export interface CompanyGroup {
  companyId: string;
  name: string;
  filings: FilingGroup[];
}

/** Ranked hits grouped by company, then filing, each in order of its best hit. */
export function groupHits(hits: SectionHit[]): CompanyGroup[] {
  const companies: CompanyGroup[] = [];
  for (const h of hits) {
    let c = companies.find((x) => x.companyId === h.company_id);
    if (!c) companies.push((c = { companyId: h.company_id, name: h.name, filings: [] }));
    let f = c.filings.find((x) => x.accessionNo === h.citation.accession_no);
    if (!f) c.filings.push((f = { accessionNo: h.citation.accession_no, form: h.citation.form, filed: h.citation.filed, hits: [] }));
    f.hits.push(h);
  }
  return companies;
}
