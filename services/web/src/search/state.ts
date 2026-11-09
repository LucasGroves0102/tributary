/** The full-text search page's state, kept in the address ("#/search?q=export+controls&form=10-K")
 * so results can be linked and the back button works. */

import { day } from "../format";
import type { SectionHit, SectionSearch } from "../types";

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
  /** "date": newest filed first; absent: best match first (#145) */
  sort?: "date";
  /** every date, across every company: the API's `window=exact` without dates (#81) */
  allDates?: boolean;
  /** 0-based */
  page?: number;
}

/** What the API is asked for the dates (#81): the dates a reader picked are exactly the dates
 * searched (`exact`), and so is "All dates"; one company is always exact. Only an all-company
 * search with no dates is `auto`: the API ranks the newest window that fills a page and says
 * which (`searched_from`), because ranking every company's every filing can take too long. */
export function searchWindow(s: Pick<SearchState, "from" | "to" | "company" | "allDates">): "auto" | "exact" {
  return s.company || s.from || s.to || s.allDates ? "exact" : "auto";
}

/** The words are one quoted phrase: `"export controls"`. */
export function isPhrase(q: string): boolean {
  return /^"[^"]*\S[^"]*"$/.test(q.trim());
}

/** The words as one exact phrase, or back to plain words (the "Exact phrase" box, #80). Other
 * quotes and a leading minus go: a phrase is the words in order, nothing else. */
export function asPhrase(q: string, on: boolean): string {
  const words = q.replace(/"/g, " ").replace(/\s+/g, " ").trim();
  return on && words ? `"${words}"` : words;
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
  if (p.get("sort") === "date") out.sort = "date";
  if (p.get("dates") === "all") out.allDates = true;
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
  if (s.sort) p.set("sort", s.sort);
  if (s.allDates) p.set("dates", "all");
  if (s.page) p.set("page", String(s.page));
  return p.toString();
}

/** What was searched, in one sentence, from the API's answer rather than from what was asked (an
 * older API ignores `window` and `sort`): how many sections match, whose filings, which dates, in
 * which order. `widen` when the API chose a recent window itself, so the page can offer more. */
export function searchScope(
  s: SearchState,
  r: Pick<SectionSearch, "total" | "searched_from" | "sort">,
  companyName: string | null,
): { text: string; widen: boolean } {
  const n = r.total;
  const count = n == null ? "Sections" : `${n.toLocaleString("en-US")} ${n === 1 ? "section" : "sections"}`;
  const whose = s.company ? `${companyName ?? "this company"}'s filings` : "every company's filings";
  let when: string;
  if (r.searched_from) when = `filed since ${day(r.searched_from, true)}`;
  else if (s.from && s.to) when = `filed ${day(s.from, true)} to ${day(s.to, true)}`;
  else if (s.from) when = `filed since ${day(s.from, true)}`;
  else if (s.to) when = `filed up to ${day(s.to, true)}`;
  else when = "of any date";
  const order = r.sort === "date" ? "newest first" : "best match first";
  const verb = n === 1 ? "matches" : "match";
  const text = `${count} in ${whose} ${when} ${verb}, ${order}.`;
  return { text, widen: !!r.searched_from && !s.company };
}

/** "Filed from" after "filed to": no filing can match, so it's never asked and never blamed on
 * the words. */
export function datesInverted(s: Pick<SearchState, "from" | "to">): boolean {
  return !!s.from && !!s.to && s.from > s.to;
}

/** Whether a date field holds a date worth searching: while a year is being typed, the browser
 * reports years like 0002. EDGAR's electronic filings start in the 1990s. */
export function usableDate(v: string | undefined): boolean {
  return !v || (DATE.test(v) && v >= "1990-01-01");
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

/** Ranked hits grouped by company, then filing, each in order of its best hit. `inOrder` (newest
 * first): a group is only a run of consecutive hits, so the list stays in date order. */
export function groupHits(hits: SectionHit[], inOrder = false): CompanyGroup[] {
  const companies: CompanyGroup[] = [];
  for (const h of hits) {
    let c = inOrder
      ? (companies.at(-1)?.companyId === h.company_id ? companies.at(-1) : undefined)
      : companies.find((x) => x.companyId === h.company_id);
    if (!c) companies.push((c = { companyId: h.company_id, name: h.name, filings: [] }));
    let f = c.filings.find((x) => x.accessionNo === h.citation.accession_no);
    if (!f) c.filings.push((f = { accessionNo: h.citation.accession_no, form: h.citation.form, filed: h.citation.filed, hits: [] }));
    f.hits.push(h);
  }
  return companies;
}
