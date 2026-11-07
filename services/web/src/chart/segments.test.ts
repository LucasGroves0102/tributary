import { describe, expect, it } from "vitest";

import { seg } from "../fixtures";
import type { FinancialRow } from "../types";
import { stateOf } from "../workspaces/model";
import { emptyChart, layerKey, restoreChart, segmentRequest, type Layer } from "./model";
import { memberName, segmentSeries, type SegmentRequest } from "./segments";
import { toSeries } from "./series";

const NVDA = "0001045810";
const CN = "nvda:ComputeAndNetworkingSegmentMember";
const GR = "nvda:GraphicsSegmentMember";

const req = (over: Partial<SegmentRequest["segment"]> = {}): SegmentRequest => ({
  segment: { company: NVDA, axis: "business_segment", metric: "revenue", members: [CN], view: "quarter", basis: "fiscal", transform: "q", ...over },
});

// five quarters: FY2026 Q2 .. FY2027 Q2; Graphics misses FY2026 Q4 (a gap, not a zero)
const ROWS = [
  seg(CN, "FY2026 Q2", "2025-07-27", "41331000000"),
  seg(CN, "FY2026 Q3", "2025-10-26", "50908000000"),
  seg(CN, "FY2026 Q4", "2026-01-25", "61651000000", { value_source: "derived" }),
  seg(CN, "FY2027 Q1", "2026-04-26", "74550000000"),
  seg(CN, "FY2027 Q2", "2026-07-26", "88299000000"),
  seg(GR, "FY2026 Q2", "2025-07-27", "5412000000"),
  seg(GR, "FY2026 Q3", "2025-10-26", "6098000000"),
  seg(GR, "FY2027 Q1", "2026-04-26", "7065000000"),
  seg(GR, "FY2027 Q2", "2026-07-26", "7922000000"),
  seg("country:US", "FY2027 Q2", "2026-07-26", "1", { axis: "geography" }), // another axis
];

describe("segmentSeries", () => {
  it("one member: its exact values, each citing its filing", () => {
    const s = segmentSeries(req(), ROWS);
    expect(s.points.map((p) => [p.period, p.value])).toEqual([
      ["FY2026 Q2", "41331000000"], ["FY2026 Q3", "50908000000"], ["FY2026 Q4", "61651000000"],
      ["FY2027 Q1", "74550000000"], ["FY2027 Q2", "88299000000"],
    ]);
    expect(s.points[2]!.inputs[0]!.value_source).toBe("derived");
    expect(s.points[0]!.source_accessions).toEqual(["0001045810-26-000007"]);
    expect(s.inputs[0]!.company_id).toBe(NVDA);
    expect(s.unit).toBe("USD");
  });

  it("several members are summed where they report; a missing member is a gap, not a zero", () => {
    const s = segmentSeries(req({ members: [CN, GR] }), ROWS);
    expect(s.points.map((p) => [p.period, Number(p.value)])).toEqual([
      ["FY2026 Q2", 46743000000], ["FY2026 Q3", 57006000000], ["FY2026 Q4", 61651000000],
      ["FY2027 Q1", 81615000000], ["FY2027 Q2", 96221000000],
    ]);
    expect(s.points[2]!.inputs).toHaveLength(1); // only Compute reported FY2026 Q4
  });

  it("YoY over members reporting both periods", () => {
    const s = segmentSeries(req({ members: [CN, GR], transform: "yoy", view: "ttm" }), ROWS);
    expect(s.points.map((p) => p.period)).toEqual(["FY2027 Q2"]);
    expect(Number(s.points[0]!.value)).toBeCloseTo(96221 / 46743 - 1, 10);
    expect(s.unit).toBe("ratio");
    expect(s.points[0]!.inputs).toHaveLength(4); // both members, both periods
  });

  it("contribution: the members' change over the company's total a year earlier", () => {
    const totals: FinancialRow[] = [
      { metric: "revenue", period_start: null, period_end: "2025-07-27", fiscal_period: "FY2026 Q2", value: "50000000000", unit: "USD", value_source: "reported",
        citation: { accession_no: "x", form: "10-Q", filed: "", xbrl_tag: "", source_accessions: null, filing_url: null }, freshness: { filed: "", ingested_at: "" } },
    ];
    const s = segmentSeries(req({ members: [GR], transform: "contrib", view: "ttm" }), ROWS, totals);
    expect(s.points.map((p) => [p.period, Number(p.value)])).toEqual([["FY2027 Q2", (7922000000 - 5412000000) / 50000000000]]);
    // without a total that period: no point
    expect(segmentSeries(req({ members: [GR], transform: "contrib" }), ROWS, []).points).toEqual([]);
    // as a chart series: points of growth, on the same scale as a percent
    const series = toSeries(s, { id: 1, transform: "contrib", type: "sbar", axis: "L" }, "Graphics", 0);
    expect(series.unit).toBe("pts");
  });

  it("keeps the range and the basis's labels", () => {
    expect(segmentSeries(req({ from: "2026-01-01" }), ROWS).points.map((p) => p.period)).toEqual(["FY2026 Q4", "FY2027 Q1", "FY2027 Q2"]);
    expect(segmentSeries(req({ to: "2025-12-31" }), ROWS).points).toHaveLength(2);
    const cal = ROWS.map((r) => ({ ...r, calendar_period: r.fiscal_period!.replace("FY", "CY") }));
    expect(segmentSeries(req({ basis: "calendar" }), cal).points[0]!.period).toBe("CY2026 Q2");
  });
});

it("memberName", () => {
  expect(memberName(CN)).toBe("Compute And Networking");
  expect(memberName("country:US")).toBe("US");
  expect(memberName("srt:AmericasMember")).toBe("Americas");
});

describe("segment layers in charts", () => {
  const layer: Layer = { id: 1, kind: "segment", companyId: NVDA, metric: "revenue", segmentAxis: "business_segment", members: [CN, GR], transform: "q", type: "sbar", axis: "L", name: "Data center" };

  it("ask /segments: quarter view for quarterly, TTM otherwise; fiscal for one company", () => {
    const chart = { ...emptyChart(), align: "fiscal" as const, layers: [layer] };
    const r = segmentRequest(layer, chart, new Date(Date.UTC(2026, 10, 2)))!;
    expect(r.segment).toEqual({ company: NVDA, axis: "business_segment", metric: "revenue", members: [CN, GR], view: "quarter", basis: "fiscal", transform: "q", from: "2023-08-02" });
    expect(segmentRequest({ ...layer, transform: "contrib" }, chart)!.segment.view).toBe("ttm");
    expect(segmentRequest(layer, { ...chart, frequency: "annual" })!.segment.view).toBe("ttm");
    expect(JSON.parse(layerKey(layer, chart, () => true)!)).toHaveProperty("segment");
    expect(segmentRequest({ ...layer, members: [] }, chart)).toBeNull();
  });

  it("are saved and restored like other layers, and cleaned like a URL", () => {
    const chart = { ...emptyChart(), title: "NVDA segments", layers: [layer], nextId: 2 };
    const saved = stateOf({ kind: "chart", chart });
    expect((saved.layers as object[])[0]).toEqual({ kind: "segment", companyId: NVDA, metric: "revenue", segmentAxis: "business_segment", members: [CN, GR], transform: "q", type: "sbar", axis: "L", name: "Data center" });
    expect(restoreChart(saved).layers[0]).toEqual(layer);
    // untrusted: bad members, axes, transforms
    const bad = (over: object) => restoreChart({ layers: [{ ...layer, ...over }] }).layers;
    expect(bad({ members: ["a b"] })).toEqual([]);
    expect(bad({ members: [] })).toEqual([]);
    expect(bad({ segmentAxis: "reconciliation" })).toEqual([]);
    expect(bad({ transform: "ps" })[0]!.transform).toBe("ttm");
    expect(restoreChart({ layers: [{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "contrib" }] }).layers[0]!.transform).toBe("ttm");
  });
});
