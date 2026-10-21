/** Chart builder state and how each layer becomes one /v1/formulas/evaluate request. Pure, so
 * the request rules (transforms, alignment, range) are tested without rendering.
 *
 * Every layer is a formula: a metric layer is the bare metric with its company as
 * default_company. Transforms: quarterly = view quarter; TTM = view ttm; YoY = (f) / (f)[-4q] - 1
 * over TTM values; indexed = TTM values divided by the first one in range, times 100 (done here,
 * since it depends on the range). Metrics without TTM values (EPS, weighted shares) use
 * quarters for YoY and indexed. */

import type { FormulaRequest } from "../api";

export type Transform = "q" | "ttm" | "yoy" | "idx";
export type LayerType = "line" | "bar" | "area";
export type Axis = "L" | "R";
export type Align = "calendar" | "fiscal";
export type Range = 2 | 3 | 5;

export interface Layer {
  id: number;
  kind: "metric" | "formula";
  /** metric layers: the company and metric */
  companyId?: string;
  metric?: string;
  /** formula layers: the expression and the company of metrics written without a ticker */
  formula?: string;
  defaultCompany?: string;
  transform: Transform;
  type: LayerType;
  axis: Axis;
  hidden?: boolean;
}

export interface ChartState {
  title: string;
  layers: Layer[];
  range: Range;
  align: Align;
  nextId: number;
}

export const TRANSFORMS: [Transform, string][] = [
  ["q", "Quarterly"],
  ["ttm", "TTM"],
  ["yoy", "YoY %"],
  ["idx", "Indexed"],
];
export const RANGES: Range[] = [2, 3, 5];
export const MAX_LAYERS = 12;

export function emptyChart(): ChartState {
  return { title: "", layers: [], range: 3, align: "calendar", nextId: 1 };
}

/** Metric → whether it has TTM values (from /v1/screener/columns spans). */
export type TtmCatalog = (metric: string) => boolean;

/** Companies a layer reads: its company or default company, plus any TICKER.metric. */
export function layerCompanies(l: Layer): string[] {
  if (l.kind === "metric") return l.companyId ? [l.companyId] : [];
  const tickers = [...(l.formula ?? "").matchAll(/\b([A-Za-z][A-Za-z0-9]{0,9})\.[a-z_]/g)].map(
    (m) => `ticker:${m[1]!.toUpperCase()}`,
  );
  return [...new Set([...(l.defaultCompany ? [l.defaultCompany] : []), ...tickers])];
}

/** Fiscal quarters only line up within one company (the API's alignment rule), so fiscal
 * applies only when every visible layer reads the same single company. */
export function basis(s: ChartState): Align {
  if (s.align === "calendar") return "calendar";
  const companies = new Set(s.layers.filter((l) => !l.hidden).flatMap(layerCompanies));
  return companies.size <= 1 ? "fiscal" : "calendar";
}

/** Earliest period end in range: `years` back from today, plus a quarter so the oldest
 * quarter of the range is included. */
export function fromDate(range: Range, today: Date = new Date()): string {
  const d = new Date(Date.UTC(today.getUTCFullYear() - range, today.getUTCMonth() - 3, today.getUTCDate()));
  return d.toISOString().slice(0, 10);
}

/** The evaluate request for a layer, or null if it isn't complete yet. */
export function request(l: Layer, s: ChartState, hasTtm: TtmCatalog, today?: Date): FormulaRequest | null {
  let formula: string;
  let ttm: boolean;
  if (l.kind === "metric") {
    if (!l.companyId || !l.metric) return null;
    formula = l.metric;
    ttm = hasTtm(l.metric);
  } else {
    if (!l.formula?.trim()) return null;
    formula = l.formula.trim();
    ttm = true;
  }
  const view = l.transform === "q" || !ttm ? "quarter" : "ttm";
  if (l.transform === "yoy") formula = `(${formula}) / (${formula})[-4q] - 1`;
  const req: FormulaRequest = { formula, basis: basis(s), view, from: fromDate(s.range, today) };
  const company = l.kind === "metric" ? l.companyId : l.defaultCompany;
  if (company) req.default_company = company;
  return req;
}

/** A layer's legend name. `short` gives a company's ticker (or name). */
export function layerName(l: Layer, short: (companyId: string) => string, label: (metric: string) => string): string {
  const base =
    l.kind === "metric"
      ? `${l.companyId ? short(l.companyId) : "?"} ${l.metric ? label(l.metric).toLowerCase() : "?"}`
      : (l.formula ?? "").trim() || "formula";
  const suffix = { q: "", ttm: " (TTM)", yoy: " YoY", idx: " (indexed)" }[l.transform];
  return base + suffix;
}

/** Revenue (quarterly bars) and operating margin (TTM line, right axis) for one company, on
 * its fiscal quarters: the chart "Chart" buttons open. */
export function companyChart(s: ChartState, companyId: string, name: string): ChartState {
  let id = s.nextId;
  return {
    ...s,
    title: `${name}: revenue and operating margin`,
    align: "fiscal",
    layers: [
      { id: id++, kind: "metric", companyId, metric: "revenue", transform: "q", type: "bar", axis: "L" },
      { id: id++, kind: "metric", companyId, metric: "operating_margin", transform: "ttm", type: "line", axis: "R" },
    ],
    nextId: id,
  };
}

export const TEMPLATES: { label: string; metric: string; transform: Transform; title: string }[] = [
  { label: "Margins", metric: "operating_margin", transform: "ttm", title: "Operating margin, TTM" },
  { label: "Revenue growth", metric: "revenue", transform: "yoy", title: "Revenue growth, YoY (TTM)" },
  { label: "Free cash flow", metric: "free_cash_flow", transform: "ttm", title: "Free cash flow, TTM" },
  { label: "Leverage", metric: "debt_to_equity", transform: "q", title: "Debt to equity, quarter end" },
];

/** One line per company in the chart (or `fallback` if it has none) for a template. */
export function applyTemplate(s: ChartState, t: (typeof TEMPLATES)[number], fallback: string[]): ChartState {
  const inChart = [...new Set(s.layers.flatMap((l) => (l.kind === "metric" && l.companyId ? [l.companyId] : [])))];
  const companies = (inChart.length ? inChart : fallback).slice(0, 4);
  let id = s.nextId;
  return {
    ...s,
    title: t.title,
    layers: companies.map((companyId) => ({
      id: id++, kind: "metric", companyId, metric: t.metric, transform: t.transform, type: "line", axis: "L",
    })),
    nextId: id,
  };
}

export function addLayer(s: ChartState, layer: Omit<Layer, "id">): ChartState {
  if (s.layers.length >= MAX_LAYERS) return s;
  return { ...s, layers: [...s.layers, { ...layer, id: s.nextId }], nextId: s.nextId + 1 };
}

export function updateLayer(s: ChartState, id: number, patch: Partial<Layer>): ChartState {
  return { ...s, layers: s.layers.map((l) => (l.id === id ? { ...l, ...patch, id } : l)) };
}

export function removeLayer(s: ChartState, id: number): ChartState {
  return { ...s, layers: s.layers.filter((l) => l.id !== id) };
}

const TRANSFORM_SET = new Set(TRANSFORMS.map(([t]) => t));
const CIK = /^\d{10}$/;

/** A chart from storage or elsewhere untrusted: keeps only well-formed layers. */
export function restoreChart(raw: unknown): ChartState {
  const base = emptyChart();
  if (!raw || typeof raw !== "object") return base;
  const o = raw as Record<string, unknown>;
  const layers: Layer[] = [];
  let id = 1;
  for (const x of Array.isArray(o.layers) ? o.layers.slice(0, MAX_LAYERS) : []) {
    if (!x || typeof x !== "object") continue;
    const l = x as Record<string, unknown>;
    const common = {
      id,
      transform: TRANSFORM_SET.has(l.transform as Transform) ? (l.transform as Transform) : "ttm",
      type: (["line", "bar", "area"] as const).find((t) => t === l.type) ?? "line",
      axis: l.axis === "R" ? ("R" as const) : ("L" as const),
      ...(l.hidden === true ? { hidden: true } : {}),
    };
    if (l.kind === "metric" && CIK.test(String(l.companyId)) && /^[a-z_]{1,64}$/.test(String(l.metric))) {
      layers.push({ ...common, kind: "metric", companyId: String(l.companyId), metric: String(l.metric) });
      id++;
    } else if (l.kind === "formula" && typeof l.formula === "string" && l.formula.length <= 500) {
      layers.push({
        ...common,
        kind: "formula",
        formula: l.formula,
        ...(CIK.test(String(l.defaultCompany)) ? { defaultCompany: String(l.defaultCompany) } : {}),
      });
      id++;
    }
  }
  return {
    title: typeof o.title === "string" ? o.title.slice(0, 200) : "",
    layers,
    range: RANGES.includes(o.range as Range) ? (o.range as Range) : base.range,
    align: o.align === "fiscal" ? "fiscal" : "calendar",
    nextId: id,
  };
}
