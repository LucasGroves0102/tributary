/** Chart builder state and how each layer becomes one /v1/formulas/evaluate request. Pure, so
 * the request rules (transforms, alignment, range) are tested without rendering.
 *
 * Every layer is a formula: a metric layer is the bare metric with its company as
 * default_company. Transforms: quarterly = view quarter; TTM = view ttm; YoY = (f) / (f)[-4q] - 1
 * over TTM values; QoQ = (f) / (f)[-1q] - 1 over quarterly values; indexed = TTM values divided
 * by the first one in range, times 100 (done here, since it depends on the range); per share =
 * ttm(f) / weighted_shares_diluted in the quarter view (TTM dollars over the quarter's diluted
 * share count). Metrics without TTM values (EPS, weighted shares) use quarters for YoY, indexed
 * and per share. */

import type { FormulaRequest } from "../api";

export type Transform = "q" | "ttm" | "yoy" | "qoq" | "idx" | "ps";
/** sbar / sarea stack on the other layers of the same type and axis */
export type LayerType = "line" | "bar" | "area" | "sbar" | "sarea";
export type Axis = "L" | "R";
export type Align = "calendar" | "fiscal";
/** years back, or "max" for everything the API has; 2 is kept for charts saved before 1/10/max */
export type Range = 1 | 2 | 3 | 5 | 10 | "max";

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
  /** legend name instead of the automatic one */
  name?: string;
  /** series colour 0..7 (tokens.css --series-N); unset = the layer's position */
  color?: number;
}

export interface ChartState {
  title: string;
  layers: Layer[];
  range: Range;
  align: Align;
  /** 10-K / 10-Q / 8-K results markers on the time axis */
  markers?: boolean;
  nextId: number;
}

export const TRANSFORMS: [Transform, string][] = [
  ["q", "Quarterly"],
  ["ttm", "TTM"],
  ["yoy", "YoY %"],
  ["qoq", "QoQ %"],
  ["idx", "Indexed"],
  ["ps", "Per share"],
];
export const LAYER_TYPES: [LayerType, string][] = [
  ["line", "Line"],
  ["bar", "Bar"],
  ["area", "Area"],
  ["sbar", "Stacked bar"],
  ["sarea", "Stacked area"],
];
export const RANGES: Range[] = [1, 3, 5, 10, "max"];
const VALID_RANGES: Range[] = [1, 2, 3, 5, 10, "max"];
export const MAX_LAYERS = 12;
export const COLOR_NAMES = ["Blue", "Green", "Orange", "Red", "Teal", "Violet", "Pink", "Grey"];
/** The diluted share count per-share layers divide by. */
export const SHARES_METRIC = "weighted_shares_diluted";

export const rangeLabel = (r: Range) => (r === "max" ? "Max" : `${r}y`);

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
 * quarter of the range is included; null for "max". */
export function fromDate(range: Range, today: Date = new Date()): string | null {
  if (range === "max") return null;
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
  let view: "quarter" | "ttm" = l.transform === "q" || l.transform === "qoq" || !ttm ? "quarter" : "ttm";
  if (l.transform === "yoy") formula = `(${formula}) / (${formula})[-4q] - 1`;
  if (l.transform === "qoq") formula = `(${formula}) / (${formula})[-1q] - 1`;
  if (l.transform === "ps") {
    // the share count has quarterly values only, so the quarter view with the dollars as TTM
    formula = `${ttm ? `ttm(${formula})` : `(${formula})`} / ${SHARES_METRIC}`;
    view = "quarter";
  }
  const req: FormulaRequest = { formula, basis: basis(s), view };
  const from = fromDate(s.range, today);
  if (from) req.from = from;
  const company = l.kind === "metric" ? l.companyId : l.defaultCompany;
  if (company) req.default_company = company;
  return req;
}

/** A layer's legend name: its own name, or `short` (a company's ticker or name) and the
 * metric or formula with the transform. */
export function layerName(l: Layer, short: (companyId: string) => string, label: (metric: string) => string): string {
  if (l.name?.trim()) return l.name.trim();
  return autoName(l, short, label);
}

export function autoName(l: Layer, short: (companyId: string) => string, label: (metric: string) => string): string {
  const base =
    l.kind === "metric"
      ? `${l.companyId ? short(l.companyId) : "?"} ${l.metric ? label(l.metric).toLowerCase() : "?"}`
      : (l.formula ?? "").trim() || "formula";
  const suffix = { q: "", ttm: " (TTM)", yoy: " YoY", qoq: " QoQ", idx: " (indexed)", ps: " per share" }[l.transform];
  return base + suffix;
}

/** The colour a layer draws in: its own, or its position. */
export function layerColor(l: Layer, index: number): number {
  return (l.color ?? index) % COLOR_NAMES.length;
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

/** Colours the chart tells apart (tokens.css --series-0..7): "Chart these" from Compare draws
 * at most this many companies. */
export const MAX_COMPANY_LINES = 8;

/** One line per company for a metric (Compare's "Chart these"), on calendar quarters so
 * different fiscal years line up. */
export function companiesChart(
  s: ChartState,
  companyIds: string[],
  metric: string,
  transform: Transform,
  title: string,
): ChartState {
  let id = s.nextId;
  const layers = companyIds.slice(0, MAX_COMPANY_LINES).map(
    (companyId): Layer => ({ id: id++, kind: "metric", companyId, metric, transform, type: "line", axis: "L" }),
  );
  return { ...s, title, align: "calendar", layers, nextId: id };
}

type TemplateLayer = Pick<Layer, "transform" | "type" | "axis"> & { metric?: string; formula?: string; name?: string };

export interface Template {
  label: string;
  title: string;
  /** what it shows, for the button's hover */
  hint: string;
  /** "each": the first layer once per company in the chart (up to 4); "one": every layer for
   * the chart's first company */
  per: "each" | "one";
  layers: TemplateLayer[];
}

/** Templates use only metrics the formula catalog has (query-api formula/check.py CATALOG). */
export const TEMPLATES: Template[] = [
  { label: "Margins", title: "Operating margin, TTM", per: "each", hint: "Operating margin, TTM, one line per company",
    layers: [{ metric: "operating_margin", transform: "ttm", type: "line", axis: "L" }] },
  { label: "Revenue growth", title: "Revenue growth, YoY (TTM)", per: "each", hint: "Revenue growth over TTM, one line per company",
    layers: [{ metric: "revenue", transform: "yoy", type: "line", axis: "L" }] },
  { label: "Free cash flow", title: "Free cash flow, TTM", per: "each", hint: "Free cash flow, TTM, one line per company",
    layers: [{ metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L" }] },
  { label: "Leverage", title: "Debt to equity, quarter end", per: "each", hint: "Debt to equity at each quarter end, one line per company",
    layers: [{ metric: "debt_to_equity", transform: "q", type: "line", axis: "L" }] },
  { label: "Cash-flow quality", title: "Cash-flow quality: operating cash flow vs net income, TTM", per: "one",
    hint: "Operating cash flow and net income (TTM bars), and their ratio (right axis)",
    layers: [
      { metric: "operating_cash_flow", transform: "ttm", type: "bar", axis: "L" },
      { metric: "net_income", transform: "ttm", type: "bar", axis: "L" },
      { formula: "operating_cash_flow / net_income", name: "Cash conversion (OCF / net income)", transform: "ttm", type: "line", axis: "R" },
    ] },
  { label: "Balance-sheet strength", title: "Balance-sheet strength: cash vs debt, quarter end", per: "one",
    hint: "Cash and long-term debt (bars) and the current ratio (right axis), at quarter ends",
    layers: [
      { metric: "cash", transform: "q", type: "bar", axis: "L" },
      { metric: "long_term_debt", transform: "q", type: "bar", axis: "L" },
      { metric: "current_ratio", transform: "q", type: "line", axis: "R" },
    ] },
  { label: "Capital return", title: "Capital return: dividends and buybacks vs free cash flow, TTM", per: "one",
    hint: "Dividends and buybacks stacked, against free cash flow, TTM",
    layers: [
      { metric: "dividends_paid", transform: "ttm", type: "sbar", axis: "L" },
      { metric: "share_buybacks", transform: "ttm", type: "sbar", axis: "L" },
      { metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L" },
    ] },
];

/** A template over the chart's companies (or `fallback` if it has none). */
export function applyTemplate(s: ChartState, t: Template, fallback: string[]): ChartState {
  const inChart = [...new Set(s.layers.flatMap((l) => {
    const c = l.kind === "metric" ? l.companyId : l.defaultCompany;
    return c ? [c] : [];
  }))];
  const companies = (inChart.length ? inChart : fallback).slice(0, t.per === "one" ? 1 : 4);
  let id = s.nextId;
  const layers: Layer[] = companies.flatMap((companyId) =>
    (t.per === "one" ? t.layers : t.layers.slice(0, 1)).map((x): Layer => {
      const { metric, formula, ...style } = x;
      return formula !== undefined
        ? { id: id++, kind: "formula", formula, defaultCompany: companyId, ...style }
        : { id: id++, kind: "metric", companyId, metric, ...style };
    }),
  );
  return { ...s, title: t.title, layers, nextId: id };
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

/** Moves a layer up (-1) or down (+1) in the list (legend and drawing order). Colours stay
 * with their layers: each one keeps the colour it had before the move. */
export function moveLayer(s: ChartState, id: number, by: -1 | 1): ChartState {
  const i = s.layers.findIndex((l) => l.id === id);
  const j = i + by;
  if (i < 0 || j < 0 || j >= s.layers.length) return s;
  const layers = s.layers.map((l, k) => ({ ...l, color: layerColor(l, k) }));
  [layers[i], layers[j]] = [layers[j]!, layers[i]!];
  return { ...s, layers };
}

const TRANSFORM_SET = new Set(TRANSFORMS.map(([t]) => t));
const TYPE_SET = new Set(LAYER_TYPES.map(([t]) => t));
const CIK = /^\d{10}$/;
export const MAX_LAYER_NAME = 80;

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
    const name = typeof l.name === "string" ? l.name.replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, MAX_LAYER_NAME) : "";
    const common = {
      id,
      transform: TRANSFORM_SET.has(l.transform as Transform) ? (l.transform as Transform) : "ttm",
      type: TYPE_SET.has(l.type as LayerType) ? (l.type as LayerType) : "line",
      axis: l.axis === "R" ? ("R" as const) : ("L" as const),
      ...(l.hidden === true ? { hidden: true } : {}),
      ...(name ? { name } : {}),
      ...(Number.isInteger(l.color) && (l.color as number) >= 0 && (l.color as number) < COLOR_NAMES.length
        ? { color: l.color as number }
        : {}),
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
    range: VALID_RANGES.includes(o.range as Range) ? (o.range as Range) : base.range,
    align: o.align === "fiscal" ? "fiscal" : "calendar",
    ...(o.markers === true ? { markers: true } : {}),
    nextId: id,
  };
}
