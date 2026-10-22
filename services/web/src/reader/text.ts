/** The filing reader's pure parts: paragraphs with offsets, the outline's groups, search-term
 * highlighting and the prior comparable filing. Tested without rendering. */

import type { IndexedFiling } from "../types";

export interface Paragraph {
  /** offset of the paragraph's first character in the section text */
  start: number;
  end: number;
  text: string;
}

/** A section's text as paragraphs: blank lines, or a line break before a capital or a bullet
 * (the parser keeps one line per block). Offsets index into `text`. */
export function paragraphs(text: string): Paragraph[] {
  const out: Paragraph[] = [];
  const breaks = /\n{2,}|\n(?=[A-Z•])/g;
  let from = 0;
  const push = (end: number) => {
    const raw = text.slice(from, end);
    const lead = raw.length - raw.trimStart().length;
    const body = raw.trim();
    if (body) out.push({ start: from + lead, end: from + lead + body.length, text: body });
  };
  for (const m of text.matchAll(breaks)) {
    push(m.index!);
    from = m.index! + m[0].length;
  }
  push(text.length);
  return out;
}

/** The paragraph holding a section-relative character offset (or the nearest after it). */
export function paragraphAt(ps: Paragraph[], offset: number): number {
  const i = ps.findIndex((p) => offset < p.end);
  return i < 0 ? -1 : i;
}

export interface OutlineSection {
  section_key: string;
  title: string | null;
  chars: number;
}

/** Sections grouped as the filing is: 10-K Items into Parts I-IV, 10-Q "I-"/"II-" keys into
 * Parts I-II, 8-K items, then exhibits. Order within a group is the filing's. */
export function outlineGroups(sections: OutlineSection[]): { label: string; sections: OutlineSection[] }[] {
  const groups: { label: string; sections: OutlineSection[] }[] = [];
  for (const s of sections) {
    const label = groupOf(s.section_key);
    const g = groups.find((x) => x.label === label);
    if (g) g.sections.push(s);
    else groups.push({ label, sections: [s] });
  }
  return groups;
}

const TEN_K_PARTS: [number, string][] = [
  [4, "Part I"],
  [9, "Part II"],
  [14, "Part III"],
  [99, "Part IV"],
];

function groupOf(key: string): string {
  if (key.startsWith("EX-")) return "Exhibits";
  const part = /^(I{1,3}|IV)-/.exec(key);
  if (part) return `Part ${part[1]}`;
  if (/^\d\.\d\d$/.test(key)) return "Items";
  const item = /^(\d{1,2})[A-C]?$/.exec(key);
  if (item) return TEN_K_PARTS.find(([last]) => Number(item[1]) <= last)![1];
  return "Other";
}

/** Words a search highlights: quoted phrases and plain words (2+ characters), not "or" and not
 * excluded words ("-word"), as Postgres websearch syntax reads them. */
export function searchTerms(q: string): string[] {
  const terms: string[] = [];
  for (const m of q.matchAll(/(-?)"([^"]+)"|(-?)(\S+)/g)) {
    const excluded = (m[1] ?? m[3]) === "-";
    const term = (m[2] ?? m[4] ?? "").replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
    if (excluded || term.length < 2 || term.toLowerCase() === "or") continue;
    terms.push(term);
  }
  return [...new Set(terms)].slice(0, 10);
}

export type Run = string | { mark: string };

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Text split into plain runs and matches of the terms (case-insensitive, word starts, so
 * "export" also marks "exports" as the stemmed search does). */
export function highlight(text: string, terms: string[]): Run[] {
  if (!terms.length) return [text];
  const pattern = new RegExp(`\\b(?:${terms.map((t) => escape(t).replace(/\s+/g, "\\s+")).join("|")})\\w*`, "gi");
  const out: Run[] = [];
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    out.push({ mark: m[0] });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function countMatches(text: string, terms: string[]): number {
  return highlight(text, terms).filter((r) => typeof r !== "string").length;
}

/** A search snippet ("... «export» controls ...") as runs; the marks come from the Query API's
 * ts_headline, the text from the filing. */
export function snippetRuns(snippet: string): Run[] {
  const out: Run[] = [];
  for (const part of snippet.split(/(«[^»]*»)/)) {
    if (!part) continue;
    out.push(part.startsWith("«") && part.endsWith("»") ? { mark: part.slice(1, -1) } : part);
  }
  return out;
}

/** The form whose previous filing is "comparable": the same periodic form, originals only. */
export function comparableForm(form: string): string | null {
  const base = form.replace(/\/A$/, "");
  return base === "10-K" || base === "10-Q" ? base : null;
}

/** The filing before this one of the same form (an original), from the index; filed the same
 * day counts when its accession number is lower. Whether it was parsed is the caller's to show. */
export function priorComparable(
  index: IndexedFiling[],
  current: { accession_no: string; form: string; filed: string },
): IndexedFiling | null {
  const form = comparableForm(current.form);
  if (!form) return null;
  const before = (f: IndexedFiling) =>
    f.filed < current.filed || (f.filed === current.filed && f.accession_no < current.accession_no);
  const earlier = index.filter((f) => f.form === form && f.accession_no !== current.accession_no && before(f));
  earlier.sort((a, b) => b.filed.localeCompare(a.filed) || b.accession_no.localeCompare(a.accession_no));
  return earlier[0] ?? null;
}
