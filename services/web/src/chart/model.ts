/** Chart builder state and how each layer becomes one /v1/formulas/evaluate request. Pure, so
 * the request rules (transforms, alignment, range) are tested without rendering.
 *
 * Every layer is a formula: a metric layer is the bare metric with its company as
 * default_company. Transforms: quarterly = view quarter; TTM = view ttm; YoY = (f) / (f)[-4q] - 1
 * over TTM values; QoQ = (f) / (f)[-1q] - 1 over quarterly values; indexed = TTM values divided
 * by the first one in range, times 100 (done here, since it depends on the range); per share =
 * ttm(f) / weighted_shares_diluted in the quarter view (TTM dollars over the quarter's diluted
 * share count). Metrics without TTM values (EPS, weighted shares) use quarters for YoY, indexed
 * and per share.
 *
 * Segment layers (kind "segment", made by the company Revenue view) are the exception: they
 * read /v1/companies/{id}/segments instead (`segmentRequest`; rules in chart/segments.ts), with
 * their own transforms (quarterly, TTM, YoY, contribution to growth). Price layers (kind
 * "price", "Connect your data") read the viewer's own prices (`priceRequest`, chart/prices.ts)
 * and put the chart on calendar quarters (`basis`). `layerKey` gives any of the requests as
 * the key chart/fetch.ts `fetchLayer` answers.
 *
 * A chart's title belongs to the user only when they typed one: `title` is that text, and empty
 * means `autoTitle` writes it from the visible layers, so it can't go stale (templates and the
 * Chart buttons leave it empty). Draw and export through `chartTitle`. A chart saved before this
 * keeps its stored title as a custom one. */

import type { FormulaRequest } from "../api";
import { PRICE_TRANSFORM_SET, type PriceRequest, type PriceTransform } from "./prices";
import { MAX_MEMBERS, MEMBER, memberName, SEGMENT_AXES, SEGMENT_TRANSFORMS, type SegmentAxisKind, type SegmentRequest } from "./segments";

/** contrib: segment layers only (chart/segments.ts) */
export type Transform = "q" | "ttm" | "yoy" | "qoq" | "idx" | "ps" | "contrib";
/** sbar / sarea stack on the other layers of the same type and axis */
export type LayerType = "line" | "bar" | "area" | "sbar" | "sarea";
export type Axis = "L" | "R";
export type Align = "calendar" | "fiscal";
/** years back, "max" for everything the API has, or "custom" (ChartState start / end); 2 is
 * kept for charts saved before 1/10/max */
export type Range = 1 | 2 | 3 | 5 | 10 | "max" | "custom";
/** quarterly: every quarter; annual: one point per year (see `request` and series.ts). */
export type Frequency = "quarterly" | "annual";
/** How a dimensionless layer reads (query-api formula/check.py). */
export type Display = "percent" | "multiple" | "number";

export interface Layer {
  id: number;
  /** segment: members of one segment axis from /segments (chart/segments.ts); price: the
   * viewer's own share prices (chart/prices.ts; companyId only) */
  kind: "metric" | "formula" | "segment" | "price";
  /** metric and segment layers: the company and metric (a segment metric for segment layers) */
  companyId?: string;
  metric?: string;
  /** formula layers: the expression and the company of metrics written without a ticker */
  formula?: string;
  defaultCompany?: string;
  /** segment layers: the axis and the members summed (QNames as last filed) */
  segmentAxis?: SegmentAxisKind;
  members?: string[];
  transform: Transform;
  type: LayerType;
  axis: Axis;
  hidden?: boolean;
  /** legend name instead of the automatic one */
  name?: string;
  /** series colour 0..7 (tokens.css --series-N); unset = the layer's position */
  color?: number;
  /** a dimensionless metric or formula layer: how it reads; unset = what the API says from its
   * formula's shape */
  display?: Display;
}

export interface ChartState {
  /** what the user typed; empty: `autoTitle` */
  title: string;
  layers: Layer[];
  range: Range;
  align: Align;
  /** 10-K / 10-Q / 8-K results markers on the time axis */
  markers?: boolean;
  /** range "custom": earliest and latest period end (ISO dates; either may be open) */
  start?: string;
  end?: string;
  /** unset: quarterly */
  frequency?: Frequency;
  /** deck output: light background, subtitle, units, source footnote */
  deck?: boolean;
  /** deck output's second line; unset: automatic (frequency, periods, units) */
  subtitle?: string;
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
export const DISPLAYS: [Display, string][] = [
  ["percent", "Percent"],
  ["multiple", "Multiple (x)"],
  ["number", "Plain number"],
];
export const RANGES: Range[] = [1, 3, 5, 10, "max"];
const VALID_RANGES: Range[] = [1, 2, 3, 5, 10, "max", "custom"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_LAYERS = 12;
export const COLOR_NAMES = ["Blue", "Green", "Orange", "Red", "Teal", "Violet", "Pink", "Grey"];
/** The diluted share count per-share layers divide by. */
export const SHARES_METRIC = "weighted_shares_diluted";

export const rangeLabel = (r: Range) => (r === "max" ? "Max" : r === "custom" ? "Custom" : `${r}y`);

export function emptyChart(): ChartState {
  return { title: "", layers: [], range: 3, align: "calendar", nextId: 1 };
}

/** Metric → whether it has TTM values (from /v1/screener/columns spans). */
export type TtmCatalog = (metric: string) => boolean;

/** What a formula's text reads: tickers (TICKER.metric), and whether any metric has no ticker,
 * which is all the default company is for. */
export function formulaReads(formula: string): { bare: boolean; tickers: string[] } {
  const tickers = new Set<string>();
  let bare = false;
  // a name not inside another word or after a dot; lag units ("4q") follow a digit
  for (const m of formula.matchAll(/(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*)(\.[A-Za-z0-9_]*)?(\s*\()?/g)) {
    if (m[3]) continue; // ttm( abs(
    if (m[2]) tickers.add(m[1]!.toUpperCase());
    else bare = true;
  }
  return { bare, tickers: [...tickers] };
}

/** Companies a layer reads: its company, or what its formula names (the default company only
 * when a metric goes without a ticker: a DECK-only formula doesn't read the company in the box). */
export function layerCompanies(l: Layer): string[] {
  if (l.kind === "metric" || l.kind === "segment" || l.kind === "price") return l.companyId ? [l.companyId] : [];
  const reads = formulaReads(l.formula ?? "");
  return [...(reads.bare && l.defaultCompany ? [l.defaultCompany] : []), ...reads.tickers.map((t) => `ticker:${t}`)];
}

/** Fiscal quarters only line up within one company (the API's alignment rule), so fiscal
 * applies only when every visible layer reads the same single company. Prices have no fiscal
 * calendar: a visible price layer puts the chart on calendar quarters. */
export function basis(s: ChartState): Align {
  if (s.align === "calendar") return "calendar";
  const visible = s.layers.filter((l) => !l.hidden);
  if (visible.some((l) => l.kind === "price")) return "calendar";
  const companies = new Set(visible.flatMap(layerCompanies));
  return companies.size <= 1 ? "fiscal" : "calendar";
}

/** Why a chart asked for fiscal quarters draws calendar ones (null when it doesn't). */
export function basisReason(s: ChartState): string | null {
  if (s.align !== "fiscal" || basis(s) === "fiscal") return null;
  const visible = s.layers.filter((l) => !l.hidden);
  if (visible.some((l) => l.kind === "price")) return "Calendar quarters: share prices have no fiscal calendar.";
  const companies = new Set(visible.flatMap(layerCompanies)).size;
  return `Calendar quarters: fiscal years only line up within one company, and this chart reads ${companies}.`;
}

/** Earliest period end in range: `years` back from today, plus a quarter so the oldest
 * quarter of the range is included; null for "max". */
export function fromDate(range: Range, today: Date = new Date()): string | null {
  if (range === "max" || range === "custom") return null;
  const d = new Date(Date.UTC(today.getUTCFullYear() - range, today.getUTCMonth() - 3, today.getUTCDate()));
  return d.toISOString().slice(0, 10);
}

/** The period ends a chart asks for: its range back from today, or the custom start / end. */
export function rangeDates(s: Pick<ChartState, "range" | "start" | "end">, today?: Date): { from: string | null; to: string | null } {
  if (s.range === "custom") return { from: s.start ?? null, to: s.end ?? null };
  return { from: fromDate(s.range, today), to: null };
}

export const frequencyOf = (s: Pick<ChartState, "frequency">): Frequency => s.frequency ?? "quarterly";

/** The evaluate request for a layer, or null if it isn't complete yet.
 *
 * Annual frequency keeps one point per year: the value at each year's fourth quarter (fiscal
 * years on fiscal quarters, calendar years on calendar quarters; series.ts `annualPoints`). So
 * flows are asked for as TTM (the TTM at Q4 is the year's total), balances (no TTM values)
 * as the quarter (the year-end value), ratios as TTM (the full-year ratio); QoQ becomes YoY
 * (year over year); the other transforms apply to those annual values. */
export function request(l: Layer, s: ChartState, hasTtm: TtmCatalog, today?: Date): FormulaRequest | null {
  let formula: string;
  let ttm: boolean;
  if (l.kind === "segment" || l.kind === "price") return null; // segmentRequest, priceRequest
  if (l.kind === "metric") {
    if (!l.companyId || !l.metric) return null;
    formula = l.metric;
    ttm = hasTtm(l.metric);
  } else {
    if (!l.formula?.trim()) return null;
    formula = l.formula.trim();
    ttm = true;
  }
  const annual = frequencyOf(s) === "annual";
  const transform = annual && l.transform === "qoq" ? "yoy" : l.transform;
  let view: "quarter" | "ttm" = (transform === "q" && !annual) || transform === "qoq" || !ttm ? "quarter" : "ttm";
  if (transform === "yoy") formula = `(${formula}) / (${formula})[-4q] - 1`;
  if (transform === "qoq") formula = `(${formula}) / (${formula})[-1q] - 1`;
  if (transform === "ps") {
    // the share count has quarterly values only, so the quarter view with the dollars as TTM
    formula = `${ttm ? `ttm(${formula})` : `(${formula})`} / ${SHARES_METRIC}`;
    view = "quarter";
  }
  const req: FormulaRequest = { formula, basis: basis(s), view };
  const { from, to } = rangeDates(s, today);
  if (from) req.from = from;
  if (to) req.to = to;
  const company = l.kind === "metric" ? l.companyId : l.defaultCompany;
  if (company) req.default_company = company;
  return req;
}

/** A segment layer's request (chart/segments.ts), or null if it isn't complete. Quarterly
 * values with quarterly frequency, else TTM (annual = the TTM at each Q4; YoY and contribution
 * over TTM values). */
export function segmentRequest(l: Layer, s: ChartState, today?: Date): SegmentRequest | null {
  if (l.kind !== "segment" || !l.companyId || !l.metric || !l.segmentAxis || !l.members?.length) return null;
  const transform = SEGMENT_TRANSFORM_SET.has(l.transform) ? (l.transform as SegmentRequest["segment"]["transform"]) : "ttm";
  const view = transform === "q" && frequencyOf(s) === "quarterly" ? "quarter" : "ttm";
  const { from, to } = rangeDates(s, today);
  return {
    segment: {
      company: l.companyId,
      axis: l.segmentAxis,
      metric: l.metric,
      members: l.members,
      view,
      basis: basis(s),
      transform,
      ...(from ? { from } : {}),
      ...(to ? { to } : {}),
    },
  };
}

/** A price layer's request (chart/prices.ts), or null without a company. */
export function priceRequest(l: Layer, s: ChartState, today?: Date): PriceRequest | null {
  if (l.kind !== "price" || !l.companyId) return null;
  const transform = PRICE_TRANSFORM_SET.has(l.transform) ? (l.transform as PriceTransform) : "q";
  const { from, to } = rangeDates(s, today);
  return { price: { company: l.companyId, transform, ...(from ? { from } : {}), ...(to ? { to } : {}) } };
}

/** What a layer's answer is cached under: its evaluate, segment or price request, as JSON
 * (chart/fetch.ts `fetchLayer` takes it back). */
export function layerKey(l: Layer, s: ChartState, hasTtm: TtmCatalog, today?: Date): string | null {
  const req = l.kind === "segment" ? segmentRequest(l, s, today)
    : l.kind === "price" ? priceRequest(l, s, today)
    : request(l, s, hasTtm, today);
  return req ? JSON.stringify(req) : null;
}

/** A layer's legend name: its own name, or `short` (a company's ticker or name) and the
 * metric or formula with the transform. */
export function layerName(l: Layer, short: (companyId: string) => string, label: (metric: string) => string, frequency: Frequency = "quarterly"): string {
  if (l.name?.trim()) return l.name.trim();
  return autoName(l, short, label, frequency);
}

const transformSuffix = (l: Layer, frequency: Frequency) => (frequency === "annual"
  ? { q: "", ttm: "", yoy: " YoY", qoq: " YoY", idx: " (indexed)", ps: " per share", contrib: " contribution" }
  : { q: "", ttm: " (TTM)", yoy: " YoY", qoq: " QoQ", idx: " (indexed)", ps: " per share", contrib: " contribution" })[l.transform];

/** A metric's label mid-sentence: "free cash flow", but "ROE", "R&D" and "FCF margin" as they are. */
const inSentence = (label: string) => (/[A-Z]{2}|&/.test(label) ? label : label.toLowerCase());

/** What a layer shows, without its company. */
function layerWhat(l: Layer, label: (metric: string) => string, frequency: Frequency): string {
  if (l.kind === "price") return l.transform === "yoy" ? "share price YoY" : l.transform === "idx" ? "share price (indexed)" : "share price";
  const base =
    l.kind === "metric"
      ? l.metric ? inSentence(label(l.metric)) : "?"
      : l.kind === "segment"
        ? l.members?.length === 1 ? memberName(l.members[0]!) : `${l.members?.length ?? 0} segments`
        : (l.formula ?? "").trim() || "formula";
  return base + transformSuffix(l, frequency);
}

export function autoName(l: Layer, short: (companyId: string) => string, label: (metric: string) => string, frequency: Frequency = "quarterly"): string {
  if (l.kind === "formula") return layerWhat(l, label, frequency);
  return `${l.companyId ? short(l.companyId) : "?"} ${layerWhat(l, label, frequency)}`;
}

const andList = (items: string[], most: number) => {
  const shown = items.length > most ? [...items.slice(0, most), `${items.length - most} more`] : items;
  return shown.length < 2 ? shown.join("") : `${shown.slice(0, -1).join(", ")} and ${shown.at(-1)}`;
};
const TITLE_METRICS = 3;
const TITLE_COMPANIES = 4;
const MAX_TITLE = 200;

/** The title a chart's visible layers give it: "AMZN: revenue (TTM) and net_income / revenue
 * (TTM)", "Operating margin (TTM): KO, PEP and KDP", with ", annual" when it is. Empty without a
 * visible layer. A formula naming its tickers has no company to put in front of it. `shows` leaves
 * out layers known to draw nothing, so it names what is there (unless nothing would be left). */
export function autoTitle(s: ChartState, short: (companyId: string) => string, label: (metric: string) => string, shows?: (l: Layer) => boolean): string {
  const frequency = frequencyOf(s);
  // a formula layer nothing has been typed into isn't anything yet
  const visible = s.layers.filter((l) => !l.hidden && (l.kind !== "formula" || l.formula?.trim()));
  const named = shows && visible.some(shows) ? visible.filter(shows) : visible;
  const parts = named.map((l) => {
    const name = l.name?.trim();
    const what = name || layerWhat(l, label, frequency);
    const company = l.kind === "formula"
      ? (formulaReads(l.formula ?? "").tickers.length ? undefined : l.defaultCompany)
      : l.companyId;
    const who = company ? short(company) : null;
    return { who: who && !name?.includes(who) ? who : null, what };
  });
  if (!parts.length) return "";
  const whos = [...new Set(parts.flatMap((p) => (p.who ? [p.who] : [])))];
  const whats = [...new Set(parts.map((p) => p.what))];
  const title = whats.length === 1 && whos.length > 1
    ? `${whats[0]!.charAt(0).toUpperCase()}${whats[0]!.slice(1)}: ${andList(whos, TITLE_COMPANIES)}`
    : `${whos.length ? `${andList(whos, TITLE_COMPANIES)}: ` : ""}${andList(whats, TITLE_METRICS)}`;
  return `${title}${frequency === "annual" ? ", annual" : ""}`.slice(0, MAX_TITLE);
}

/** What a chart is called on screen and in its exports: the user's title, or the automatic one. */
export function chartTitle(s: ChartState, short: (companyId: string) => string, label: (metric: string) => string, shows?: (l: Layer) => boolean): string {
  return s.title.trim() || autoTitle(s, short, label, shows);
}

/** The colour a layer draws in: its own, or its position. */
export function layerColor(l: Layer, index: number): number {
  return (l.color ?? index) % COLOR_NAMES.length;
}

/** Revenue (quarterly bars) and operating margin (TTM line, right axis) for one company, on
 * its fiscal quarters: the chart "Chart" buttons open. */
export function companyChart(s: ChartState, companyId: string): ChartState {
  let id = s.nextId;
  return {
    ...s,
    title: "",
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
): ChartState {
  let id = s.nextId;
  const layers = companyIds.slice(0, MAX_COMPANY_LINES).map(
    (companyId): Layer => ({ id: id++, kind: "metric", companyId, metric, transform, type: "line", axis: "L" }),
  );
  return { ...s, title: "", align: "calendar", layers, nextId: id };
}

type TemplateLayer = Pick<Layer, "transform" | "type" | "axis"> & { metric?: string; formula?: string; name?: string };

export interface Template {
  label: string;
  /** what it shows, for the button's hover */
  hint: string;
  /** "each": the first layer once per company in the chart (up to 4); "one": every layer for
   * the chart's first company */
  per: "each" | "one";
  layers: TemplateLayer[];
}

/** Templates use only metrics the formula catalog has (query-api formula/check.py CATALOG). */
export const TEMPLATES: Template[] = [
  { label: "Margins", per: "each", hint: "Operating margin, TTM, one line per company",
    layers: [{ metric: "operating_margin", transform: "ttm", type: "line", axis: "L" }] },
  { label: "Revenue growth", per: "each", hint: "Revenue growth over TTM, one line per company",
    layers: [{ metric: "revenue", transform: "yoy", type: "line", axis: "L" }] },
  { label: "Free cash flow", per: "each", hint: "Free cash flow, TTM, one line per company",
    layers: [{ metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L" }] },
  { label: "Leverage", per: "each", hint: "Debt to equity at each quarter end, one line per company",
    layers: [{ metric: "debt_to_equity", transform: "q", type: "line", axis: "L" }] },
  { label: "Cash-flow quality", per: "one",
    hint: "Operating cash flow and net income (TTM bars), and their ratio (right axis)",
    layers: [
      { metric: "operating_cash_flow", transform: "ttm", type: "bar", axis: "L" },
      { metric: "net_income", transform: "ttm", type: "bar", axis: "L" },
      { formula: "operating_cash_flow / net_income", name: "Cash conversion (OCF / net income)", transform: "ttm", type: "line", axis: "R" },
    ] },
  { label: "Balance-sheet strength", per: "one",
    hint: "Cash and long-term debt (bars) and the current ratio (right axis), at quarter ends",
    layers: [
      { metric: "cash", transform: "q", type: "bar", axis: "L" },
      { metric: "long_term_debt", transform: "q", type: "bar", axis: "L" },
      { metric: "current_ratio", transform: "q", type: "line", axis: "R" },
    ] },
  { label: "Capital return", per: "one",
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
  return { ...s, title: "", layers, nextId: id };
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

const TRANSFORM_SET = new Set<Transform>(TRANSFORMS.map(([t]) => t));
const SEGMENT_TRANSFORM_SET = new Set<Transform>(SEGMENT_TRANSFORMS.map(([t]) => t));
const TYPE_SET = new Set(LAYER_TYPES.map(([t]) => t));
const DISPLAY_SET = new Set(DISPLAYS.map(([d]) => d));
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
    const transforms = l.kind === "segment" ? SEGMENT_TRANSFORM_SET : l.kind === "price" ? PRICE_TRANSFORM_SET : TRANSFORM_SET;
    const common = {
      id,
      transform: transforms.has(l.transform as Transform) ? (l.transform as Transform)
        : l.kind === "price" ? ("q" as Transform) : ("ttm" as Transform),
      type: TYPE_SET.has(l.type as LayerType) ? (l.type as LayerType) : "line",
      axis: l.axis === "R" ? ("R" as const) : ("L" as const),
      ...(l.hidden === true ? { hidden: true } : {}),
      ...(name ? { name } : {}),
      ...(Number.isInteger(l.color) && (l.color as number) >= 0 && (l.color as number) < COLOR_NAMES.length
        ? { color: l.color as number }
        : {}),
      ...((l.kind === "metric" || l.kind === "formula") && DISPLAY_SET.has(l.display as Display)
        ? { display: l.display as Display }
        : {}),
    };
    if (l.kind === "metric" && CIK.test(String(l.companyId)) && /^[a-z_]{1,64}$/.test(String(l.metric))) {
      layers.push({ ...common, kind: "metric", companyId: String(l.companyId), metric: String(l.metric) });
      id++;
    } else if (l.kind === "price" && CIK.test(String(l.companyId))) {
      layers.push({ ...common, kind: "price", companyId: String(l.companyId) });
      id++;
    } else if (
      l.kind === "segment" && CIK.test(String(l.companyId)) && /^[a-z_]{1,64}$/.test(String(l.metric))
      && SEGMENT_AXES.includes(l.segmentAxis as SegmentAxisKind) && Array.isArray(l.members)
      && l.members.length >= 1 && l.members.length <= MAX_MEMBERS && l.members.every((m) => typeof m === "string" && MEMBER.test(m))
    ) {
      layers.push({
        ...common,
        kind: "segment",
        companyId: String(l.companyId),
        metric: String(l.metric),
        segmentAxis: l.segmentAxis as SegmentAxisKind,
        members: [...new Set(l.members as string[])],
      });
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
  const iso = (v: unknown) => (typeof v === "string" && ISO_DATE.test(v) ? v : undefined);
  const start = iso(o.start);
  const end = iso(o.end);
  const subtitle = typeof o.subtitle === "string" ? o.subtitle.replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 200) : "";
  return {
    title: typeof o.title === "string" ? o.title.slice(0, 200) : "",
    layers,
    range: VALID_RANGES.includes(o.range as Range) ? (o.range as Range) : base.range,
    align: o.align === "fiscal" ? "fiscal" : "calendar",
    ...(o.markers === true ? { markers: true } : {}),
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
    ...(o.frequency === "annual" ? { frequency: "annual" as const } : {}),
    ...(o.deck === true ? { deck: true } : {}),
    ...(subtitle ? { subtitle } : {}),
    nextId: id,
  };
}
