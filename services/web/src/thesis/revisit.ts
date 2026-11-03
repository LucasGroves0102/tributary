/** Revisit: after the company's next 10-Q or 10-K, offer a re-check, and show what flipped
 * between two checks. Pure, so the rules are tested without rendering.
 *
 * A newer filing (`newerFiling`): the company's newest 10-K/10-Q in the SEC filing index
 * (`/v1/companies/{id}/filing-index?form=10-K&form=10-Q&limit=1`, public metadata) is newer than
 * the check when
 *   - the check recorded the index's newest 10-K/10-Q when it ran (`filings[].latest`, agent-api
 *     thesis_check.read_filings): the accession differs and it was filed on or after that one;
 *   - otherwise (checks stored before Revisit, or the index couldn't be read then): it was filed
 *     after the day the check ran (`checked_at`, else `created_at`, as a UTC date). A filing
 *     filed the same day as such a check isn't offered.
 *
 * What flipped (`diffChecks`, previous → current check):
 *   - claims are matched by quote, normalised like the highlighter (`check.ts` normalize: case,
 *     whitespace runs, curly quotes and dashes) without trailing punctuation; a quote that
 *     appears several times matches in order;
 *   - a matched claim whose status differs is "flipped" (supported → contradicted, ...);
 *     unmatched claims of the current check are "new", of the previous one "removed";
 *   - model assumptions are matched by model item id and fiscal year; a growth or margin flag
 *     that differs ("above" / "below" / none) is a changed flag; a model in one check only is
 *     added or removed;
 *   - points the thesis doesn't mention are counted, not matched (their wording changes run to
 *     run). */

import type { CheckFilingRef, CheckFlag, CheckModel, ClaimStatus, ThesisCheckResult } from "../types";
import { normalize } from "./check";

export const PERIODIC_FORMS = ["10-K", "10-Q"];

/** What newerFiling needs of a check: its summary from the history list, or the full check. */
export interface CheckBasis {
  checked_at?: string | null;
  created_at: string;
  company_id?: string | null;
  filings?: { company_id: string; latest?: CheckFilingRef | null }[];
}

/** The company's newest 10-K/10-Q (from the filing index) if it is newer than the check;
 * else null. */
export function newerFiling(check: CheckBasis, companyId: string, newest: CheckFilingRef | null | undefined): CheckFilingRef | null {
  if (!newest || !PERIODIC_FORMS.includes(newest.form)) return null;
  const latest = check.filings?.find((f) => f.company_id === companyId)?.latest;
  if (latest) {
    return newest.accession_no !== latest.accession_no && newest.filed >= latest.filed ? newest : null;
  }
  const day = (check.checked_at ?? check.created_at).slice(0, 10);
  return newest.filed > day ? newest : null;
}

/** The check's company: the thesis's company it checked, else the thesis's company now. */
export const checkCompany = (check: CheckBasis, current: string | null): string | null => check.company_id ?? current;

export interface Flip {
  quote: string;
  from: ClaimStatus;
  to: ClaimStatus;
}

export interface FlagChange {
  itemId: string;
  model: string;
  fy: number;
  what: "growth" | "margin";
  from: CheckFlag;
  to: CheckFlag;
}

export interface CheckDiff {
  flipped: Flip[];
  added: { quote: string; status: ClaimStatus }[];
  removed: { quote: string; status: ClaimStatus }[];
  /** matched claims with the same status */
  unchanged: number;
  flags: FlagChange[];
  modelsAdded: string[];
  modelsRemoved: string[];
  notMentioned: { before: number; after: number };
}

/** A quote as claims are matched: normalised, without trailing punctuation. */
export function quoteKey(quote: string): string {
  return normalize(quote).text.trim().replace(/[\s.,;:!?"')\]]+$/, "").replace(/^["'(\[]+/, "");
}

const flagOf = (f: CheckFlag | undefined): CheckFlag => f ?? null;

/** What changed from `prev` to `next` (rules in the module docstring). */
export function diffChecks(prev: ThesisCheckResult, next: ThesisCheckResult): CheckDiff {
  const pool = new Map<string, { quote: string; status: ClaimStatus }[]>();
  for (const c of prev.claims) {
    const k = quoteKey(c.quote);
    pool.set(k, [...(pool.get(k) ?? []), { quote: c.quote, status: c.status }]);
  }
  const flipped: Flip[] = [];
  const added: CheckDiff["added"] = [];
  let unchanged = 0;
  for (const c of next.claims) {
    const before = pool.get(quoteKey(c.quote))?.shift();
    if (!before) added.push({ quote: c.quote, status: c.status });
    else if (before.status !== c.status) flipped.push({ quote: c.quote, from: before.status, to: c.status });
    else unchanged++;
  }
  const removed = [...pool.values()].flat();

  const models = (r: ThesisCheckResult) => new Map((r.assumptions ?? []).map((m) => [m.item_id, m]));
  const a = models(prev);
  const b = models(next);
  const flags: FlagChange[] = [];
  for (const [itemId, m] of b) {
    const old = a.get(itemId);
    if (!old) continue;
    const years = new Map((old.years ?? []).map((y) => [y.fy, y]));
    for (const y of m.years ?? []) {
      const o = years.get(y.fy);
      if (!o) continue;
      for (const what of ["growth", "margin"] as const) {
        const from = flagOf(o[`${what}_flag`]);
        const to = flagOf(y[`${what}_flag`]);
        if (from !== to) flags.push({ itemId, model: m.name, fy: y.fy, what, from, to });
      }
    }
  }
  const names = (m: Map<string, CheckModel>, other: Map<string, CheckModel>) =>
    [...m.values()].filter((x) => !other.has(x.item_id)).map((x) => x.name);
  return {
    flipped,
    added,
    removed,
    unchanged,
    flags,
    modelsAdded: names(b, a),
    modelsRemoved: names(a, b),
    notMentioned: { before: prev.not_mentioned.length, after: next.not_mentioned.length },
  };
}

/** Nothing worth a line: no claim or flag changed, none added or removed. */
export function isQuiet(d: CheckDiff): boolean {
  return !d.flipped.length && !d.added.length && !d.removed.length && !d.flags.length && !d.modelsAdded.length && !d.modelsRemoved.length;
}

/** "above the CAGR" / "within the range": a flag in words. */
export function flagWords(what: "growth" | "margin", f: CheckFlag): string {
  if (!f) return what === "growth" ? "within 10 pts of the CAGR" : "within the 5-year range";
  return what === "growth" ? `${f} the CAGR` : `${f} the 5-year range`;
}
