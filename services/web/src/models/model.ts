/** Models: a revenue-first forecast saved as a workspace item of kind "model" (app DB 009,
 * validated by query_api/workspaces.py ModelState). The item stores only what the user typed:
 * the company, how many fiscal years of history to show and, per forecast year, revenue growth
 * and operating margin (percent) with a note. The history is read live through the Query API
 * as the viewer (`/v1/companies/{id}/financials?view=ttm`, the TTM at each fiscal Q4 is the
 * fiscal year, as the company page's annual tables read it), so a model never holds data the
 * viewer couldn't read.
 *
 * Pure functions, tested without rendering:
 * - `annualHistory`: fiscal years of revenue and operating income from the API's rows
 * - `historyStats`: the revenue CAGR and average operating margin over the last 5 fiscal years
 *   (what each assumption is shown next to, and what the thesis checker compares against)
 * - `marginRange` / `checkAssumptions`: the thesis check's flags (agent-api ports these to
 *   Python, services/agent `model_stats.py`; `stats-fixture.json` keeps the two equal): a
 *   forecast year's growth more than GROWTH_FLAG_POINTS percentage points above or below the
 *   CAGR, or its operating margin outside [min, max] of the margins `historyStats` averages
 * - `project`: revenue_t = revenue_(t-1) x (1 + growth_t); operating income_t = revenue_t x
 *   margin_t, from the latest fiscal year with revenue
 * - `modelSeries` / `modelRows`: the chart (history solid, forecast dashed) and the table
 * - `runRate` / `defaultAssumptions`: what a new model starts from and Reset sets. Year 1 at the
 *   trailing-twelve-month run rate (the newest TTM revenue against the TTM a year earlier, and
 *   its operating margin; the latest fiscal year's when there's no TTM), then each year closes
 *   half the gap (FADE) to the long run: growth to the 5-year CAGR held between 0% and
 *   LONG_RUN_GROWTH_CAP (5%), margin to the 5-year average. A flat 5-year CAGR ignored the run
 *   rate both ways (Vertiv 11.4% against 20.1% TTM, Deckers 16.5% against 6.3%) and compounded a
 *   hyper-grower's peak forever. Ported to agent-api (`stats-fixture.json` `default_cases`: real
 *   TTM rows of NVIDIA, Vertiv, Deckers, Intel).
 *
 * Scenarios (the Analysis tab): `assumptions` is the BASE case, which is all a model saved
 * before scenarios holds; `scenarios.bear` / `scenarios.bull` optionally hold the other cases for
 * the same forecast years. `scenarioAssumptions` reads one case from a saved state (ported to
 * agent-api `model_stats.scenario_assumptions`; `stats-fixture.json` `scenario_cases` keep the
 * two equal); the thesis check checks the base case only. New bear / bull cases start from the
 * base case moved by SCENARIO_SPREAD: half the base growth (at least 5 points) and a fifth of
 * the base margin (at least 3 points), per year. The chart draws the base forecast dashed and
 * bear / bull lighter (`faint`); the table shows the selected case, or the case it would be
 * (labelled a preview) before bear and bull are added. */

import type { Series, Source } from "../chart/series";
import type { FinancialRow } from "../types";

export const MAX_YEARS = 10;
export const DEFAULT_YEARS = 3;
export const MIN_HISTORY = 3;
export const MAX_HISTORY = 10;
export const DEFAULT_HISTORY = 8;
/** fiscal years the averages next to each assumption look back over */
export const STATS_WINDOW = 5;
export const NOTE_MAX = 500;
export const GROWTH_RANGE = [-100, 1000] as const;
export const MARGIN_RANGE = [-1000, 100] as const;
/** what the history request asks the Query API for (40 TTM quarters: 10 fiscal years) */
export const HISTORY_METRICS = ["revenue", "operating_income"];
export const HISTORY_QUARTERS = 40;

const CIK = /^\d{10}$/;

export interface Assumption {
  /** revenue growth over the year before, percent */
  growth: number;
  /** operating margin, percent */
  margin: number;
  note?: string;
}

export const SCENARIOS = ["bear", "base", "bull"] as const;
export type Scenario = (typeof SCENARIOS)[number];
export const SCENARIO_LABELS: Record<Scenario, string> = { bear: "Bear", base: "Base", bull: "Bull" };
/** How far a new bear (minus) or bull (plus) case sits from the base case, per year: half the
 * base growth but at least 5 points, a fifth of the base margin but at least 3 points. In
 * proportion, so a hyper-grower's bear case doesn't still quadruple revenue (5 points off 83%
 * growth was barely a different line); the minimums keep a slow grower's cases apart. */
export const SCENARIO_SPREAD = { growth: { share: 0.5, min: 5 }, margin: { share: 0.2, min: 3 } } as const;

export interface ModelState {
  companyId: string;
  historyYears: number;
  /** the base case */
  assumptions: Assumption[];
  /** bear / bull cases for the same years; absent: base only */
  scenarios?: { bear?: Assumption[]; bull?: Assumption[] };
}

export interface HistoryYear {
  /** the fiscal year, e.g. 2025 for "FY2025" */
  fy: number;
  periodEnd: string;
  revenue: number | null;
  operatingIncome: number | null;
  revenueSource: Source | null;
  operatingIncomeSource: Source | null;
}

export interface ProjectedYear {
  fy: number;
  revenue: number;
  /** ratios (0.1 = 10%) */
  growth: number;
  margin: number;
  operatingIncome: number;
  note?: string;
}

export interface HistoryStats {
  /** compound annual revenue growth over `cagrYears` fiscal years (ratio), null without two
   * years of positive revenue in the window */
  cagr: number | null;
  cagrYears: number;
  /** mean of the yearly operating margins over `marginYears` fiscal years (ratio) */
  avgMargin: number | null;
  marginYears: number;
  /** the latest fiscal year's growth and margin, when known */
  lastGrowth: number | null;
  lastMargin: number | null;
}

const clamp = (v: number, [lo, hi]: readonly [number, number]) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** Per-year assumptions from untrusted JSON: entries with finite growth and margin, the first
 * MAX_YEARS, notes trimmed (blank ones dropped). Not clamped (scenarioAssumptions mirrors the
 * agent's port); restoreModel clamps. */
function years(raw: unknown): Assumption[] {
  return (Array.isArray(raw) ? raw : []).slice(0, MAX_YEARS).flatMap((x) => {
    const a = isObject(x) ? x : null;
    const growth = num(a?.growth);
    const margin = num(a?.margin);
    if (growth === null || margin === null) return [];
    const note = typeof a?.note === "string" ? a.note.trim().slice(0, NOTE_MAX) : "";
    return [{ growth, margin, ...(note ? { note } : {}) }];
  });
}

/** One case's assumptions from a saved model state (untrusted): base = `assumptions`; bear and
 * bull from `scenarios`, only with as many (valid) years as the base case; null when there is
 * no such case. agent-api `model_stats.scenario_assumptions` is the port the thesis check uses
 * (on the base case). */
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

const clampYear = (a: Assumption): Assumption => ({ ...a, growth: clamp(a.growth, GROWTH_RANGE), margin: clamp(a.margin, MARGIN_RANGE) });

/** A model's state from the API (untrusted) as a ModelState, or null without a company. */
export function restoreModel(raw: unknown): ModelState | null {
  const o = isObject(raw) ? raw : null;
  if (!o || typeof o.companyId !== "string" || !CIK.test(o.companyId)) return null;
  const years = num(o.historyYears);
  const assumptions = (scenarioAssumptions(o, "base") ?? []).map(clampYear);
  const bear = scenarioAssumptions(o, "bear")?.map(clampYear);
  const bull = scenarioAssumptions(o, "bull")?.map(clampYear);
  return {
    companyId: o.companyId,
    historyYears: years === null ? DEFAULT_HISTORY : Math.round(clamp(years, [MIN_HISTORY, MAX_HISTORY])),
    assumptions: assumptions.length ? assumptions : [{ growth: 0, margin: 0 }],
    ...(bear || bull ? { scenarios: { ...(bear ? { bear } : {}), ...(bull ? { bull } : {}) } } : {}),
  };
}

const savedYears = (list: Assumption[]) =>
  list.map((a) => {
    const note = a.note?.trim();
    return { growth: round2(a.growth), margin: round2(a.margin), ...(note ? { note: note.slice(0, NOTE_MAX) } : {}) };
  });

/** The state as saved: blank notes dropped, numbers rounded to 0.01; bear / bull only when set
 * (a base-only model saves exactly as before scenarios). */
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
  };
}

/** The assumptions of one case of a model (base when the case isn't set). */
export function caseOf(m: Pick<ModelState, "assumptions" | "scenarios">, scenario: Scenario): Assumption[] {
  return scenario === "base" ? m.assumptions : (m.scenarios?.[scenario] ?? m.assumptions);
}

/** A new bear (or bull) case: each base year moved down (up) by SCENARIO_SPREAD, without notes,
 * rounded to 0.1. */
export function shiftedCase(base: Assumption[], scenario: "bear" | "bull"): Assumption[] {
  const sign = scenario === "bear" ? -1 : 1;
  const step = (v: number, s: { share: number; min: number }) => Math.max(s.min, Math.abs(v) * s.share);
  return base.map((a) => ({
    growth: round1(clamp(a.growth + sign * step(a.growth, SCENARIO_SPREAD.growth), GROWTH_RANGE)),
    margin: round1(clamp(a.margin + sign * step(a.margin, SCENARIO_SPREAD.margin), MARGIN_RANGE)),
  }));
}

const round2 = (v: number) => Math.round(v * 100) / 100;
const round1 = (v: number) => Math.round(v * 10) / 10;

function source(r: FinancialRow, companyId: string | undefined): Source {
  return {
    form: r.citation.form,
    filed: r.citation.filed,
    accessionNo: r.citation.accession_no,
    url: r.citation.filing_url,
    periodEnd: r.period_end,
    ...(companyId ? { companyId } : {}),
  };
}

/** Fiscal years from TTM rows (fiscal basis): the row at each fiscal Q4 is the year. The
 * newest `years` years with any value, oldest first. Years without a Q4 row are left out. */
export function annualHistory(rows: FinancialRow[], years: number, companyId?: string): HistoryYear[] {
  const by = new Map<number, HistoryYear>();
  for (const r of rows) {
    const m = /^FY(\d{4}) Q4$/.exec(r.fiscal_period ?? "");
    if (!m || (r.metric !== "revenue" && r.metric !== "operating_income")) continue;
    const value = Number(r.value);
    if (!Number.isFinite(value)) continue;
    const fy = Number(m[1]);
    const y = by.get(fy) ?? {
      fy,
      periodEnd: r.period_end,
      revenue: null,
      operatingIncome: null,
      revenueSource: null,
      operatingIncomeSource: null,
    };
    if (r.metric === "revenue") {
      y.revenue = value;
      y.revenueSource = source(r, companyId);
    } else {
      y.operatingIncome = value;
      y.operatingIncomeSource = source(r, companyId);
    }
    if (r.period_end > y.periodEnd) y.periodEnd = r.period_end;
    by.set(fy, y);
  }
  return [...by.values()].sort((a, b) => a.fy - b.fy).slice(-Math.max(1, years));
}

/** Growth from one fiscal year to the next (ratio), when both revenues are known and the first
 * is positive, and the years are consecutive. */
export function growthOf(prev: HistoryYear | undefined, cur: HistoryYear): number | null {
  if (!prev || prev.fy !== cur.fy - 1 || prev.revenue === null || cur.revenue === null || prev.revenue <= 0) return null;
  return cur.revenue / prev.revenue - 1;
}

export function marginOf(y: HistoryYear): number | null {
  return y.revenue !== null && y.revenue > 0 && y.operatingIncome !== null ? y.operatingIncome / y.revenue : null;
}

/** The latest fiscal year with revenue: where the forecast starts. */
export function baseYear(history: HistoryYear[]): HistoryYear | null {
  return [...history].reverse().find((y) => y.revenue !== null) ?? null;
}

/** Revenue CAGR and average operating margin over the last STATS_WINDOW fiscal years, ending
 * at the base year. CAGR: (revenue_end / revenue_start)^(1/n) - 1 from the earliest year in
 * the window with positive revenue (n = the years between them). Margin: the mean of each
 * year's operating income / revenue in the window (the years that have both). */
export function historyStats(history: HistoryYear[], window = STATS_WINDOW): HistoryStats {
  const end = baseYear(history);
  const empty = { cagr: null, cagrYears: 0, avgMargin: null, marginYears: 0, lastGrowth: null, lastMargin: null };
  if (!end) return empty;
  const inWindow = history.filter((y) => y.fy <= end.fy && y.fy >= end.fy - window);
  const start = inWindow.find((y) => y.revenue !== null && y.revenue > 0 && y.fy < end.fy);
  const n = start ? end.fy - start.fy : 0;
  const cagr = start && n > 0 && end.revenue! > 0 ? (end.revenue! / start.revenue!) ** (1 / n) - 1 : null;
  const margins = inWindow.filter((y) => y.fy > end.fy - window).map(marginOf).filter((m): m is number => m !== null);
  const prev = history.find((y) => y.fy === end.fy - 1);
  return {
    cagr,
    cagrYears: cagr === null ? 0 : n,
    avgMargin: margins.length ? margins.reduce((a, b) => a + b, 0) / margins.length : null,
    marginYears: margins.length,
    lastGrowth: growthOf(prev, end),
    lastMargin: marginOf(end),
  };
}

/** Lowest and highest operating margin over the fiscal years `historyStats` averages (the
 * STATS_WINDOW years ending at the base year that have both values); null without any. */
export function marginRange(history: HistoryYear[], window = STATS_WINDOW): { min: number; max: number } | null {
  const end = baseYear(history);
  if (!end) return null;
  const margins = history.filter((y) => y.fy <= end.fy && y.fy > end.fy - window).map(marginOf).filter((m): m is number => m !== null);
  return margins.length ? { min: Math.min(...margins), max: Math.max(...margins) } : null;
}

/** percentage points of growth above or below the CAGR before the thesis check flags a year */
export const GROWTH_FLAG_POINTS = 10;
/** margins are typed to 0.01%; this only absorbs float noise */
const MARGIN_EPSILON = 1e-9;

export type Flag = "above" | "below" | null;

export interface AssumptionCheck {
  fy: number;
  /** percent, as typed */
  growth: number;
  margin: number;
  /** growth minus the CAGR, in percentage points (null without a CAGR) */
  growthVsCagr: number | null;
  growthFlag: Flag;
  marginFlag: Flag;
}

/** Each forecast year's growth and margin against history (the thesis check, B2). */
export function checkAssumptions(history: HistoryYear[], assumptions: Assumption[]): {
  baseFy: number | null;
  stats: HistoryStats;
  marginMin: number | null;
  marginMax: number | null;
  years: AssumptionCheck[];
} {
  const stats = historyStats(history);
  const range = marginRange(history);
  const base = baseYear(history);
  return {
    baseFy: base?.fy ?? null,
    stats,
    marginMin: range?.min ?? null,
    marginMax: range?.max ?? null,
    years: assumptions.map((a, i) => {
      const diff = stats.cagr === null ? null : a.growth - stats.cagr * 100;
      const growthFlag: Flag = diff === null ? null : diff > GROWTH_FLAG_POINTS ? "above" : diff < -GROWTH_FLAG_POINTS ? "below" : null;
      const ratio = a.margin / 100;
      const marginFlag: Flag = !range ? null : ratio > range.max + MARGIN_EPSILON ? "above" : ratio < range.min - MARGIN_EPSILON ? "below" : null;
      return { fy: base ? base.fy + i + 1 : i + 1, growth: a.growth, margin: a.margin, growthVsCagr: diff, growthFlag, marginFlag };
    }),
  };
}

/** The trailing-twelve-month run rate, from the same TTM rows the history comes from. */
export interface RunRate {
  /** the newest TTM revenue against the TTM a year earlier (ratio) */
  growth: number | null;
  /** that TTM's operating income / revenue (ratio) */
  margin: number | null;
  /** "FY2027 Q2" */
  period: string | null;
}

/** The newest TTM revenue row, the row for the same fiscal quarter a year earlier, and
 * operating income at the newest row's quarter. agent-api `model_stats.run_rate` is the port. */
export function runRate(rows: FinancialRow[]): RunRate {
  const rev = new Map<string, { fy: number; q: number; end: string; value: number }>();
  const oi = new Map<string, number>();
  for (const r of rows) {
    const m = /^FY(\d{4}) Q([1-4])$/.exec(r.fiscal_period ?? "");
    const value = Number(r.value);
    if (!m || r.value === null || !Number.isFinite(value)) continue;
    const key = `${m[1]} ${m[2]}`;
    if (r.metric === "revenue") rev.set(key, { fy: Number(m[1]), q: Number(m[2]), end: r.period_end ?? "", value });
    else if (r.metric === "operating_income") oi.set(key, value);
  }
  const newest = [...rev.values()].sort((a, b) => a.end.localeCompare(b.end) || a.fy - b.fy || a.q - b.q).at(-1);
  if (!newest) return { growth: null, margin: null, period: null };
  const ago = rev.get(`${newest.fy - 1} ${newest.q}`);
  const key = `${newest.fy} ${newest.q}`;
  return {
    growth: ago && ago.value > 0 ? newest.value / ago.value - 1 : null,
    margin: oi.has(key) && newest.value > 0 ? oi.get(key)! / newest.value : null,
    period: `FY${newest.fy} Q${newest.q}`,
  };
}

/** the most growth a default fades toward, percent (a long-run rate a company can hold) */
export const LONG_RUN_GROWTH_CAP = 5;
/** each later default year closes this share of the gap to the long run */
export const FADE = 0.5;

const first = (...values: (number | null | undefined)[]): number | null => values.find((v) => v !== null && v !== undefined) ?? null;

/** What a new model starts from (and Reset sets): year 1 at the run rate (else the latest fiscal
 * year's growth and margin, else the 5-year figures), then each year closes FADE of the gap to
 * the long run: growth to the 5-year CAGR held between 0% and LONG_RUN_GROWTH_CAP, margin to the
 * 5-year average. Percent, rounded to 0.1. Ported to agent-api `model_stats.default_assumptions`
 * (`stats-fixture.json` `default_cases`), so the thesis check knows a year still at its default. */
export function defaultAssumptions(stats: HistoryStats, years = DEFAULT_YEARS, run: RunRate | null = null): Assumption[] {
  const g0 = (first(run?.growth, stats.lastGrowth, stats.cagr) ?? 0) * 100;
  const m0 = (first(run?.margin, stats.lastMargin, stats.avgMargin) ?? 0) * 100;
  const gLong = clamp(stats.cagr !== null ? stats.cagr * 100 : g0, [0, LONG_RUN_GROWTH_CAP]);
  const mLong = stats.avgMargin !== null ? stats.avgMargin * 100 : m0;
  return Array.from({ length: Math.min(MAX_YEARS, Math.max(1, years)) }, (_, k) => {
    const keep = FADE ** k;
    return {
      growth: round1(clamp(gLong + (g0 - gLong) * keep, GROWTH_RANGE)),
      margin: round1(clamp(mLong + (m0 - mLong) * keep, MARGIN_RANGE)),
    };
  });
}

/** More or fewer forecast years: new years repeat the last one's numbers (without its note). */
export function resizeAssumptions(list: Assumption[], years: number): Assumption[] {
  const n = Math.min(MAX_YEARS, Math.max(1, years));
  if (list.length >= n) return list.slice(0, n);
  const last = list.at(-1) ?? { growth: 0, margin: 0 };
  return [...list, ...Array.from({ length: n - list.length }, () => ({ growth: last.growth, margin: last.margin }))];
}

/** The forecast: from the base year's revenue, each year grows by its assumption and earns its
 * margin. Empty without a base year. */
export function project(history: HistoryYear[], assumptions: Assumption[]): ProjectedYear[] {
  const base = baseYear(history);
  if (!base) return [];
  let revenue = base.revenue!;
  return assumptions.map((a, i) => {
    revenue = revenue * (1 + a.growth / 100);
    return {
      fy: base.fy + i + 1,
      revenue,
      growth: a.growth / 100,
      margin: a.margin / 100,
      operatingIncome: revenue * (a.margin / 100),
      ...(a.note ? { note: a.note } : {}),
    };
  });
}

export const fyLabel = (fy: number, forecast = false) => `FY${fy}${forecast ? "E" : ""}`;

export interface ModelRow {
  fy: number;
  label: string;
  forecast: boolean;
  revenue: number | null;
  growth: number | null;
  margin: number | null;
  operatingIncome: number | null;
  note?: string;
  revenueSource?: Source | null;
}

/** The table: history years (reported) then forecast years, oldest first. */
export function modelRows(history: HistoryYear[], projection: ProjectedYear[]): ModelRow[] {
  return [
    ...history.map((y, i) => ({
      fy: y.fy,
      label: fyLabel(y.fy),
      forecast: false,
      revenue: y.revenue,
      growth: growthOf(history[i - 1], y),
      margin: marginOf(y),
      operatingIncome: y.operatingIncome,
      revenueSource: y.revenueSource,
    })),
    ...projection.map((p) => ({
      fy: p.fy,
      label: fyLabel(p.fy, true),
      forecast: true,
      revenue: p.revenue,
      growth: p.growth,
      margin: p.margin,
      operatingIncome: p.operatingIncome,
      ...(p.note ? { note: p.note } : {}),
    })),
  ];
}

/** Chart series on a fiscal-year axis: revenue and operating income, history solid and
 * forecast dashed in the same colour; each forecast line starts at the base year so the two
 * join. History points carry their filing (a click opens it); forecast points have none. */
export function modelSeries(
  history: HistoryYear[],
  projection: ProjectedYear[],
  /** bear / bull forecasts, drawn lighter (faint) beside the base one */
  others: Partial<Record<"bear" | "bull", ProjectedYear[]>> = {},
): Series[] {
  const base = baseYear(history);
  const hist = (id: number, name: string, color: number, value: (y: HistoryYear) => number | null, src: (y: HistoryYear) => Source | null): Series => ({
    id,
    name,
    unit: "USD",
    color,
    type: "line",
    axis: "L",
    points: history.map((y) => {
      const v = value(y);
      const s = src(y);
      return { key: y.fy, period: fyLabel(y.fy), periodEnd: y.periodEnd, y: v, raw: v === null ? "" : String(v), sources: s ? [s] : [] };
    }),
  });
  const fore = (id: number, name: string, color: number, start: number | null, value: (p: ProjectedYear) => number, rows = projection, faint = false): Series => ({
    id,
    name,
    unit: "USD",
    color,
    type: "line",
    axis: "L",
    dashed: true,
    ...(faint ? { faint: true } : {}),
    points: [
      ...(base && start !== null
        ? [{ key: base.fy, period: fyLabel(base.fy), periodEnd: base.periodEnd, y: start, raw: String(start), sources: [] }]
        : []),
      ...rows.map((p) => ({ key: p.fy, period: fyLabel(p.fy, true), periodEnd: "", y: value(p), raw: String(value(p)), sources: [] })),
    ],
  });
  const cases = (["bear", "bull"] as const).filter((c) => others[c]?.length);
  const out = [
    hist(0, "Revenue", 0, (y) => y.revenue, (y) => y.revenueSource),
    fore(1, cases.length ? "Revenue (base)" : "Revenue (forecast)", 0, base?.revenue ?? null, (p) => p.revenue),
    ...cases.map((c, k) => fore(4 + k, `Revenue (${c})`, 0, base?.revenue ?? null, (p) => p.revenue, others[c], true)),
    hist(2, "Operating income", 1, (y) => y.operatingIncome, (y) => y.operatingIncomeSource),
    fore(3, cases.length ? "Operating income (base)" : "Operating income (forecast)", 1, base?.operatingIncome ?? null, (p) => p.operatingIncome),
    ...cases.map((c, k) => fore(6 + k, `Operating income (${c})`, 1, base?.operatingIncome ?? null, (p) => p.operatingIncome, others[c], true)),
  ];
  return out.filter((s) => s.points.some((p) => p.y !== null));
}

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
