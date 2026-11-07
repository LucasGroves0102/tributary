import { describe, expect, it } from "vitest";

import { fin, seg } from "../fixtures";
import { contributionPlan, segmentSeries, separateMembers, type SegmentRequest } from "./segments";

const periods = (rows: Record<string, Record<string, number>>) =>
  new Map(Object.entries(rows).map(([k, m]) => [Number(k), new Map(Object.entries(m))]));
const totals = (t: Record<string, number>) => new Map(Object.entries(t).map(([k, v]) => [Number(k), v]));

// NVIDIA's TTM product lines, $M, as filed
const PRODUCTS = periods({
  1: { DC: 98011, Compute: 84713, Net: 13298, Gaming: 11669, Viz: 1826, Auto: 1412, OEM: 353 },
  2: { DC: 115186, Compute: 102196, Net: 12990, Gaming: 11350, Viz: 1878, Auto: 1694, OEM: 389 },
  3: { DC: 131735, Compute: 116959, Net: 14776, Gaming: 12466, Viz: 1960, Auto: 1932, OEM: 422 },
  4: { DC: 146559, Compute: 128199, Net: 18360, Gaming: 13873, Viz: 2107, Auto: 2172, OEM: 507 },
  5: { DC: 167003, Compute: 143583, Net: 23420, Gaming: 14859, Viz: 2381, Auto: 2315, OEM: 584 },
  6: { DC: 193737, Compute: 162361, Net: 31376, Gaming: 16042, Viz: 3191, Auto: 2349, OEM: 619 },
  7: { DC: 229871 },
  8: { DC: 277798 },
});
const REVENUE = totals({ 1: 113269, 2: 130497, 3: 148515, 4: 165218, 5: 187142, 6: 215938, 7: 253491, 8: 302969 });

describe("separateMembers", () => {
  it("leaves out the children beside a parent, and isn't fooled by a closer-to-fewer chance match", () => {
    // Data Center + Networking + Viz is 99.89% of revenue in period 1: three members, but not a partition
    const s = separateMembers(PRODUCTS, REVENUE);
    expect([...s.drop].sort()).toEqual(["Compute", "Net"]);
    expect(s.unreliable.size).toBe(0);
  });

  it("two cuts of one revenue: keeps the one holding the largest member", () => {
    const s = separateMembers(
      periods({ 1: { Products: 3637.1, ProductExSpares: 3593.8, ServicesAndSpares: 719.0, Services: 675.7 } }),
      totals({ 1: 4312.8 }),
    );
    expect([...s.drop].sort()).toEqual(["ProductExSpares", "ServicesAndSpares"]);
  });

  it("one chance match can't drop a real member", () => {
    // NVIDIA's geography around its change of basis: only the last period has any subset near revenue
    const s = separateMembers(periods({
      1: { US: 62941, Taiwan: 22245, Singapore: 19681, China: 18270 },
      2: { US: 69747, Taiwan: 22537, Singapore: 23684, China: 22118 },
      3: { US: 81936, Taiwan: 25812, Singapore: 28664, China: 29286 },
      4: { US: 101811, Taiwan: 28974, Singapore: 33198, China: 29604 },
    }), totals({ 1: 113269, 2: 130497, 3: 148515, 4: 165218 }));
    expect(s.drop.has("Taiwan")).toBe(false);
    expect([...s.unreliable].sort()).toEqual([1, 2, 3, 4]);
  });

  it("members that add up, or fall short, are left alone", () => {
    const s = separateMembers(periods({ 1: { A: 60, B: 40 }, 2: { A: 60 } }), totals({ 1: 100, 2: 100 }));
    expect(s.drop.size).toBe(0);
    expect(s.unreliable.size).toBe(0);
  });

  it("a member that is the whole company is dropped; what's left must still make it", () => {
    const s = separateMembers(periods({ 1: { Whole: 100, A: 60, B: 40 } }), totals({ 1: 100 }));
    expect([...s.drop]).toEqual(["Whole"]);
    const t = separateMembers(periods({ 1: { Whole: 100, A: 30 } }), totals({ 1: 100 }));
    expect([...t.drop]).toEqual(["Whole"]);
    expect([...t.unreliable]).toEqual([1]);
  });

  it("sales between segments can't be separated, and says so", () => {
    const s = separateMembers(periods({ 1: { A: 70, B: 40 } }), totals({ 1: 100 }));
    expect(s.drop.size).toBe(0);
    expect([...s.unreliable]).toEqual([1]);
  });
});

describe("contributionPlan", () => {
  it("blocks a period whose members change by more than revenue did", () => {
    const plan = contributionPlan(periods({
      1: { US: 62941, Taiwan: 22245, China: 18270 },
      5: { US: 121368, Taiwan: 36537, China: 24435 },
    }), totals({ 1: 113269, 5: 187142 }));
    // +69.6 pts of members against +65.2 of revenue
    expect([...plan.blocked]).toEqual([5]);
  });

  it("lets through a period that adds up, and one that merely falls short", () => {
    const plan = contributionPlan(periods({
      1: { A: 60, B: 40 },
      5: { A: 90, B: 30 },
      2: { A: 60, B: 40 },
      6: { A: 70 },
    }), totals({ 1: 100, 5: 120, 2: 100, 6: 130 }));
    expect(plan.blocked.size).toBe(0);
  });
});

describe("a contribution layer draws what can be defended", () => {
  const Q = [["FY2025 Q1", "2024-04-28"], ["FY2026 Q1", "2025-04-27"]] as const;
  const rows = (axis: "product_service") => Object.entries({ DC: [100, 200], Compute: [80, 150], Net: [20, 50], Gaming: [10, 20] })
    .flatMap(([m, values]) => values.map((v, i) => seg(`x:${m}`, Q[i]![0], Q[i]![1], String(v), { axis })));
  const fins = [fin("revenue", Q[0]![1], "110", Q[0]![0]), fin("revenue", Q[1]![1], "220", Q[1]![0])];
  const req = (members: string[]): SegmentRequest => ({
    segment: { company: "1", axis: "product_service", metric: "revenue", members, view: "ttm", basis: "fiscal", transform: "contrib" },
  });

  it("a member that overlaps the rest draws nothing, in a saved chart too; the others add up to the total", () => {
    const series = (m: string) => segmentSeries(req([m]), rows("product_service"), fins);
    expect(series("x:Compute").points).toEqual([]);
    expect(Number(series("x:DC").points[0]!.value)).toBeCloseTo(100 / 110);
    expect(Number(series("x:Gaming").points[0]!.value)).toBeCloseTo(10 / 110);
  });

  it("a period that overshoots draws no bars at all", () => {
    const over = [["x:A", [60, 150]], ["x:B", [50, 90]]] as const;
    const data = over.flatMap(([m, values]) => values.map((v, i) => seg(m, Q[i]![0], Q[i]![1], String(v), { axis: "product_service" })));
    // revenue 110 to 180: +63.6 pts, the members +118 pts
    const totals_ = [fin("revenue", Q[0]![1], "110", Q[0]![0]), fin("revenue", Q[1]![1], "180", Q[1]![0])];
    expect(segmentSeries(req(["x:A"]), data, totals_).points).toEqual([]);
    // the same request but for a plain view is as reported
    const plain = { segment: { ...req(["x:A"]).segment, transform: "ttm" as const } };
    expect(segmentSeries(plain, data, totals_).points).toHaveLength(2);
  });
});
