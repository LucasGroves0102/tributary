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
 * - `modelSeries` / `modelRows`: the chart (history solid, forecast dashed) and the table */

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

export interface ModelState {
  companyId: string;
  historyYears: number;
  assumptions: Assumption[];
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

/** A model's state from the API (untrusted) as a ModelState, or null without a company. */
export function restoreModel(raw: unknown): ModelState | null {
  const o = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
  if (!o || typeof o.companyId !== "string" || !CIK.test(o.companyId)) return null;
  const years = num(o.historyYears);
  const assumptions = (Array.isArray(o.assumptions) ? o.assumptions : []).slice(0, MAX_YEARS).flatMap((x) => {
    const a = x && typeof x === "object" ? (x as Record<string, unknown>) : null;
    const growth = num(a?.growth);
    const margin = num(a?.margin);
    if (growth === null || margin === null) return [];
    const note = typeof a?.note === "string" ? a.note.trim().slice(0, NOTE_MAX) : "";
    return [{ growth: clamp(growth, GROWTH_RANGE), margin: clamp(margin, MARGIN_RANGE), ...(note ? { note } : {}) }];
  });
  return {
    companyId: o.companyId,
    historyYears: years === null ? DEFAULT_HISTORY : Math.round(clamp(years, [MIN_HISTORY, MAX_HISTORY])),
    assumptions: assumptions.length ? assumptions : [{ growth: 0, margin: 0 }],
  };
}

/** The state as saved: blank notes dropped, numbers rounded to 0.01. */
export function modelState(m: ModelState): Record<string, unknown> {
  return {
    companyId: m.companyId,
    historyYears: m.historyYears,
    assumptions: m.assumptions.map((a) => {
      const note = a.note?.trim();
      return { growth: round2(a.growth), margin: round2(a.margin), ...(note ? { note: note.slice(0, NOTE_MAX) } : {}) };
    }),
  };
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

/** Assumptions for `years` forecast years, each at the historical CAGR and average margin
 * (0 where history has none), rounded to 0.1. */
export function defaultAssumptions(stats: HistoryStats, years = DEFAULT_YEARS): Assumption[] {
  const growth = stats.cagr === null ? 0 : round1(clamp(stats.cagr * 100, GROWTH_RANGE));
  const margin = stats.avgMargin === null ? 0 : round1(clamp(stats.avgMargin * 100, MARGIN_RANGE));
  return Array.from({ length: Math.min(MAX_YEARS, Math.max(1, years)) }, () => ({ growth, margin }));
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
export function modelSeries(history: HistoryYear[], projection: ProjectedYear[]): Series[] {
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
  const fore = (id: number, name: string, color: number, start: number | null, value: (p: ProjectedYear) => number): Series => ({
    id,
    name,
    unit: "USD",
    color,
    type: "line",
    axis: "L",
    dashed: true,
    points: [
      ...(base && start !== null
        ? [{ key: base.fy, period: fyLabel(base.fy), periodEnd: base.periodEnd, y: start, raw: String(start), sources: [] }]
        : []),
      ...projection.map((p) => ({ key: p.fy, period: fyLabel(p.fy, true), periodEnd: "", y: value(p), raw: String(value(p)), sources: [] })),
    ],
  });
  const out = [
    hist(0, "Revenue", 0, (y) => y.revenue, (y) => y.revenueSource),
    fore(1, "Revenue (forecast)", 0, base?.revenue ?? null, (p) => p.revenue),
    hist(2, "Operating income", 1, (y) => y.operatingIncome, (y) => y.operatingIncomeSource),
    fore(3, "Operating income (forecast)", 1, base?.operatingIncome ?? null, (p) => p.operatingIncome),
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
