/** Evaluate responses → plotted series on one shared quarter axis, and the CSV export. Exact
 * decimals stay strings (`raw`) for the CSV; `y` is the parsed number for drawing. */

import type { FinancialRow, FormulaSeries } from "../types";
import { POINT_IN_TIME } from "./metrics";
import type { Axis, Display, LayerType, Transform } from "./model";

export type { Axis };

export interface Source {
  form: string;
  filed: string;
  accessionNo: string;
  url: string | null;
  periodEnd: string;
  /** the company that filed it, when known (opens the filing in the reader) */
  companyId?: string;
}

export interface Point {
  key: number;
  /** "CY2026 Q3" / "FY2027 Q2" */
  period: string;
  periodEnd: string;
  y: number | null;
  /** the API's exact decimal, or the computed index for indexed layers */
  raw: string;
  sources: Source[];
  /** an annual chart's latest point when it isn't a fiscal year end (F5): the trailing twelve months
   * to the newest quarter ("ttm"), or that quarter's balance ("quarter"). Drawn lighter, marked "*". */
  partial?: "ttm" | "quarter";
  /** why the value isn't drawn: it reads n/m by the tables' rule (the API's reason, F7a) */
  notMeaningful?: string;
}

/** "Operating margin reads n/m in FY2024 Q1 and FY2024 Q2 (revenue is under $100K, too small a
 * base): left as gaps." for the points of a series the API marked not meaningful, else null. */
export function notMeaningfulNote(name: string, points: Point[]): string | null {
  const nm = points.filter((p) => p.notMeaningful);
  if (!nm.length) return null;
  const reasons = [...new Set(nm.map((p) => p.notMeaningful!))];
  const when = nm.length <= 3 ? `in ${nm.map((p) => p.period).join(nm.length === 2 ? " and " : ", ")}` : `in ${nm.length} periods`;
  const why = reasons.length === 1 ? reasons[0] : `${reasons[0]}, and ${reasons.length - 1} other reason${reasons.length > 2 ? "s" : ""}`;
  return `${name} reads n/m ${when} (${why}): left as ${nm.length === 1 ? "a gap" : "gaps"}, as in the tables.`;
}

export interface Series {
  id: number;
  name: string;
  /** API unit (USD, USD/shares, ...); a dimensionless one as it reads: "ratio" a percent,
   * "multiple" or "number" (the API's `display` or the layer's own); "pct" for a change, "pts" for
   * points of growth, "index" for indexed */
  unit: string;
  color: number;
  type: LayerType;
  axis: Axis;
  points: Point[];
  /** drawn dashed whatever its axis (a model's forecast); unset: dashed on the right axis */
  dashed?: boolean;
  /** drawn lighter and thinner: a model's bear / bull case beside its base case */
  faint?: boolean;
  /** something to say about this series under the chart (chart/notes.ts) */
  note?: string;
}

export const COLORS = 8;

/** Annual frequency: the fourth quarter of each year, labelled by the year ("FY2025 Q4" →
 * "FY2025"). With the TTM view that is the year's total for flows and the full-year ratio for
 * ratios; with the quarter view (balances) the year-end value. Years whose Q4 isn't in range
 * are left out rather than made up from fewer quarters.
 *
 * The year in progress (F5, T1's "no latest TTM or quarter on the annual charts"): when the newest
 * point is a quarter after the last fiscal year end, it stays, keeping its quarter label ("FY2027
 * Q2") and marked `partial`: the trailing twelve months to it with the TTM view, its balance with the
 * quarter view. It is never called a year. */
export function annualPoints(points: Point[], partial: "ttm" | "quarter" | null = null): Point[] {
  const years = points.filter((p) => / Q4$/.test(p.period)).map((p) => ({ ...p, period: p.period.replace(/ Q4$/, "") }));
  if (!partial) return years;
  const newest = points.reduce<Point | null>((a, b) => (b.y !== null && (!a || b.key > a.key) ? b : a), null);
  const lastYear = years.at(-1)?.key ?? -Infinity;
  return newest && !/ Q4$/.test(newest.period) && newest.key > lastYear ? [...years, { ...newest, partial }] : years;
}

export function toSeries(
  data: FormulaSeries,
  layer: { id: number; transform: Transform; type: LayerType; axis: Axis; display?: Display },
  name: string,
  color: number,
  frequency: "quarterly" | "annual" = "quarterly",
): Series {
  let points: Point[] = data.points.map((p) => {
    const y = Number(p.value);
    const seen = new Map<string, Source>();
    for (const x of p.inputs) {
      const companyId = data.inputs.find((i) => i.input === x.input)?.company_id ?? undefined;
      seen.set(x.citation.accession_no, {
        form: x.citation.form,
        filed: x.citation.filed,
        accessionNo: x.citation.accession_no,
        url: x.citation.filing_url,
        periodEnd: x.period_end,
        ...(companyId ? { companyId } : {}),
      });
    }
    // a ratio that reads n/m in the tables (035) is a gap here too, never an absurd value (F7a)
    const nm = typeof p.not_meaningful === "string" && p.not_meaningful ? p.not_meaningful : null;
    return {
      key: p.period_key,
      period: p.period,
      periodEnd: p.period_end,
      y: Number.isFinite(y) && !nm ? y : null,
      raw: p.value,
      sources: [...seen.values()],
      ...(nm ? { notMeaningful: nm } : {}),
    };
  });
  // the year in progress: TTM to the newest quarter, or its balance when every input is one
  if (frequency === "annual") {
    const flows = (data.view === "ttm" || /\bttm\(/.test(data.formula ?? "")) && data.inputs.some((i) => !POINT_IN_TIME.has(i.metric));
    points = annualPoints(points, flows ? "ttm" : "quarter");
  }
  let unit = layer.transform === "yoy" || layer.transform === "qoq" ? "pct"
    : layer.transform === "contrib" ? "pts"
    : data.unit === "ratio" ? readsAs(layer.display ?? data.display)
    : data.unit;
  let note: string | undefined;
  if (layer.transform === "idx") {
    // index to the first value in range; meaningless from zero or a negative base
    const base = points.find((p) => p.y !== null)?.y ?? null;
    points = points.map((p) => {
      const y = base !== null && base > 0 && p.y !== null ? (p.y / base) * 100 : null;
      return { ...p, y, raw: y === null ? "" : String(Number(y.toPrecision(10))) };
    });
    unit = "index";
    if (base !== null && base <= 0) note = `${name} can't be indexed: its first value in this range is zero or negative.`;
    else if (points.some((p) => p.y !== null && p.y < 0)) note = `${name} is below 0 where the value itself is negative: read those periods as losses, not as an index.`;
  }
  const nm = notMeaningfulNote(name, points);
  if (nm) note = note ? `${note} ${nm}` : nm;
  return { id: layer.id, name, unit, color: color % COLORS, type: layer.type, axis: layer.axis, points, ...(note ? { note } : {}) };
}

/** The unit a dimensionless value reads in (an API without `display` meant a percent). */
function readsAs(display: Display | null | undefined): string {
  return display === "multiple" ? "multiple" : display === "number" ? "number" : "ratio";
}

export interface Tick {
  key: number;
  period: string;
  /** on the axis only because periods either side are: no series has it */
  gap?: boolean;
  /** an annual chart's year in progress (a `partial` point): not a fiscal year */
  partial?: boolean;
}

const LABEL = /^(FY|CY)(\d{4})(?: Q([1-4]))?$/;
/** far past any real chart: a key that isn't a period doesn't fill an axis with thousands of ticks */
const MOST_TICKS = 400;

/** Every period from the first any series has to the last, oldest first, labelled by the first
 * series that has it. A period none of them has is still on the axis (`gap`), named like its
 * neighbours: a missing year or quarter shows as a hole, it doesn't close up. Annual series tick
 * every fourth key (annualPoints keeps the Q4s). Keys that aren't quarters since year 0 (a model's
 * fiscal years, its FY2027E) aren't periods to fill. */
export function xAxis(series: Series[]): Tick[] {
  const out = new Map<number, string>();
  const partial = new Set<number>();
  for (const s of series) {
    for (const p of s.points) {
      if (!out.has(p.key)) out.set(p.key, p.period);
      if (p.partial) partial.add(p.key);
    }
  }
  const known = [...out.entries()].sort((a, b) => a[0] - b[0])
    .map(([key, period]): Tick => (partial.has(key) ? { key, period, partial: true } : { key, period }));
  const parsed = known.map((t) => LABEL.exec(t.period));
  // a year in progress keeps its quarter's label on an annual axis
  const annual = parsed.every((m, i) => m && (!m[3] || known[i]!.partial));
  const periods = parsed.every((m, i) => m && Number(m[2]) * 4 + (m[3] ? Number(m[3]) - 1 : 3) === known[i]!.key);
  if (known.length < 2 || !periods) return known;
  const step = annual ? 4 : 1;
  if ((known.at(-1)!.key - known[0]!.key) / step > MOST_TICKS) return known;
  const ticks: Tick[] = [];
  for (let i = 0; i < known.length; i++) {
    ticks.push(known[i]!);
    const prefix = parsed[i]![1]!;
    for (let key = known[i]!.key + step; i + 1 < known.length && key < known[i + 1]!.key; key += step) {
      const year = Math.floor(key / 4);
      ticks.push({ key, period: annual ? `${prefix}${year}` : `${prefix}${year} Q${(key % 4) + 1}`, gap: true });
    }
  }
  return ticks;
}

export function pointAt(s: Series, key: number): Point | undefined {
  return s.points.find((p) => p.key === key);
}

/** "10-Q filed 2026-08-26, 0001045810-26-000075" for each filing behind a point. */
export function sourceText(p: Point): string {
  return p.sources.map((s) => `${s.form} filed ${s.filed}, ${s.accessionNo}`).join("; ");
}

/** The latest filing behind any plotted point, for the source footnote. */
export function latestSource(series: Series[]): Source | null {
  let best: Source | null = null;
  for (const s of series) {
    for (const p of s.points) {
      for (const src of p.sources) if (!best || src.filed > best.filed) best = src;
    }
  }
  return best;
}

const NUMBER = /^-?\d+(\.\d+)?(E[+-]?\d+)?$/i;

/** One CSV cell: numbers as they are; text quoted when needed and kept from being read as a
 * spreadsheet formula (=, +, -, @ prefixes). */
export function csvCell(v: string): string {
  if (NUMBER.test(v)) return v;
  const safe = /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** What an annual chart's year in progress is, for its footnote (F5): null without one. */
export function partialNote(series: Series[]): string | null {
  const points = series.flatMap((s) => s.points.filter((p) => p.partial));
  if (!points.length) return null;
  const periods = [...new Set(points.map((p) => p.period))].join(", ");
  const kinds = new Set(points.map((p) => p.partial));
  const what = kinds.size > 1
    ? "flows are the trailing twelve months to it, balances its quarter end"
    : kinds.has("ttm") ? "the trailing twelve months to it" : "its quarter end";
  const year = points.every((p) => p.period.startsWith("CY")) ? "calendar year" : "fiscal year";
  return `* ${periods}: the latest quarter, not a full ${year} (${what}).`;
}

/** Period, then per layer its value (exact decimal from the API) and source accessions. */
export function chartCsv(series: Series[]): string {
  const axis = xAxis(series);
  const header = ["period", ...series.flatMap((s) => [`${s.name} (${s.unit})`, `${s.name} sources`])];
  const rows = axis.map(({ key, period }) => [
    period,
    ...series.flatMap((s) => {
      const p = pointAt(s, key);
      return [p?.raw ?? "", p ? p.sources.map((x) => x.accessionNo).join(" ") : ""];
    }),
  ]);
  return [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** "FY2027 Q2" → the formula API's period_key (year * 4 + quarter - 1); null if unlabelled. */
export function periodKey(period: string | null | undefined): number | null {
  const m = /^(?:FY|CY)(\d{4}) Q([1-4])$/.exec(period ?? "");
  return m ? Number(m[1]) * 4 + Number(m[2]) - 1 : null;
}

/** One metric's /financials rows as a series on fiscal quarters (rows without a fiscal period
 * are left out), each point citing its filing. */
export function financialSeries(
  rows: FinancialRow[],
  metric: string,
  layer: { id: number; name: string; color: number; type: LayerType; axis: Axis },
  companyId?: string,
): Series {
  const points: Point[] = rows
    .filter((r) => r.metric === metric)
    .flatMap((r) => {
      const key = periodKey(r.fiscal_period);
      if (key === null) return [];
      const y = Number(r.value);
      return [{
        key,
        period: r.fiscal_period!,
        periodEnd: r.period_end,
        y: Number.isFinite(y) ? y : null,
        raw: r.value,
        sources: [{
          form: r.citation.form,
          filed: r.citation.filed,
          accessionNo: r.citation.accession_no,
          url: r.citation.filing_url,
          periodEnd: r.period_end,
          ...(companyId ? { companyId } : {}),
        }],
      }];
    })
    .sort((a, b) => a.key - b.key);
  const unit = rows.find((r) => r.metric === metric)?.unit ?? "";
  return { ...layer, color: layer.color % COLORS, unit, points };
}
