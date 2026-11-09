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

describe("members that overlap", () => {
  const PRODUCTS = {
    "x:DataCenter": [100, 110, 120, 130, 150, 180],
    "x:Compute": [80, 88, 96, 104, 120, 144],
    "x:Networking": [20, 22, 24, 26, 30, 36],
    "x:Gaming": [10, 11, 12, 13, 15, 20],
  };
  const rows = Object.entries(PRODUCTS).flatMap(([m, v]) => member(m, v, { axis: "product_service" }));
  const revenue = total([110, 121, 132, 143, 165, 200]);

  it("stay in the table, marked, out of the sum and off the stack", () => {
    const t = axisTable(rows, "product_service", revenue);
    expect(t.rows.map((r) => [r.label, r.overlaps ?? false])).toEqual([
      ["DataCenter", false], ["Gaming", false], ["Compute", true], ["Networking", true],
    ]);
    expect(t.rows.find((r) => r.label === "Compute")!.note).toBe("overlaps the others");
    expect(t.coverage.map((c) => Math.round(c! * 100))).toEqual([100, 100, 100, 100, 100, 100]);
    expect(t.additive).toBe(true);
    const layers = axisChart(t, NVDA, "NVDA", "ttm").layers;
    expect(layers.map((l) => l.name)).toEqual(["DataCenter", "Gaming"]);
    expect(layers.every((l) => l.type === "sbar")).toBe(true);
  });

  it("a decomposition can't overshoot: the rest add up to revenue growth", () => {
    const t = axisTable(rows, "product_service", revenue);
    const d = decompose(t, rows, revenue, 2);
    expect(d.rows.map((r) => r.label)).toEqual(["DataCenter", "Gaming"]);
    // FY2027 Q2 against FY2026 Q2: +70 of revenue 121, all of it in the two lines
    expect(d.rows[0]!.points[1]).toBeCloseTo((70 / 121) * 100);
    expect(d.unattributed[1]).toBeCloseTo(0);
    expect(d.blocked).toEqual([false, false]);
  });

  it("members that change by more than revenue did show no contributions, and say so", () => {
    const shifted = [
      ...member("country:US", [40, 40, 40, 40, 90, 95], { axis: "geography" }),
      ...member("country:TW", [60, 60, 60, 60, 60, 65], { axis: "geography" }),
    ];
    const totals = total([100, 100, 100, 100, 110, 115]);
    const t = axisTable(shifted, "geography", totals);
    const d = decompose(t, shifted, totals, 2);
    expect(d.blocked).toEqual([true, true]);
    expect(d.rows[0]!.points).toEqual([null, null]);
    expect(d.unattributed).toEqual([null, null]);
    expect(d.total[1]).toBeCloseTo(15);
  });
});

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
    expect(q[0]!.dso).toBeCloseTo(9.125); // 10 / 100 × a quarter's 91.25 days (no dates on the row)
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

  it("its chart is the rule's two sides: the quarter's revenue YoY and receivables YoY, both quarter view", () => {
    const c = qualityChart(NVDA, "NVDA");
    expect(c.layers.map((l) => [l.kind, l.metric ?? l.formula, l.transform])).toEqual([
      ["formula", "revenue / revenue[-4q] - 1", "q"], ["formula", "receivables / receivables[-4q] - 1", "q"],
    ]);
  });

  // production quarters, 2026-11-08 (/financials view=quarter), in millions
  const quarters = (rev: number[], ar: number[], periods: [string, string, string][]) => [
    ...rev.map((v, i) => fin("revenue", periods[i]![2], String(v * 1e6), periods[i]![0], { period_start: periods[i]![1] })),
    ...ar.map((v, i) => fin("receivables", periods[i]![2], String(v * 1e6), periods[i]![0])),
  ];

  it("H3: Vertiv's receivables +55.8% against the quarter's revenue +61.2% isn't a flag (TTM's +35.3% made one)", () => {
    const VRT: [string, string, string][] = [["FY2025 Q3", "2025-07-01", "2025-09-30"], ["FY2025 Q4", "2025-10-01", "2025-12-31"],
      ["FY2026 Q1", "2026-01-01", "2026-03-31"], ["FY2026 Q2", "2026-04-01", "2026-06-30"], ["FY2026 Q3", "2026-07-01", "2026-09-30"]];
    const latest = revenueQuality(quarters([2675.8, 2880.0, 2649.5, 3274.3, 4312.8], [2810.7, 3109.0, 3148.7, 3750.3, 4379.7], VRT)).at(-1)!;
    expect(latest.receivablesGrowth).toBeCloseTo(0.558, 3);
    expect(latest.revenueGrowth).toBeCloseTo(0.612, 3);
    expect(latest.flagged).toBe(false);
    expect(latest.dso).toBeCloseTo((4379.7 / 4312.8) * 92, 3); // July to September: 92 days
  });

  it("H3: Deckers' September quarter still flags: receivables +25.3% against the quarter's revenue +3.5%", () => {
    const DECK: [string, string, string][] = [["FY2026 Q2", "2025-07-01", "2025-09-30"], ["FY2026 Q3", "2025-10-01", "2025-12-31"],
      ["FY2026 Q4", "2026-01-01", "2026-03-31"], ["FY2027 Q1", "2026-04-01", "2026-06-30"], ["FY2027 Q2", "2026-07-01", "2026-09-30"]];
    const q = revenueQuality(quarters([1430.84, 1957.549, 1119.369, 1019.531, 1480.554], [538.764, 344.325, 318.978, 378.348, 675.153], DECK));
    const latest = q.at(-1)!;
    expect(latest.receivablesGrowth).toBeCloseTo(0.253, 3);
    expect(latest.revenueGrowth).toBeCloseTo(0.035, 3);
    expect(latest.flagged).toBe(true);
  });
});
