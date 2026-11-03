import { describe, expect, it } from "vitest";

import { fin, seg } from "../fixtures";
import type { SegmentRow } from "../types";
import { axesOf, axisChart, axisTable, decompose, decompositionChart, qualityChart, revenueQuality, totalsByKey } from "./revenue";

const NVDA = "0001045810";
const Q = [
  ["FY2026 Q1", "2025-04-27"], ["FY2026 Q2", "2025-07-27"], ["FY2026 Q3", "2025-10-26"], ["FY2026 Q4", "2026-01-25"],
  ["FY2027 Q1", "2026-04-26"], ["FY2027 Q2", "2026-07-26"],
] as const;
const member = (m: string, values: (number | null)[], over: Partial<SegmentRow> = {}) =>
  values.flatMap((v, i) => (v === null ? [] : [seg(m, Q[i]![0], Q[i]![1], String(v), over)]));
const total = (values: number[]) => totalsByKey(values.map((v, i) => fin("revenue", Q[i]![1], String(v), Q[i]![0])), "revenue");

describe("axisTable", () => {
  it("members by period, gaps as null, notes for members reported in part of the window", () => {
    const rows = [
      ...member("x:Big", [60, 62, 64, 66, 68, 70]),
      ...member("x:New", [null, null, 5, 6, 7, 8]),
      ...member("x:Gone", [40, 38, null, null, null, null]),
      ...member("x:Gappy", [1, null, 1, 1, 1, 1]),
      ...member("x:Recon", [9, 9, 9, 9, 9, 9], { axis: "reconciliation" }),
    ];
    const t = axisTable(rows, "business_segment", total([101, 100, 70, 73, 76, 79]));
    expect(t.periods.map((p) => p.label)).toEqual(Q.map(([f]) => f));
    expect(t.rows.map((r) => [r.label, r.note])).toEqual([
      ["Big", null], ["New", "from FY2026 Q3"], ["Gappy", "some periods missing"], ["Gone", "until FY2026 Q2"],
    ]);
    expect(t.rows[1]!.values).toEqual([null, null, 5, 6, 7, 8]);
    expect(t.rows[0]!.sources[0]).toContain("10-Q filed 2025-04-27");
    // coverage: members' sum / reported revenue; 100 vs 101 in Q1 is within 2%
    expect(t.coverage.map((c) => Math.round(c! * 100))).toEqual([100, 100, 100, 100, 100, 100]);
    expect(t.additive).toBe(true);
    expect(axisChart(t, NVDA, "NVDA", "quarter").layers.map((l) => [l.type, l.transform])).toEqual(Array(4).fill(["sbar", "q"]));
  });

  it("folds members beyond the largest six into Other, summing those that report", () => {
    const rows = [1, 2, 3, 4, 5, 6, 7, 8].flatMap((n) => member(`x:M${n}`, [null, null, null, null, n === 1 ? null : n, n * 10]));
    const t = axisTable(rows, "business_segment", new Map());
    expect(t.rows.map((r) => r.label)).toEqual(["M8", "M7", "M6", "M5", "M4", "M3", "Other (2 members)"]);
    expect(t.rows.at(-1)!.members).toEqual(["x:M2", "x:M1"]);
    expect(t.rows.at(-1)!.values.slice(-2)).toEqual([2, 30]); // M1 has no FY2027 Q1
    // no totals: coverage unknown, so the chart draws lines, not stacks
    expect(t.additive).toBe(false);
    const chart = axisChart(t, NVDA, "NVDA", "ttm");
    expect(chart.layers.map((l) => l.type)).toEqual(Array(7).fill("line"));
    expect(chart.layers.at(-1)!.members).toEqual(["x:M2", "x:M1"]);
    expect(chart.align).toBe("fiscal");
  });

  it("a partial breakdown (a few named countries) isn't stacked", () => {
    const rows = member("country:US", [40, 40, 40, 40, 40, 40], { axis: "geography" });
    const t = axisTable(rows, "geography", total([100, 100, 100, 100, 100, 100]));
    expect(t.coverage[0]).toBeCloseTo(0.4);
    expect(t.additive).toBe(false);
    expect(axesOf(rows)).toEqual(["geography"]);
  });
});

describe("decompose", () => {
  it("contributions in points of TTM growth, and what isn't attributed", () => {
    const rows = [
      ...member("x:A", [60, 60, 60, 60, 72, 75]),
      ...member("x:B", [40, 40, 40, 40, 38, null]), // B misses the latest period
    ];
    const totals = total([100, 100, 100, 100, 110, 115]);
    const t = axisTable(rows, "business_segment", totals);
    const d = decompose(t, rows, totals, 2);
    expect(d.periods.map((p) => p.label)).toEqual(["FY2027 Q1", "FY2027 Q2"]);
    expect(d.rows.map((r) => [r.label, r.points.map((p) => (p === null ? null : Math.round(p * 10) / 10))])).toEqual([
      ["A", [12, 15]], ["B", [-2, null]],
    ]);
    expect(d.total.map((v) => Math.round(v! * 10) / 10)).toEqual([10, 15]);
    expect(d.unattributed.map((v) => Math.abs(Math.round(v! * 10) / 10))).toEqual([0, 0]);
    const chart = decompositionChart(t, NVDA, "NVDA");
    expect(chart.layers.map((l) => [l.kind, l.transform, l.type])).toEqual([
      ["segment", "contrib", "sbar"], ["segment", "contrib", "sbar"], ["metric", "yoy", "line"],
    ]);
  });

  it("no total a year earlier: nothing to divide by", () => {
    const rows = member("x:A", [1, 1, 1, 1, 2, 2]);
    const d = decompose(axisTable(rows, "business_segment", new Map()), rows, new Map());
    expect(d.rows[0]!.points.every((p) => p === null)).toBe(true);
    expect(d.total.every((p) => p === null)).toBe(true);
  });
});

describe("revenueQuality", () => {
  const rows = [
    ...[100, 100, 100, 100, 110, 112].map((v, i) => fin("revenue", Q[i]![1], String(v), Q[i]![0])),
    ...[10, 10, 10, 10, 11, 14].map((v, i) => fin("receivables", Q[i]![1], String(v), Q[i]![0])),
  ];

  it("DSO, both growth rates and the gap; flagged when receivables outgrow revenue by > 10 pts", () => {
    const q = revenueQuality(rows);
    expect(q.map((r) => r.label)).toEqual(Q.map(([f]) => f));
    expect(q[0]!.dso).toBeCloseTo(36.5);
    expect(q[0]!.gap).toBeNull(); // no year-ago quarter
    const [q1, q2] = q.slice(-2);
    expect(q1!.gap).toBeCloseTo(0); // +10% and +10%
    expect(q1!.flagged).toBe(false);
    expect(q2!.receivablesGrowth).toBeCloseTo(0.4);
    expect(q2!.revenueGrowth).toBeCloseTo(0.12);
    expect(q2!.gap).toBeCloseTo(28);
    expect(q2!.flagged).toBe(true);
  });

  it("exactly 10 points isn't flagged; no receivables, no rows", () => {
    const edge = [
      ...[100, 100, 100, 100, 100].map((v, i) => fin("revenue", Q[i]![1], String(v), Q[i]![0])),
      ...[10, 10, 10, 10, 11].map((v, i) => fin("receivables", Q[i]![1], String(v), Q[i]![0])),
    ];
    expect(revenueQuality(edge).at(-1)!.flagged).toBe(false);
    expect(revenueQuality(rows.filter((r) => r.metric === "revenue"))).toEqual([]);
  });

  it("its chart is two formula-API lines: TTM revenue YoY and receivables YoY", () => {
    const c = qualityChart(NVDA, "NVDA");
    expect(c.layers.map((l) => [l.kind, l.metric ?? l.formula, l.transform])).toEqual([
      ["metric", "revenue", "yoy"], ["formula", "receivables / receivables[-4q] - 1", "q"],
    ]);
  });
});
