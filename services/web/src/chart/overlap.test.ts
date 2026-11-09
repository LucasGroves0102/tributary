import { describe, expect, it } from "vitest";

import { fin, seg } from "../fixtures";
import { contributionPlan, partition, segmentSeries, separateMembers, type SegmentRequest } from "./segments";

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

/** The subset search before the review (a Map of arrays copied per member): the reference the
 * bounded one must agree with. */
function referencePartition(members: [string, number][], total: number): Set<string> | null {
  const tiers = [0.0002, 0.001, 0.005];
  const largerFirst = (a: number[], b: number[]) => {
    const at = a.findIndex((x, i) => x !== b[i]);
    return at < 0 ? 0 : a[at]! - b[at]!;
  };
  const ordered = members.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const scale = 10_000 / total;
  const limit = Math.ceil(10_000 * 1.005) + ordered.length;
  let best = new Map<number, number[]>([[0, []]]);
  ordered.forEach(([, v], i) => {
    const size = Math.round(v * scale);
    const next = new Map(best);
    for (const [sum, picks] of best) {
      const to = sum + size;
      if (to > limit) continue;
      const seen = next.get(to);
      const mine = [...picks, i];
      if (!seen || mine.length < seen.length || (mine.length === seen.length && largerFirst(mine, seen) < 0)) next.set(to, mine);
    }
    best = next;
  });
  const candidates = [...best.values()].filter((p) => p.length >= 2)
    .map((picks) => ({ picks, off: Math.abs(picks.reduce((s, i) => s + ordered[i]![1], 0) - total) / total }));
  for (const tier of tiers) {
    const found = candidates.filter((c) => c.off <= tier).sort((a, b) => a.picks.length - b.picks.length || largerFirst(a.picks, b.picks))[0];
    if (found) return new Set(found.picks.map((i) => ordered[i]![0]));
  }
  return null;
}

/** 20 countries that make revenue, and one member that is three of them (the review's case). */
function countries(n: number, seed = 7): { periods: Map<number, Map<string, number>>; totals: Map<number, number> } {
  let x = seed;
  const rand = () => ((x = (x * 1103515245 + 12345) % 2147483648) / 2147483648);
  const periods_ = new Map<number, Map<string, number>>();
  const totals_ = new Map<number, number>();
  for (let p = 1; p <= n; p++) {
    const m = new Map<string, number>();
    for (let c = 0; c < 20; c++) m.set(`country:C${c}`, Math.round(200 + rand() * 5000));
    const sum = [...m.values()].reduce((s, v) => s + v, 0);
    m.set("geo:Americas", m.get("country:C3")! + m.get("country:C7")! + m.get("country:C11")!);
    periods_.set(p, m);
    totals_.set(p, sum);
  }
  return { periods: periods_, totals: totals_ };
}

describe("the subset search's cost (review: the Revenue tab froze for 5.9 s)", () => {
  it("gives the same partition as the unbounded search", () => {
    let x = 11;
    const rand = () => ((x = (x * 1103515245 + 12345) % 2147483648) / 2147483648);
    for (let trial = 0; trial < 300; trial++) {
      const n = 2 + Math.floor(rand() * 9);
      const members: [string, number][] = Array.from({ length: n }, (_, i) => [`m${i}`, Math.round(1 + rand() * 1000) / (rand() < 0.3 ? 10 : 1)]);
      const parts = members.filter(() => rand() < 0.6).reduce((s, [, v]) => s + v, 0);
      const total = rand() < 0.7 && parts > 0 ? parts * (1 + (rand() - 0.5) * 0.004) : 100 + rand() * 4000;
      expect([...partition(members, total) ?? []].sort()).toEqual([...referencePartition(members, total) ?? []].sort());
    }
    for (const [key, members] of PRODUCTS) {
      expect(partition([...members], REVENUE.get(key)!)).toEqual(referencePartition([...members], REVENUE.get(key)!));
    }
    const vertiv: [string, number][] = [["Products", 3637.1], ["ProductExSpares", 3593.8], ["ServicesAndSpares", 719.0], ["Services", 675.7]];
    expect(partition(vertiv, 4312.8)).toEqual(referencePartition(vertiv, 4312.8));
  });

  it("40 periods of 20 countries and an overlapping region: under 50 ms, the same answer as before", () => {
    // the fastest of three different inputs (each new to the cache), so a busy test machine
    // doesn't fail it; ~25 ms each on a laptop, 5.9 s before
    const took = [7, 8, 9].map((seed) => {
      const { periods: p, totals: t } = countries(40, seed);
      const start = performance.now();
      separateMembers(p, t);
      return performance.now() - start;
    });
    expect(Math.min(...took)).toBeLessThan(50);
    const { periods: p, totals: t } = countries(40);
    // asked again (a re-render, the decomposition, the Segments tab): remembered
    const again = performance.now();
    separateMembers(p, t);
    expect(performance.now() - again).toBeLessThan(5);
    // the unbounded search took ~150 ms a period here; two of them are enough to compare
    for (const key of [1, 40]) {
      const members = [...p.get(key)!];
      expect(partition(members, t.get(key)!)).toEqual(referencePartition(members, t.get(key)!));
    }
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
