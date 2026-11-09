/** Models: a forecast saved as a workspace item of kind "model" (app DB 009/015, validated by
 * query_api/workspaces.py ModelState). The item stores only what the user typed: the company, how
 * many fiscal years of history to show and, per forecast year and case, revenue growth and
 * operating margin (percent), four optional rates (interest and other, tax rate, share change,
 * FCF margin; blank = the default from history) and a note; since F2 also `ytd` (year 1 is the
 * current fiscal year's reported quarters plus the remainder), named `adjustments` and the user's
 * own `price`.
 *
 * The maths are not here. Since F2 the Query API runs a model (`POST /v1/companies/{id}/model`,
 * `api.ts` runModel): query_api/model_maths.py is the one copy, which the thesis check and later
 * MCP call too, and its docstring is the spec (history, current year, defaults, projection,
 * one-offs, valuation). This module:
 * - restores a saved state leniently (`restoreModel`, untrusted JSON) and writes the saved form
 *   (`modelState`: a model without F2's fields saves byte-identical to before);
 * - types the run's answer (`ModelRun`) and turns it into the chart (`modelSeries`) and the
 *   three-case table (`modelTable`: every line, bear / base / bull side by side);
 * - places the company's outlook beside the forecast year it covers (`guidanceYear`), as a
 *   reference the user reads, never an input. */

import type { Series, Source } from "../chart/series";
import { money } from "../format";
import type { EarningsQuarter, EarningsResponse } from "../types";

export const MAX_YEARS = 10;
export const DEFAULT_YEARS = 3;
export const MIN_HISTORY = 3;
export const MAX_HISTORY = 10;
export const DEFAULT_HISTORY = 8;
export const NOTE_MAX = 500;
export const GROWTH_RANGE = [-100, 1000] as const;
export const MARGIN_RANGE = [-1000, 100] as const;
export const MAX_ADJUSTMENTS = 20;
export const ADJUSTMENT_NAME_MAX = 80;
/** a price per share the API accepts */
export const PRICE_MAX = 10_000_000;

const CIK = /^\d{10}$/;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The per-year rates beyond growth and margin (F2), in percent; absent = the default. */
export const RATES = ["nonOperating", "taxRate", "shareChange", "fcfMargin"] as const;
export type Rate = (typeof RATES)[number];
export const RATE_RANGES: Record<Rate, readonly [number, number]> = {
  nonOperating: [-100, 100],
  taxRate: [-100, 100],
  shareChange: [-50, 100],
  fcfMargin: [-1000, 100],
};
export const RATE_LABELS: Record<Rate, string> = {
  nonOperating: "Interest and other",
  taxRate: "Tax rate",
  shareChange: "Share change",
  fcfMargin: "FCF margin",
};

export interface Assumption {
  /** revenue growth over the year before, percent (a ytd year 1: the remainder's) */
  growth: number;
  /** operating margin, percent */
  margin: number;
  note?: string;
  nonOperating?: number;
  taxRate?: number;
  shareChange?: number;
  fcfMargin?: number;
}

export type AdjustmentKind = "one_off" | "acquired";
export interface Adjustment {
  name: string;
  fy: number;
  quarter: number;
  kind: AdjustmentKind;
  /** USD: a one-off in operating income (positive raised it), or acquired revenue */
  amount: number;
}

export interface Price {
  value: number;
  /** YYYY-MM-DD */
  date: string;
  /** the price's currency (057): absent is dollars, as every price saved before */
  currency?: string;
}

export const SCENARIOS = ["bear", "base", "bull"] as const;
export type Scenario = (typeof SCENARIOS)[number];
export const SCENARIO_LABELS: Record<Scenario, string> = { bear: "Bear", base: "Base", bull: "Bull" };
/** How the Query API starts a new bear / bull case from the base case (worded on the page; the
 * numbers come from the run's preview): half the growth, at least 5 points; a fifth of the
 * margin, at least 3. */
export const SCENARIO_SPREAD = { growth: { share: 0.5, min: 5 }, margin: { share: 0.2, min: 3 } } as const;
/** the most growth a default fades toward, percent (worded on the page; the API applies it) */
export const LONG_RUN_GROWTH_CAP = 5;

export interface ModelState {
  companyId: string;
  historyYears: number;
  /** the base case */
  assumptions: Assumption[];
  /** bear / bull cases for the same years; absent: base only */
  scenarios?: { bear?: Assumption[]; bull?: Assumption[] };
  /** year 1 = the current fiscal year's reported quarters plus the remainder (F2) */
  ytd?: boolean;
  adjustments?: Adjustment[];
  price?: Price;
}

const clamp = (v: number, [lo, hi]: readonly [number, number]) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const round2 = (v: number) => Math.round(v * 100) / 100;

/** Per-year assumptions from untrusted JSON: entries with finite growth and margin, the first
 * MAX_YEARS, notes trimmed (blank ones dropped), rates kept when finite. Not clamped
 * (restoreModel clamps). */
function years(raw: unknown): Assumption[] {
  return (Array.isArray(raw) ? raw : []).slice(0, MAX_YEARS).flatMap((x) => {
    const a = isObject(x) ? x : null;
    const growth = num(a?.growth);
    const margin = num(a?.margin);
    if (growth === null || margin === null) return [];
    const note = typeof a?.note === "string" ? a.note.trim().slice(0, NOTE_MAX) : "";
    const rates: Partial<Record<Rate, number>> = {};
    for (const r of RATES) {
      const v = num(a?.[r]);
      if (v !== null) rates[r] = v;
    }
    return [{ growth, margin, ...(note ? { note } : {}), ...rates }];
  });
}

/** One case's assumptions from a saved model state (untrusted): base = `assumptions`; bear and
 * bull from `scenarios`, only with as many (valid) years as the base case; null when there is
 * no such case. */
export function scenarioAssumptions(raw: unknown, scenario: Scenario): Assumption[] | null {
  if (!isObject(raw) || !SCENARIOS.includes(scenario)) return null;
  const base = years(raw.assumptions);
  if (!base.length) return null;
  if (scenario === "base") return base;
  const cases = isObject(raw.scenarios) ? raw.scenarios : {};
  const list = cases[scenario];
  const out = years(list);
  return out.length && Array.isArray(list) && list.length === out.length && out.length === base.length ? out : null;
}

const clampYear = (a: Assumption): Assumption => {
  const out: Assumption = { ...a, growth: clamp(a.growth, GROWTH_RANGE), margin: clamp(a.margin, MARGIN_RANGE) };
  for (const r of RATES) if (out[r] !== undefined) out[r] = clamp(out[r]!, RATE_RANGES[r]);
  return out;
};

function adjustments(raw: unknown): Adjustment[] {
  return (Array.isArray(raw) ? raw : []).slice(0, MAX_ADJUSTMENTS).flatMap((x) => {
    if (!isObject(x)) return [];
    const name = typeof x.name === "string" ? x.name.trim().slice(0, ADJUSTMENT_NAME_MAX) : "";
    const fy = num(x.fy);
    const quarter = num(x.quarter);
    const amount = num(x.amount);
    const kind: AdjustmentKind = x.kind === "acquired" ? "acquired" : "one_off";
    if (!name || fy === null || quarter === null || amount === null || ![1, 2, 3, 4].includes(quarter)) return [];
    return [{ name, fy: Math.round(fy), quarter, kind, amount }];
  });
}

function price(raw: unknown): Price | null {
  if (!isObject(raw)) return null;
  const value = num(raw.value);
  return value !== null && value > 0 && value <= PRICE_MAX && typeof raw.date === "string" && ISO_DAY.test(raw.date)
    ? { value, date: raw.date }
    : null;
}

/** A model's state from the API (untrusted) as a ModelState, or null without a company. */
export function restoreModel(raw: unknown): ModelState | null {
  const o = isObject(raw) ? raw : null;
  if (!o || typeof o.companyId !== "string" || !CIK.test(o.companyId)) return null;
  const hy = num(o.historyYears);
  const assumptions = (scenarioAssumptions(o, "base") ?? []).map(clampYear);
  const bear = scenarioAssumptions(o, "bear")?.map(clampYear);
  const bull = scenarioAssumptions(o, "bull")?.map(clampYear);
  const adj = adjustments(o.adjustments);
  const p = price(o.price);
  return {
    companyId: o.companyId,
    historyYears: hy === null ? DEFAULT_HISTORY : Math.round(clamp(hy, [MIN_HISTORY, MAX_HISTORY])),
    assumptions: assumptions.length ? assumptions : [{ growth: 0, margin: 0 }],
    ...(bear || bull ? { scenarios: { ...(bear ? { bear } : {}), ...(bull ? { bull } : {}) } } : {}),
    ...(o.ytd === true ? { ytd: true } : {}),
    ...(adj.length ? { adjustments: adj } : {}),
    ...(p ? { price: p } : {}),
  };
}

const savedYears = (list: Assumption[]) =>
  list.map((a) => {
    const note = a.note?.trim();
    const out: Record<string, unknown> = { growth: round2(a.growth), margin: round2(a.margin) };
    if (note) out.note = note.slice(0, NOTE_MAX);
    for (const r of RATES) if (a[r] !== undefined) out[r] = round2(a[r]!);
    return out;
  });

/** The state as saved: blank notes dropped, numbers rounded to 0.01; bear / bull, ytd,
 * adjustments and price only when set (a model without them saves exactly as before F2). */
export function modelState(m: ModelState): Record<string, unknown> {
  const bear = m.scenarios?.bear;
  const bull = m.scenarios?.bull;
  return {
    companyId: m.companyId,
    historyYears: m.historyYears,
    assumptions: savedYears(m.assumptions),
    ...(bear || bull
      ? { scenarios: { ...(bear ? { bear: savedYears(bear) } : {}), ...(bull ? { bull: savedYears(bull) } : {}) } }
      : {}),
    ...(m.ytd ? { ytd: true } : {}),
    ...(m.adjustments?.length
      ? { adjustments: m.adjustments.map((a) => ({ name: a.name.trim(), fy: a.fy, quarter: a.quarter, kind: a.kind, amount: Math.round(a.amount) })) }
      : {}),
    ...(m.price ? { price: { value: round2(m.price.value), date: m.price.date } } : {}),
  };
}

/** The assumptions of one case of a model (base when the case isn't set). */
export function caseOf(m: Pick<ModelState, "assumptions" | "scenarios">, scenario: Scenario): Assumption[] {
  return scenario === "base" ? m.assumptions : (m.scenarios?.[scenario] ?? m.assumptions);
}

/** More or fewer forecast years: new years repeat the last one's numbers (without its note). */
export function resizeAssumptions(list: Assumption[], n: number): Assumption[] {
  const count = Math.min(MAX_YEARS, Math.max(1, n));
  if (list.length >= count) return list.slice(0, count);
  const last = list.at(-1) ?? { growth: 0, margin: 0 };
  const { note: _note, ...repeat } = last;
  return [...list, ...Array.from({ length: count - list.length }, () => ({ ...repeat }))];
}

export const fyLabel = (fy: number, forecast = false) => `FY${fy}${forecast ? "E" : ""}`;

/** "5-yr CAGR 8.1%" / "5-yr avg 22.0%", or why there's none. */
export function statLabel(value: number | null, years: number, what: "CAGR" | "avg"): string {
  if (value === null || years === 0) return `no ${what === "CAGR" ? "growth" : "margin"} history`;
  return `${years}-yr ${what} ${(value * 100).toFixed(1)}%`;
}

/** A typed percentage: a number in range, or null (the field is flagged). */
export function parsePercent(text: string, range: readonly [number, number]): number | null {
  const t = text.trim().replace(/%$/, "").trim();
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= range[0] && v <= range[1] ? v : null;
}

/** A typed rate: blank is the default (undefined), else a number in range, or null (flagged). */
export function parseRate(text: string, range: readonly [number, number]): number | undefined | null {
  return text.trim() === "" ? undefined : parsePercent(text, range);
}

/** A typed amount in $ millions ("46", "-12.5", "1,200") as USD, or null. */
export function parseMillions(text: string): number | null {
  const t = text.trim().replace(/^\$/, "").replace(/,/g, "").replace(/\s*m$/i, "");
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t) * 1e6;
  return Math.abs(v) <= 1e13 ? v : null;
}

/** A typed price per share, or null. */
export function parsePrice(text: string): number | null {
  const t = text.trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v > 0 && v <= PRICE_MAX ? v : null;
}

// --- the run's answer (query_api/model_maths.py) --------------------------------------------

export interface RunSource {
  accession_no: string | null;
  form: string | null;
  filed: string | null;
  filing_url: string | null;
  period_end: string | null;
}

/** One fiscal year (or the TTM): ratios are ratios (0.1 = 10%), money in USD. */
export interface RunYear {
  fy: number;
  period_end: string;
  revenue: number | null;
  growth: number | null;
  organic_growth: number | null;
  acquired_revenue: number | null;
  operating_income: number | null;
  operating_income_adjusted: number | null;
  one_offs: number;
  margin: number | null;
  margin_reported: number | null;
  pretax_income: number | null;
  income_tax: number | null;
  non_operating: number | null;
  non_operating_rate: number | null;
  tax_rate: number | null;
  net_income: number | null;
  net_income_adjusted: number | null;
  eps: number | null;
  eps_adjusted: number | null;
  eps_growth: number | null;
  shares: number | null;
  share_change: number | null;
  operating_cash_flow: number | null;
  capex: number | null;
  fcf: number | null;
  fcf_margin: number | null;
  buybacks: number | null;
  /** the diluted share count moved by more than half: a split or a merger, no share change */
  share_break?: boolean;
  /** per-share figures on the basis before the break in this fiscal year: no P/E */
  earlier_share_basis?: number | null;
  sources: Record<string, RunSource>;
}

export interface RunTtm extends RunYear {
  /** "FY2026 Q3" */
  period: string;
}

export interface RunCurrent {
  fy: number;
  /** reported quarters of the current fiscal year (Q1..Qk) */
  quarters: number;
  through: string | null;
  actual: {
    revenue: number | null;
    operating_income: number | null;
    operating_income_adjusted: number | null;
    one_offs: number;
    pretax_income: number | null;
    income_tax: number | null;
    net_income: number | null;
    net_income_adjusted?: number | null;
    fcf: number | null;
    margin: number | null;
    fcf_margin: number | null;
    growth: number | null;
  } | null;
  prior: { revenue: number | null; operating_income: number | null; margin: number | null; fcf: number | null; fcf_margin: number | null } | null;
  remainder_prior: { revenue: number | null; operating_income: number | null; margin: number | null; fcf: number | null; fcf_margin: number | null } | null;
  sources: RunSource[];
}

export type Inputs = { growth: number; margin: number } & Record<Rate, number>;

export interface Projected {
  fy: number;
  inputs: Inputs;
  defaulted: Rate[];
  note?: string;
  revenue: number;
  growth: number | null;
  organic_growth: number | null;
  acquired_revenue: number | null;
  operating_income: number;
  operating_income_adjusted: number;
  one_offs: number;
  margin: number | null;
  margin_reported: number | null;
  non_operating: number;
  non_operating_rate: number | null;
  pretax_income: number;
  tax_rate: number;
  net_income: number;
  net_income_adjusted: number;
  shares: number | null;
  share_change: number;
  eps: number | null;
  eps_adjusted: number | null;
  eps_growth: number | null;
  fcf: number;
  fcf_margin: number | null;
  /** a ytd year 1: the reported quarters and the remainder */
  actual?: { quarters: number; through: string | null; revenue: number | null; operating_income: number | null; operating_income_adjusted: number | null; net_income: number | null; fcf: number | null };
  remainder?: { quarters: number; revenue: number; operating_income: number; growth: number; margin: number; fcf: number };
}

export interface RunCase {
  /** bear / bull not added yet: what they would start as */
  preview: boolean;
  years: Projected[];
  /** what Reset sets, per year (percent) */
  defaults: Inputs[];
  check?: unknown;
  user_set?: boolean[];
  /** a model saved before F2 (year 1 a full-year guess): the remainder its year 1 implies */
  implied_remainder?: { growth: number; margin: number } | null;
}

export type Multiple = number | "n/m" | null;
export interface Multiples {
  fy?: number;
  pe: Multiple;
  ev_revenue: Multiple;
  ev_ebit: Multiple;
  fcf_yield: number | null;
}

export interface Reading {
  value: number | null;
  period_end: string | null;
  /** migration 035: not_applicable | not_meaningful | old | behind | null */
  status: string | null;
  reason: string | null;
  citation: { accession_no: string; form: string | null; filed: string | null; filing_url: string | null } | null;
}

export interface Valuation {
  price: number;
  date: string;
  shares_outstanding: Reading | null;
  /** "outstanding", or "diluted" when only the diluted weighted count is known */
  shares_basis: "outstanding" | "diluted";
  net_debt: Reading | null;
  market_cap: number | null;
  /** the market cap's currency: the price's (057; absent from an older API: USD) */
  market_cap_currency?: string;
  /** the price's currency isn't the figures': no EV and no multiple (057) */
  currency_mismatch?: { price: string; figures: string };
  enterprise_value: number | null;
  /** why there is no EV: absent | old | not_meaningful | not_applicable */
  ev_note: string | null;
  history: Multiples[];
  ttm: Multiples | null;
  cases: Partial<Record<Scenario, Multiples[]>>;
  note?: string;
}

export interface RunStats {
  cagr: number | null;
  cagr_years: number;
  avg_margin: number | null;
  margin_years: number;
  last_growth: number | null;
  last_margin: number | null;
  margin_min: number | null;
  margin_max: number | null;
  avg_fcf_margin: number | null;
  fcf_years: number;
}

export interface RunRate {
  growth: number | null;
  margin: number | null;
  period: string | null;
  non_operating: number | null;
  tax_rate: number | null;
  tax_source: "ttm" | "five_year" | "statutory" | null;
  share_change: number | null;
  share_source: string | null;
  fcf_margin: number | null;
}

export interface ModelRun {
  company_id: string;
  base_fy: number | null;
  ytd: boolean;
  history: RunYear[];
  ttm: RunTtm | null;
  current: RunCurrent | null;
  stats: RunStats;
  run_rate: RunRate;
  long_run: { growth: number; margin: number | null; fcf_margin: number | null };
  cases: Partial<Record<Scenario, RunCase>>;
  adjustments: Adjustment[];
  valuation: Valuation | null;
  /** the company's reporting currency, every amount's (057; absent from an older API: USD) */
  currency?: string;
}

/** A chart's unit for the run's amounts: its currency (057), "USD" from an older API. */
export function amountUnit(run: Pick<ModelRun, "currency">): string {
  return run.currency && /^[A-Z]{3}$/.test(run.currency) ? run.currency : "USD";
}

/** The base year (latest fiscal year with revenue) of a run. */
export function runBase(run: ModelRun): RunYear | null {
  return [...run.history].reverse().find((y) => y.revenue !== null) ?? null;
}

/** Year 1 is built from reported quarters (a ytd model, with at least one quarter reported). */
export function builtFromQuarters(run: ModelRun): boolean {
  return run.ytd && !!run.current?.quarters && !!run.current.actual;
}

/** "Q1–Q3", "H1" for two quarters, "Q1" for one. */
export function quartersLabel(k: number): string {
  return k === 2 ? "H1" : k === 1 ? "Q1" : `Q1–Q${k}`;
}

/** The quarters left: "Q4", "H2", "Q2–Q4". */
export function remainderLabel(k: number): string {
  return k === 3 ? "Q4" : k === 2 ? "H2" : k === 1 ? "Q2–Q4" : "full year";
}

function toSource(s: RunSource | undefined, companyId: string): Source | null {
  if (!s?.accession_no) return null;
  return { form: s.form ?? "", filed: s.filed ?? "", accessionNo: s.accession_no, url: s.filing_url, periodEnd: s.period_end ?? "", companyId };
}

/** Chart series on a fiscal-year axis: revenue and (adjusted) operating income, history solid,
 * each case's forecast dashed in the same colour from the base year (bear / bull faint). History
 * points carry their filing (a click opens it); forecast points have none. */
export function modelSeries(run: ModelRun, historyYears: number, cases: Scenario[]): Series[] {
  const history = run.history.slice(-historyYears);
  const base = runBase(run);
  const hist = (id: number, name: string, color: number, value: (y: RunYear) => number | null, src: string): Series => ({
    id, name, unit: amountUnit(run), color, type: "line", axis: "L",
    points: history.map((y) => {
      const v = value(y);
      const s = toSource(y.sources[src], run.company_id);
      return { key: y.fy, period: fyLabel(y.fy), periodEnd: y.period_end, y: v, raw: v === null ? "" : String(v), sources: s ? [s] : [] };
    }),
  });
  const fore = (id: number, name: string, color: number, start: number | null, value: (p: Projected) => number, rows: Projected[], faint: boolean): Series => ({
    id, name, unit: amountUnit(run), color, type: "line", axis: "L", dashed: true,
    ...(faint ? { faint: true } : {}),
    points: [
      ...(base && start !== null ? [{ key: base.fy, period: fyLabel(base.fy), periodEnd: base.period_end, y: start, raw: String(start), sources: [] }] : []),
      ...rows.map((p) => ({ key: p.fy, period: fyLabel(p.fy, true), periodEnd: "", y: value(p), raw: String(value(p)), sources: [] })),
    ],
  });
  const shown = cases.filter((c) => run.cases[c]?.years.length);
  const several = shown.length > 1;
  const name = (what: string, c: Scenario) => `${what} (${several ? c : "forecast"})`;
  const ids = { bear: 4, base: 1, bull: 5 } as const;
  const out: Series[] = [hist(0, "Revenue", 0, (y) => y.revenue, "revenue")];
  for (const c of shown) out.push(fore(ids[c], name("Revenue", c), 0, base?.revenue ?? null, (p) => p.revenue, run.cases[c]!.years, c !== "base"));
  out.push(hist(2, "Operating income", 1, (y) => y.operating_income_adjusted, "operating_income"));
  for (const c of shown) {
    out.push(fore(ids[c] + 4, name("Operating income", c), 1, base?.operating_income_adjusted ?? null, (p) => p.operating_income_adjusted, run.cases[c]!.years, c !== "base"));
  }
  return out.filter((s) => s.points.some((p) => p.y !== null));
}

// --- the three-case table ---------------------------------------------------------------------

export type Unit = "USD" | "ratio" | "USD/shares" | "shares" | "multiple";
export type CellValue = number | "n/m" | null;

export interface Column {
  key: string;
  /** "FY2025", "TTM", "FY2026 Q1–Q3", "FY2026E" */
  label: string;
  /** "FY2026 Q3", "reported", "Q1–Q3 reported + Q4" */
  sub?: string;
  kind: "history" | "ttm" | "ytd" | "forecast";
  fy?: number;
  /** the forecast's index */
  index?: number;
}

export interface Line {
  key: string;
  label: string;
  unit: Unit;
  group: "Revenue" | "Profit" | "Per share" | "Cash" | "Valuation at your price";
  /** why a blank reads as it does, for the cell's title */
  blank?: string;
  /** what a history year's cell needs said (a split, an earlier share basis) */
  noteOf?: (y: RunYear) => string | undefined;
  history: (y: RunYear, i: number) => CellValue;
  ttm?: (t: RunTtm) => CellValue;
  ytd?: (c: RunCurrent) => CellValue;
  forecast: (p: Projected, c: Scenario, i: number) => CellValue;
}

/** Which lines the table shows for a run: one-off rows (reported beside adjusted) only with
 * one-offs, organic growth only with acquired revenue, valuation only with a price. */
export function modelLines(run: ModelRun): Line[] {
  const oneOffs = run.adjustments.some((a) => a.kind === "one_off");
  const acquired = run.adjustments.some((a) => a.kind === "acquired");
  const v = run.valuation;
  const adj = oneOffs ? ", adjusted" : "";
  const multiple = (pick: (m: Multiples) => Multiple) => ({
    history: (y: RunYear) => pick(v!.history.find((m) => m.fy === y.fy) ?? NONE),
    ttm: () => (v!.ttm ? pick(v!.ttm) : null),
    forecast: (_p: Projected, c: Scenario, i: number) => {
      const m = v!.cases[c]?.[i];
      return m ? pick(m) : null;
    },
  });
  const lines: (Line | false)[] = [
    { key: "revenue", label: "Revenue", unit: "USD", group: "Revenue", history: (y) => y.revenue, ttm: (t) => t.revenue, ytd: (c) => c.actual?.revenue ?? null, forecast: (p) => p.revenue },
    {
      key: "growth", label: "Revenue growth", unit: "ratio", group: "Revenue", blank: "no revenue the year before",
      history: (y) => y.growth, ttm: (t) => t.growth, ytd: (c) => c.actual?.growth ?? null, forecast: (p) => p.growth,
    },
    acquired && {
      key: "organic", label: "Organic growth (your acquired revenue out)", unit: "ratio", group: "Revenue", blank: "no acquired revenue entered",
      history: (y) => y.organic_growth, forecast: (p) => p.organic_growth,
    },
    {
      key: "oi", label: `Operating income${adj}`, unit: "USD", group: "Profit",
      history: (y) => y.operating_income_adjusted, ttm: (t) => t.operating_income_adjusted, ytd: (c) => c.actual?.operating_income_adjusted ?? null,
      forecast: (p) => p.operating_income_adjusted,
    },
    {
      key: "margin", label: `Operating margin${adj}`, unit: "ratio", group: "Profit",
      history: (y) => y.margin, ttm: (t) => t.margin, ytd: (c) => c.actual?.margin ?? null, forecast: (p) => p.margin,
    },
    oneOffs && {
      key: "oi_reported", label: "Operating income, reported", unit: "USD", group: "Profit",
      history: (y) => y.operating_income, ttm: (t) => t.operating_income, ytd: (c) => c.actual?.operating_income ?? null, forecast: (p) => p.operating_income,
    },
    oneOffs && {
      key: "margin_reported", label: "Operating margin, reported", unit: "ratio", group: "Profit",
      history: (y) => y.margin_reported, ttm: (t) => t.margin_reported,
      ytd: (c) => (c.actual?.operating_income != null && c.actual.revenue ? c.actual.operating_income / c.actual.revenue : null),
      forecast: (p) => p.margin_reported,
    },
    {
      key: "nonop", label: "Interest and other, % of revenue", unit: "ratio", group: "Profit", blank: "no pretax income in the filings",
      history: (y) => y.non_operating_rate, ttm: (t) => t.non_operating_rate,
      ytd: (c) => (c.actual?.operating_income != null && c.actual.pretax_income != null && c.actual.revenue ? (c.actual.operating_income - c.actual.pretax_income) / c.actual.revenue : null),
      forecast: (p) => p.non_operating_rate,
    },
    {
      key: "tax", label: "Tax rate", unit: "ratio", group: "Profit", blank: "no tax rate: pretax income is zero or negative",
      history: (y) => y.tax_rate, ttm: (t) => t.tax_rate,
      ytd: (c) => (c.actual?.income_tax != null && c.actual.pretax_income ? c.actual.income_tax / c.actual.pretax_income : null),
      forecast: (p) => p.tax_rate,
    },
    {
      key: "ni", label: `Net income${adj}`, unit: "USD", group: "Profit",
      history: (y) => y.net_income_adjusted, ttm: (t) => t.net_income_adjusted,
      ytd: (c) => c.actual?.net_income_adjusted ?? c.actual?.net_income ?? null,
      forecast: (p) => p.net_income_adjusted,
    },
    {
      key: "eps", label: `Diluted EPS${adj}`, unit: "USD/shares", group: "Per share", blank: "per-share figures don't add across quarters: the 10-K states the year's",
      noteOf: earlierBasis, history: (y) => y.eps_adjusted, ttm: () => null, forecast: (p) => p.eps_adjusted,
    },
    oneOffs && {
      key: "eps_reported", label: "Diluted EPS, reported", unit: "USD/shares", group: "Per share", blank: "per-share figures don't add across quarters",
      noteOf: earlierBasis, history: (y) => y.eps, ttm: () => null, forecast: (p) => p.eps,
    },
    { key: "eps_growth", label: "EPS growth", unit: "ratio", group: "Per share", blank: "no positive EPS the year before", noteOf: splitYear, history: (y) => y.eps_growth, forecast: (p) => p.eps_growth },
    { key: "shares", label: "Diluted shares", unit: "shares", group: "Per share", blank: "no diluted share count in the 10-K", noteOf: earlierBasis, history: (y) => y.shares, ttm: (t) => t.shares, forecast: (p) => p.shares },
    { key: "share_change", label: "Share change", unit: "ratio", group: "Per share", noteOf: splitYear, history: (y) => y.share_change, forecast: (p) => p.share_change },
    { key: "fcf", label: "Free cash flow", unit: "USD", group: "Cash", history: (y) => y.fcf, ttm: (t) => t.fcf, ytd: (c) => c.actual?.fcf ?? null, forecast: (p) => p.fcf },
    { key: "fcf_margin", label: "FCF margin", unit: "ratio", group: "Cash", history: (y) => y.fcf_margin, ttm: (t) => t.fcf_margin, ytd: (c) => c.actual?.fcf_margin ?? null, forecast: (p) => p.fcf_margin },
    { key: "buybacks", label: "Buybacks", unit: "USD", group: "Cash", blank: "no buybacks tagged", history: (y) => y.buybacks, ttm: (t) => t.buybacks, forecast: () => null },
    !!v && { key: "pe", label: "P/E", unit: "multiple", group: "Valuation at your price", noteOf: earlierBasis, ...multiple((m) => m.pe) },
    !!v && { key: "ev_revenue", label: "EV / Revenue", unit: "multiple", group: "Valuation at your price", blank: evBlank(v), ...multiple((m) => m.ev_revenue) },
    !!v && { key: "ev_ebit", label: "EV / EBIT", unit: "multiple", group: "Valuation at your price", blank: evBlank(v), ...multiple((m) => m.ev_ebit) },
    !!v && { key: "fcf_yield", label: "FCF yield", unit: "ratio", group: "Valuation at your price", ...multiple((m) => m.fcf_yield) },
  ];
  return lines.filter((l): l is Line => !!l);
}

const NONE: Multiples = { pe: null, ev_revenue: null, ev_ebit: null, fcf_yield: null };

/** a split or a merger that year (the API's `share_break`) */
const splitYear = (y: RunYear) =>
  y.share_break ? "the diluted share count moved by more than half this year: a split or a merger, not buybacks, so no change is shown" : undefined;

/** a year stated on the share basis before a later split or merger */
const earlierBasis = (y: RunYear) =>
  y.earlier_share_basis ? `on the share basis before the split or merger in FY${y.earlier_share_basis}: not comparable with later years, so no P/E` : undefined;

/** Why there is no EV, in words, or undefined. */
export function evBlank(v: Valuation | null): string | undefined {
  if (!v || v.enterprise_value !== null) return undefined;
  const d = v.net_debt;
  switch (v.ev_note) {
    case "old":
      return `no EV: the latest net debt in the filings is from ${d?.period_end ?? "long ago"}, over 300 days before the company's latest period`;
    case "not_applicable":
      return `no EV: ${d?.reason ?? "net debt doesn't apply to this company"}`;
    case "not_meaningful":
      return `no EV: net debt is not meaningful (${d?.reason ?? "see Data notes"})`;
    case "currency":
      return `no EV: the price is in ${v.currency_mismatch?.price ?? "another currency"} and the figures in ${v.currency_mismatch?.figures ?? "another"}`;
    default:
      return "no EV: no net debt in the filings (Tributary derives it only where debt is tagged)";
  }
}

/** The table's columns: the last `historyYears` fiscal years, the TTM when it isn't a fiscal
 * year end, the current fiscal year's reported quarters, then the forecast years. */
export function modelColumns(run: ModelRun, historyYears: number, forecastYears: number): Column[] {
  const cols: Column[] = run.history.slice(-historyYears).map((y) => ({ key: `h${y.fy}`, label: fyLabel(y.fy), kind: "history", fy: y.fy }));
  if (run.ttm) cols.push({ key: "ttm", label: "TTM", sub: run.ttm.period, kind: "ttm" });
  const cur = run.current;
  if (cur?.quarters && cur.actual) cols.push({ key: "ytd", label: `FY${cur.fy} ${quartersLabel(cur.quarters)}`, sub: "reported", kind: "ytd", fy: cur.fy });
  const base = runBase(run);
  const fromQuarters = builtFromQuarters(run);
  for (let i = 0; i < forecastYears; i++) {
    const fy = base ? base.fy + 1 + i : i + 1;
    const sub = i === 0 && fromQuarters ? `${quartersLabel(cur!.quarters)} reported + ${remainderLabel(cur!.quarters)}` : i === 0 && cur?.quarters ? "full-year guess" : "forecast";
    cols.push({ key: `f${i}`, label: base ? fyLabel(fy, true) : `Year ${i + 1}`, sub, kind: "forecast", fy, index: i });
  }
  return cols;
}

/** Formats a cell. */
export function formatCell(v: CellValue, unit: Unit, currency = "USD"): string {
  if (v === "n/m") return "n/m";
  if (v === null || !Number.isFinite(v)) return "—";
  switch (unit) {
    case "USD":
      // an amount in the company's currency (057): "€2.10B", "CNY 996.35B"
      return money(v, currency);
    case "USD/shares":
      return money(v, `${currency}/shares`);
    case "shares": {
      const abs = Math.abs(v);
      return abs >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : `${(v / 1e6).toFixed(1)}M`;
    }
    case "multiple":
      return `${v.toFixed(1)}x`;
    default:
      return `${(v * 100).toFixed(1)}%`;
  }
}

// --- guidance, as a reference ---------------------------------------------------------------

export interface Guidance {
  /** the forecast year it sits beside */
  fy: number;
  quarter: EarningsQuarter;
}

const DAY = 86_400_000;

/** The latest earnings release's outlook and the forecast year it covers: the fiscal year of the
 * quarter after the release's quarter. Counted in quarters from the base year's end (n = 0 is
 * the base year's Q4): the next quarter falls in base + ceil((n + 1) / 4), so a release for the
 * base year's Q4 or the current year's Q1-Q3 sits beside year 1, and one for the current year's
 * Q4 (out before its 10-K) beside year 2. A release older than the base year's end covers a year
 * already reported: none. The text is quoted, never read into an input. */
export function guidanceYear(earnings: EarningsResponse | null, run: ModelRun | null): Guidance | null {
  const base = run ? runBase(run) : null;
  if (!earnings || !base) return null;
  const q = earnings.quarters.find((x) => x.release && x.outlook.length);
  if (!q) return null;
  const gap = (Date.parse(q.period_end) - Date.parse(base.period_end)) / DAY;
  if (!Number.isFinite(gap) || gap < -7) return null;
  const n = Math.max(0, Math.round(gap / 91.31));
  return { fy: base.fy + Math.ceil((n + 1) / 4), quarter: q };
}
