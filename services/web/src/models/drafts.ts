/** What the model page's fields hold as typed (strings, so "-" or "12." can be typed on the way to
 * a number) and how they read back as a ModelState. Blank rate fields are Tributary's defaults;
 * a field that doesn't read is flagged and the case keeps its last valid numbers on screen. */

import {
  ADJUSTMENT_NAME_MAX,
  GROWTH_RANGE,
  MARGIN_RANGE,
  MAX_ADJUSTMENTS,
  parseMillions,
  parsePercent,
  parsePrice,
  parseRate,
  RATE_RANGES,
  RATES,
  resizeAssumptions,
  type Adjustment,
  type AdjustmentKind,
  type Assumption,
  type Price,
  type Projected,
  type Rate,
} from "./model";

export type Draft = { growth: string; margin: string; note: string } & Record<Rate, string>;

export interface AdjustmentDraft {
  name: string;
  kind: AdjustmentKind;
  fy: string;
  quarter: string;
  /** $ millions */
  amount: string;
}

export interface PriceDraft {
  value: string;
  date: string;
}

const text = (v: number | undefined) => (v === undefined ? "" : String(v));

export const toDraft = (a: Assumption): Draft => ({
  growth: String(a.growth),
  margin: String(a.margin),
  note: a.note ?? "",
  nonOperating: text(a.nonOperating),
  taxRate: text(a.taxRate),
  shareChange: text(a.shareChange),
  fcfMargin: text(a.fcfMargin),
});

/** One typed year as numbers, or null while any field is out of range or not a number. */
export function parseDraft(d: Draft): Assumption | null {
  const growth = parsePercent(d.growth, GROWTH_RANGE);
  const margin = parsePercent(d.margin, MARGIN_RANGE);
  if (growth === null || margin === null) return null;
  const out: Assumption = { growth, margin };
  if (d.note.trim()) out.note = d.note.trim();
  for (const r of RATES) {
    const v = parseRate(d[r], RATE_RANGES[r]);
    if (v === null) return null;
    if (v !== undefined) out[r] = v;
  }
  return out;
}

/** The typed assumptions as numbers, or null while any is out of range or not a number. */
export function parseDrafts(drafts: Draft[]): Assumption[] | null {
  const out: Assumption[] = [];
  for (const d of drafts) {
    const a = parseDraft(d);
    if (!a) return null;
    out.push(a);
  }
  return out;
}

/** More or fewer forecast years for one case: through resizeAssumptions when it parses (new years
 * repeat the last one, notes kept per year), else cut or padded as typed. */
export function resizeDrafts(d: Draft[], n: number): Draft[] {
  const parsed = parseDrafts(d);
  if (!parsed) return d.length >= n ? d.slice(0, n) : [...d, ...Array.from({ length: n - d.length }, () => ({ ...d.at(-1)!, note: "" }))];
  const notes = d.map((x) => x.note);
  return resizeAssumptions(parsed, n).map((a, k) => ({ ...toDraft(a), note: notes[k] ?? "" }));
}

/** A new bear / bull case from the run's preview of it: its growth and margin, and the rates the
 * user set in the base case (the others stay blank: defaults), without notes. */
export function fromPreview(years: Projected[]): Draft[] {
  return years.map((p) => {
    const d: Draft = { growth: String(p.inputs.growth), margin: String(p.inputs.margin), note: "", nonOperating: "", taxRate: "", shareChange: "", fcfMargin: "" };
    for (const r of RATES) if (!p.defaulted.includes(r)) d[r] = String(p.inputs[r]);
    return d;
  });
}

export const toAdjustmentDraft = (a: Adjustment): AdjustmentDraft => ({
  name: a.name,
  kind: a.kind,
  fy: String(a.fy),
  quarter: String(a.quarter),
  amount: String(Math.round(a.amount / 1e4) / 100),
});

/** Why an adjustment as typed doesn't read, or null. */
export function adjustmentProblem(a: AdjustmentDraft): string | null {
  if (!a.name.trim()) return "name it";
  if (a.name.trim().length > ADJUSTMENT_NAME_MAX) return `at most ${ADJUSTMENT_NAME_MAX} characters`;
  if (!/^\d{4}$/.test(a.fy.trim()) || Number(a.fy) < 1990 || Number(a.fy) > 2100) return "a fiscal year, like 2027";
  if (!["1", "2", "3", "4"].includes(a.quarter)) return "a quarter, 1 to 4";
  if (parseMillions(a.amount) === null) return "an amount in $ millions, like 46 or -12.5";
  return null;
}

/** The typed adjustments, or null while any doesn't read. */
export function parseAdjustments(list: AdjustmentDraft[]): Adjustment[] | null {
  if (list.length > MAX_ADJUSTMENTS) return null;
  const out: Adjustment[] = [];
  for (const a of list) {
    if (adjustmentProblem(a)) return null;
    out.push({ name: a.name.trim(), kind: a.kind, fy: Number(a.fy), quarter: Number(a.quarter), amount: parseMillions(a.amount)! });
  }
  return out;
}

/** The typed price: undefined when both fields are blank, null while either doesn't read. */
export function parsePriceDraft(p: PriceDraft): Price | undefined | null {
  if (!p.value.trim() && !p.date.trim()) return undefined;
  const value = parsePrice(p.value);
  const ok = /^\d{4}-\d{2}-\d{2}$/.test(p.date) && !Number.isNaN(Date.parse(`${p.date}T00:00:00Z`))
    && new Date(`${p.date}T00:00:00Z`).toISOString().slice(0, 10) === p.date;
  return value !== null && ok ? { value, date: p.date } : null;
}
