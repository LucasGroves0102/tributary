/** A chart layer's data by its key (model.ts `layerKey`): formula and metric layers through
 * `/v1/formulas/evaluate`, segment layers through `/v1/companies/{id}/segments` (and, for
 * `contrib`, the company's total from `/financials`), turned into the same FormulaSeries shape
 * (chart/segments.ts). The segment and total answers are shared by every member layer of one
 * chart: asked once per (token, company, axis, metric, view, basis) while the page lives. */

import { evaluateFormula, financialsView, segmentsOf, type FormulaRequest } from "../api";
import type { FinancialRow, FormulaSeries } from "../types";
import { SEGMENT_QUARTERS, segmentSeries, type SegmentRequest } from "./segments";

let owner: string | null = null;
const shared = new Map<string, Promise<unknown>>();

function once<T>(token: string, key: string, ask: () => Promise<T>): Promise<T> {
  if (owner !== token) {
    shared.clear();
    owner = token;
  }
  let p = shared.get(key) as Promise<T> | undefined;
  if (!p) {
    p = ask();
    p.catch(() => shared.delete(key)); // a failure is asked again next time
    shared.set(key, p);
  }
  return p;
}

/** For tests. */
export function clearShared(): void {
  shared.clear();
  owner = null;
}

export async function fetchLayer(token: string, key: string): Promise<FormulaSeries> {
  const req = JSON.parse(key) as FormulaRequest | SegmentRequest;
  if (!("segment" in req)) return evaluateFormula(token, req);
  const r = req.segment;
  const rows = once(token, JSON.stringify(["segments", r.company, r.axis, r.metric, r.view, r.basis]), () =>
    segmentsOf(token, r.company, { axis: r.axis, metric: r.metric, quarters: SEGMENT_QUARTERS, view: r.view, basis: r.basis }));
  const totals = r.transform === "contrib"
    ? once(token, JSON.stringify(["totals", r.company, r.metric, r.basis]), () =>
        financialsView(token, r.company, SEGMENT_QUARTERS, [r.metric], "ttm", r.basis))
    : Promise.resolve({ as_of: "", rows: [] as FinancialRow[] });
  const [seg, tot] = await Promise.all([rows, totals]);
  return segmentSeries(req, seg.rows, tot.rows, seg.as_of);
}
