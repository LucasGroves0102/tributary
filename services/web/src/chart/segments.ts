/** Segment layers: a chart layer over `/v1/companies/{id}/segments` (gold.segments) instead of
 * `/v1/formulas/evaluate`, so the Revenue view's charts are ordinary chart items (saved,
 * embedded in a thesis, opened in the chart builder) drawn by the same LayerChart. Pure: the
 * request key and the series are tested without fetching.
 *
 * A segment layer names one company, a segment metric (revenue, operating_income), an axis
 * (business segment, geography, product/service) and 1-20 members (QNames as last filed; the
 * API already merges a renamed member's earlier QNames into it, `member_aliases`). Its value in
 * a period is the sum of the listed members that report that period: a member missing a period
 * is a gap, never a zero, and the point's sources list the filing of every member summed.
 *
 * Transforms: `q` the quarter (TTM with annual frequency, as for flows), `ttm` the trailing
 * four quarters as the API derives them, `yoy` over TTM values, summed over the members that
 * report both the period and the one four quarters earlier, `contrib` the contribution to the
 * company's total growth over TTM values: (sum of those members' change from four quarters
 * earlier) / (the company's total for the metric, TTM, four quarters earlier), from
 * `/financials`; percentage points of total growth, so the layers of an axis add up to the
 * total's growth only when the members cover the total. */

import type { FinancialRow, FormulaPoint, FormulaSeries, SegmentRow } from "../types";
import { periodKey } from "./series";

export type SegmentAxisKind = "business_segment" | "geography" | "product_service";
export const SEGMENT_AXES: SegmentAxisKind[] = ["business_segment", "geography", "product_service"];
export const SEGMENT_AXIS_LABELS: Record<string, string> = {
  business_segment: "business segment",
  geography: "geography",
  product_service: "product or service",
};
export const MAX_MEMBERS = 20;
/** periods asked for: the API's maximum (it returns the latest 40 period ends per axis) */
export const SEGMENT_QUARTERS = 40;
export type SegmentTransform = "q" | "ttm" | "yoy" | "contrib";
export const SEGMENT_TRANSFORMS: [SegmentTransform, string][] = [
  ["q", "Quarterly"],
  ["ttm", "TTM"],
  ["yoy", "YoY %"],
  ["contrib", "Contribution to growth"],
];
export const MEMBER = /^[A-Za-z0-9_.:-]{1,200}$/;

/** What a segment layer asks for: the key the chart caches its answer under. */
export interface SegmentRequest {
  segment: {
    company: string;
    axis: SegmentAxisKind;
    metric: string;
    members: string[];
    view: "quarter" | "ttm";
    basis: "fiscal" | "calendar";
    transform: SegmentTransform;
    from?: string;
    to?: string;
  };
}

/** "nvda:ComputeAndNetworkingSegmentMember" → "Compute And Networking"; "country:US" → "US".
 * Only for a legend when the layer has no name (the Revenue view always names its layers). */
export function memberName(member: string): string {
  const local = member.slice(member.indexOf(":") + 1).replace(/(Segment)?Member$/, "");
  return local.replace(/([a-z])([A-Z])/g, "$1 $2").trim() || member;
}

interface Total {
  value: number;
}

const num = (v: string): number | null => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** The layer's points from the segment rows (all members of its axis and metric) and, for
 * `contrib`, the company's total rows (the same metric, TTM). */
export function segmentSeries(req: SegmentRequest, rows: SegmentRow[], totals: FinancialRow[] = [], asOf = ""): FormulaSeries {
  const r = req.segment;
  const label = (x: { fiscal_period?: string | null; calendar_period?: string | null }) =>
    (r.basis === "calendar" ? x.calendar_period : x.fiscal_period) ?? null;
  const wanted = new Set(r.members);
  // key → member → row
  const byKey = new Map<number, Map<string, SegmentRow>>();
  const labels = new Map<number, { period: string; end: string }>();
  for (const row of rows) {
    if (row.axis !== r.axis || row.metric !== r.metric || !wanted.has(row.member)) continue;
    const period = label(row);
    const key = periodKey(period);
    if (key === null || num(row.value) === null) continue;
    const m = byKey.get(key) ?? new Map<string, SegmentRow>();
    const seen = m.get(row.member);
    if (!seen || row.period_end > seen.period_end) m.set(row.member, row); // one row per period
    byKey.set(key, m);
    if (!labels.has(key) || row.period_end > labels.get(key)!.end) labels.set(key, { period: period!, end: row.period_end });
  }
  const total = new Map<number, Total>();
  for (const t of totals) {
    const key = periodKey(label(t));
    const v = num(t.value);
    if (t.metric === r.metric && key !== null && v !== null) total.set(key, { value: v });
  }

  const unit = rows.find((x) => x.metric === r.metric)?.unit ?? "USD";
  const points: FormulaPoint[] = [];
  for (const key of [...byKey.keys()].sort((a, b) => a - b)) {
    const now = byKey.get(key)!;
    const { period, end } = labels.get(key)!;
    if ((r.from && end < r.from) || (r.to && end > r.to)) continue;
    let value: number | null;
    let used: SegmentRow[];
    if (r.transform === "q" || r.transform === "ttm") {
      used = [...now.values()];
      value = used.reduce((s, x) => s + num(x.value)!, 0);
    } else {
      const before = byKey.get(key - 4);
      used = [...now.values()].filter((x) => before?.has(x.member));
      if (!before || !used.length) continue;
      const cur = used.reduce((s, x) => s + num(x.value)!, 0);
      const old = used.reduce((s, x) => s + num(before.get(x.member)!.value)!, 0);
      if (r.transform === "yoy") {
        value = old > 0 ? cur / old - 1 : null;
      } else {
        const base = total.get(key - 4)?.value;
        value = base && base > 0 ? (cur - old) / base : null;
      }
      used = [...used, ...used.map((x) => before.get(x.member)!)];
    }
    if (value === null || !Number.isFinite(value)) continue;
    points.push({
      period,
      period_key: key,
      period_end: end,
      value: used.length === 1 && (r.transform === "q" || r.transform === "ttm") ? used[0]!.value : String(Number(value.toPrecision(15))),
      inputs: used.map((x) => ({
        input: 0,
        period_start: x.period_start,
        period_end: x.period_end,
        value: x.value,
        value_source: x.value_source,
        citation: {
          accession_no: x.citation.accession_no,
          form: x.citation.form,
          filed: x.citation.filed,
          source_accessions: x.citation.source_accessions,
          filing_url: x.citation.filing_url,
        },
        freshness: x.freshness,
      })),
      source_accessions: [...new Set(used.map((x) => x.citation.accession_no))],
    });
  }
  return {
    formula: `segments(${r.axis}: ${r.members.join(" + ")})`,
    unit: r.transform === "yoy" || r.transform === "contrib" ? "ratio" : unit,
    basis: r.basis,
    view: r.view,
    alignment: r.basis,
    as_of: asOf,
    inputs: [{ input: 0, ref: "segments", ticker: null, company_id: r.company, metric: r.metric, span: r.view, lag_quarters: 0, unit }],
    points,
  };
}
