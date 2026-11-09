/** What changed as the screens word it (Query API `/changes` with `view`, F3; the rules are
 * query_api/changes.py's: what is hidden, a passage's period, figures, score and why). Pure.
 * Nothing here re-derives a rule; it only words and orders what the API returned:
 *
 * - The comparison: a 10-Q opens against the same quarter a year earlier (`defaultBasis`), a
 *   10-K against the previous 10-K. When the year-ago 10-Q isn't indexed the page falls back to
 *   the previous one once and says so (`fallBack`).
 * - Ranked: every section's changes in one list, highest score first (`ranked`); reading order
 *   keeps the API's order, section by section.
 * - What the pass left out is one sentence of counts (`hiddenLine`), never a list.
 * - A period reads "3 months", "6 months, year to date", "3 and 6 months" (`periodLabel`).
 * - A topic is highlighted with the API's own `filters.terms` (`marks`): a word prefix, or a
 *   phrase as written, case ignored; React text only, never HTML. */

import { day } from "../format";
import { sectionName } from "../reader/sections";
import type { ChangeComparison, SectionChanges } from "../types";

export type Basis = "prior" | "year_ago";
export type ChangedSection = SectionChanges["sections"][number];
export type Change = ChangedSection["changes"][number];
export interface Placed {
  change: Change;
  section: ChangedSection;
}

/** A 10-Q against the same quarter a year earlier; anything else against the prior filing. */
export const defaultBasis = (form: string | null | undefined): Basis => (form?.startsWith("10-Q") ? "year_ago" : "prior");

/** The API ran the materiality pass (an older one answers without `view`). */
export const isMaterial = (d: SectionChanges): boolean => d.view !== undefined;

/** Fall back to the prior filing when a year-ago answer has nothing to show and the user didn't
 * ask for it: the API says what it can compare with (`comparisons`), an older one doesn't, so an
 * empty answer from it falls back too. */
export function fallBack(d: SectionChanges, basis: Basis, chosen: boolean): boolean {
  if (chosen || basis !== "year_ago" || d.basis !== "year_ago") return false;
  if (d.comparisons) return !d.comparisons.some((c) => c.basis === "year_ago");
  return d.sections.length === 0;
}

/** Every section's changes as one list: by score for `ranked`, else in the API's order. */
export function placed(d: SectionChanges, view: "reading" | "ranked"): Placed[] {
  const all = d.sections.flatMap((section) => section.changes.map((change) => ({ change, section })));
  if (view !== "ranked") return all;
  return all.map((p, i) => [p, i] as const)
    .sort(([a, i], [b, j]) => (b.change.score ?? 0) - (a.change.score ?? 0) || i - j)
    .map(([p]) => p);
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Not shown: 9 calendar rollovers, 2 paragraphs only split or rejoined, ..." or null. */
export function hiddenLine(d: SectionChanges): string | null {
  const sum = { calendar: 0, resplit: 0, figures_only: 0, minor: 0 };
  for (const s of d.sections) {
    const h = s.material?.hidden;
    if (!h) continue;
    sum.calendar += h.calendar;
    sum.resplit += h.resplit;
    sum.figures_only += h.figures_only;
    sum.minor += h.minor;
  }
  const parts = [
    sum.calendar ? plural(sum.calendar, "calendar rollover") + " (dates, “second” to “third” quarter)" : null,
    sum.resplit ? plural(sum.resplit, "paragraph") + " only split, rejoined or page furniture" : null,
    sum.figures_only ? plural(sum.figures_only, "table row") + " whose figures alone changed" : null,
    sum.minor ? plural(sum.minor, "minor edit") : null,
  ].filter((x): x is string => x !== null);
  return parts.length ? `Not shown: ${parts.join(", ")}.` : null;
}

const PERIOD: Record<string, string> = {
  "three months": "3 months",
  "six months": "6 months, year to date",
  "nine months": "9 months, year to date",
  "three and six months": "3 and 6 months",
  "three and nine months": "3 and 9 months",
  year: "the year",
};

/** "3 months", "6 months, year to date"; null when the passage names no period. */
export function periodLabel(p: string | null | undefined): string | null {
  if (!p) return null;
  return PERIOD[p] ?? p;
}

/** "Same quarter a year earlier" / "Previous 10-Q", with the compared filing's dates. */
export function comparisonLabel(basis: Basis, form: string | null | undefined): string {
  if (basis === "year_ago") return "Same quarter a year earlier";
  return `Previous ${form?.startsWith("10-K") ? "10-K" : form?.startsWith("10-Q") ? "10-Q" : "filing"}`;
}

/** "the 10-Q for the quarter ended Sep 30, 2025, filed Oct 31, 2025" */
export function comparedWith(c: Pick<ChangeComparison, "form" | "filed" | "period_of_report">): string {
  const what = c.form.startsWith("10-K") ? "year" : "quarter";
  return c.period_of_report
    ? `the ${c.form} for the ${what} ended ${day(c.period_of_report, true)}, filed ${day(c.filed, true)}`
    : `the ${c.form} filed ${day(c.filed, true)}`;
}

/** A section's short name: "Risk Factors", "MD&A". */
export const sectionShort = (key: string): string => {
  const name = sectionName(key);
  return /management/i.test(name) || /^(7|I-2)$/.test(key) ? "MD&A" : /risk/i.test(name) ? "Risk Factors" : name;
};

/** "Showing 10 of 91 changes", with the topic and the new-figures filter in words. */
export function countLine(d: SectionChanges, shown: number): string {
  const total = d.total ?? shown;
  const matched = d.matched ?? shown;
  const f = d.filters;
  const what = [f?.q ? `about “${f.q}”` : null, f?.numbers === "new" ? "with new figures" : null]
    .filter(Boolean).join(" and ");
  const of = matched === total ? plural(total, "change") : `${matched} of ${plural(total, "change")}`;
  return `${of}${what ? ` ${what}` : ""}${shown < matched ? ` (the first ${shown} shown)` : ""}`;
}

/** Text split at the API's topic terms: [text, matched] runs for <mark>. */
export function marks(text: string, terms: string[]): [string, boolean][] {
  if (!terms.length || !text) return [[text, false]];
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
  const re = new RegExp(`(?<![\\w])(${escaped.map((t) => `${t}[\\w'-]*`).join("|")})`, "gi");
  const out: [string, boolean][] = [];
  let at = 0;
  for (const m of text.matchAll(re)) {
    if (m.index! > at) out.push([text.slice(at, m.index), false]);
    out.push([m[0], true]);
    at = m.index! + m[0].length;
  }
  if (at < text.length) out.push([text.slice(at), false]);
  return out;
}
