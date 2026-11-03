/** Price layers ("Connect your data"): a chart layer over the viewer's OWN end-of-day prices
 * (`/v1/companies/{id}/prices`, fetched with their own market-data key), turned into the same
 * FormulaSeries shape as every other layer so LayerChart, CSV and embeds draw it unchanged.
 * A saved chart stores only the company and the transform: whoever opens it sees their own
 * prices, or nothing if they have no connection. Pure: tested without fetching.
 *
 * Points are calendar quarters (prices have no fiscal calendar, so a chart with a visible
 * price layer is drawn on calendar quarters: model.ts `basis`). A quarter's value is its last
 * trading day's close (for the quarter in progress, the latest close):
 *   q   quarter-end close (as traded, not adjusted)
 *   yoy adjusted close / adjusted close four quarters earlier - 1 (splits and dividends)
 *   idx adjusted closes, indexed to the first in range by series.ts (= 100)
 * Adjusted closes fall back to the close when the provider has none. Price points cite no
 * filing (sources are empty); the chart's footnote names the provider. */

import type { FormulaPoint, FormulaSeries, PriceRow } from "../types";

export type PriceTransform = "q" | "yoy" | "idx";
export const PRICE_TRANSFORMS: [PriceTransform, string][] = [
  ["q", "Quarter-end close"],
  ["yoy", "YoY %"],
  ["idx", "Indexed"],
];
export const PRICE_TRANSFORM_SET = new Set<string>(PRICE_TRANSFORMS.map(([t]) => t));

/** What a price layer asks for: the key the chart caches its answer under. */
export interface PriceRequest {
  price: {
    company: string;
    transform: PriceTransform;
    from?: string;
    to?: string;
  };
}

const num = (v: string | null): number | null => {
  if (v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
};

/** "2026-10-30" → calendar quarter key (year × 4 + quarter - 1) and label "CY2026 Q4". */
export function quarterOf(day: string): { key: number; period: string } {
  const year = Number(day.slice(0, 4));
  const q = Math.floor((Number(day.slice(5, 7)) - 1) / 3);
  return { key: year * 4 + q, period: `CY${year} Q${q + 1}` };
}

export function priceSeries(req: PriceRequest, rows: PriceRow[], asOf = ""): FormulaSeries {
  const r = req.price;
  // the last trading day of each calendar quarter
  const last = new Map<number, { row: PriceRow; period: string }>();
  for (const row of rows) {
    if (num(row.close) === null) continue;
    const { key, period } = quarterOf(row.day);
    const seen = last.get(key);
    if (!seen || row.day > seen.row.day) last.set(key, { row, period });
  }
  const adj = (row: PriceRow) => num(row.adj_close) ?? num(row.close)!;
  const points: FormulaPoint[] = [];
  for (const key of [...last.keys()].sort((a, b) => a - b)) {
    const { row, period } = last.get(key)!;
    if ((r.from && row.day < r.from) || (r.to && row.day > r.to)) continue;
    let value: string;
    if (r.transform === "q") value = row.close;
    else if (r.transform === "idx") value = String(adj(row));
    else {
      const before = last.get(key - 4);
      if (!before) continue;
      value = String(Number((adj(row) / adj(before.row) - 1).toPrecision(15)));
    }
    points.push({ period, period_key: key, period_end: row.day, value, inputs: [], source_accessions: [] });
  }
  return {
    formula: "price",
    unit: "USD/shares",
    basis: "calendar",
    view: "quarter",
    alignment: "calendar",
    as_of: asOf,
    inputs: [],
    points,
  };
}
