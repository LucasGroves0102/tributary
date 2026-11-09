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
 * total's growth only when the members cover the total.
 *
 * Members can overlap, and summing them then counts revenue twice. On one axis a filer can report a
 * parent beside its children (NVIDIA's Data Center, Hyperscale and AI Clouds, where the last two make
 * the first), two cuts of the same revenue (Vertiv's Products + Services and Product excluding Spares
 * + Services and Spares, each exactly revenue), or a member that is the whole company. At the latest
 * quarter 1,078 of 5,956 company-axis pairs add up to more than 102% of revenue: 797 of them one of
 * those, and 281 that nothing separates (sales between segments, a basis that changed).
 * `separateMembers` finds which to leave out:
 *   - A period is looked at only when its members exceed revenue by more than OVERLAP_TOLERANCE.
 *   - There, the members that make revenue are its partition: the tightest match first (a real one
 *     differs by rounding: NVIDIA's three-member chance match at 99.9% loses to the five that make
 *     100.00%), then the fewest members, then the one holding the largest. A member that alone is
 *     the whole total isn't a partition.
 *   - A member left out of most of the periods it was looked at in is dropped everywhere, so one
 *     chance match can't drop a real member (NVIDIA's Taiwan, in one period of its geography).
 *   - A period whose kept members still don't make revenue is unreliable: it says so, instead of
 *     printing a share or a bar it can't defend.
 * `contrib` applies that to every chart, saved ones too: dropped members draw nothing, and a period
 * where the kept members' change still exceeds the company's by more than OVERLAP_TOLERANCE of its
 * revenue draws no bars at all, so a stack can't overshoot the total (`contributionPlan`). */

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

/** Members may add up to this much over revenue before they are taken to overlap. */
export const OVERLAP_TOLERANCE = 0.02;
const PARTITION_TIERS = [0.0002, 0.001, 0.005];
const PARTITION_BUCKETS = 10_000;
/** More members than this and a subset matching revenue says nothing: some always will. */
const MAX_SEPARABLE = 24;

export interface Separation {
  /** members that overlap the rest: leave them out of any sum */
  drop: Set<string>;
  /** periods where what's kept still doesn't make revenue */
  unreliable: Set<number>;
}

/** Members in a pick, a bitmask over members sorted largest first (at most MAX_SEPARABLE bits). */
const bits = (x: number): number => {
  x -= (x >>> 1) & 0x55555555;
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  return (((x + (x >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
};

/** Of two picks, the fewer members, then the one holding the larger member: bit i is the i-th
 * largest, so at the lowest bit where they differ the pick holding it wins. */
const better = (a: number, b: number): boolean => {
  const na = bits(a);
  const nb = bits(b);
  return na !== nb ? na < nb : (a & (a ^ b) & -(a ^ b)) !== 0;
};

/** The members that make `total`, by the rule in the module's docstring; null without any. A
 * subset-sum over buckets of total / 10,000 kept in place (0/1 knapsack, one Int32Array of picks as
 * bitmasks), so the work is bounded by members x buckets: 24 x 10,074 steps at most, whatever the
 * filing (the earlier Map of arrays copied every reachable sum per member: 5.9 s on 40 periods of
 * 20 countries and a member overlapping three of them). Only sums that can be within the widest
 * tier are measured. */
export function partition(members: [string, number][], total: number): Set<string> | null {
  const ordered = members.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const n = ordered.length;
  if (n > MAX_SEPARABLE) return null;
  const widest = PARTITION_TIERS.at(-1)!;
  const scale = PARTITION_BUCKETS / total;
  const limit = Math.ceil(PARTITION_BUCKETS * (1 + widest)) + n;
  const best = new Int32Array(limit + 1).fill(-1); // best[sum]: the pick reaching it, -1 none
  const count = new Uint8Array(limit + 1); // its number of members
  best[0] = 0;
  let reach = 0; // the highest sum reached so far: nothing above it to read
  ordered.forEach(([, v], i) => {
    const size = Math.round(v * scale);
    const bit = 1 << i;
    // downwards, so best[sum] is still the pick without member i when it's read
    for (let sum = Math.min(limit - size, reach); sum >= 0; sum--) {
      const picks = best[sum]!;
      if (picks < 0) continue;
      const to = sum + size;
      const seen = best[to]!;
      const n = count[sum]! + 1;
      // fewer members wins; as many, the one holding the larger member (lowest differing bit)
      if (seen < 0 || n < count[to]! || (n === count[to]! && ((picks | bit) & (seen ^ (picks | bit)) & -(seen ^ (picks | bit))) !== 0)) {
        best[to] = picks | bit;
        count[to] = n;
      }
    }
    reach = Math.min(limit, reach + size);
  });
  // a bucket sum is off the exact one by at most half a bucket per member
  const lowest = Math.max(0, Math.floor(PARTITION_BUCKETS * (1 - widest) - n / 2) - 1);
  const candidates: { picks: number; off: number }[] = [];
  for (let sum = lowest; sum <= limit; sum++) {
    const picks = best[sum]!;
    if (picks < 0 || count[sum]! < 2) continue;
    let value = 0;
    for (let i = 0; i < n; i++) if (picks & (1 << i)) value += ordered[i]![1];
    candidates.push({ picks, off: Math.abs(value - total) / total });
  }
  for (const tier of PARTITION_TIERS) {
    let found: number | null = null;
    for (const c of candidates) if (c.off <= tier && (found === null || better(c.picks, found))) found = c.picks;
    if (found !== null) return new Set(ordered.filter((_, i) => found! & (1 << i)).map(([m]) => m));
  }
  return null;
}

/** separateMembers' answers by their input: the Revenue tab, its decomposition, the Segments tab
 * and saved contribution charts ask about the same periods, and a re-render asks again. */
const SEPARATIONS = new Map<string, Separation>();
const SEPARATIONS_KEPT = 64;

/** Which members overlap, over these periods: see the module's docstring. Period keys can be
 * anything; `totals` holds the consolidated figure per key (no total: nothing to measure against).
 * Remembered by input (pure), so asking twice costs a lookup. */
export function separateMembers(periods: Map<number, Map<string, number>>, totals: Map<number, number>): Separation {
  const key = JSON.stringify([[...periods].map(([k, m]) => [k, [...m]]), [...totals]]);
  let known = SEPARATIONS.get(key);
  if (!known) {
    known = separate(periods, totals);
    if (SEPARATIONS.size >= SEPARATIONS_KEPT) SEPARATIONS.delete(SEPARATIONS.keys().next().value!);
    SEPARATIONS.set(key, known);
  }
  return { drop: new Set(known.drop), unreliable: new Set(known.unreliable) };
}

function separate(periods: Map<number, Map<string, number>>, totals: Map<number, number>): Separation {
  const looked = new Map<string, number>();
  const left = new Map<string, number>();
  const over = new Set<number>();
  for (const [key, members] of periods) {
    const total = totals.get(key);
    if (!total || total <= 0) continue;
    const sum = [...members.values()].reduce((s, v) => s + v, 0);
    if (sum <= total * (1 + OVERLAP_TOLERANCE)) continue;
    over.add(key);
    const entries = [...members.entries()];
    let kept = entries.length <= MAX_SEPARABLE ? partition(entries, total) : null;
    // a member that alone is the whole company, with no pair of others that makes it
    if (!kept) {
      const whole = entries.filter(([, v]) => Math.abs(v - total) <= PARTITION_TIERS.at(-1)! * total);
      if (whole.length) kept = new Set(entries.filter(([m]) => !whole.some(([w]) => w === m)).map(([m]) => m));
    }
    for (const [m] of entries) {
      looked.set(m, (looked.get(m) ?? 0) + 1);
      if (kept && !kept.has(m)) left.set(m, (left.get(m) ?? 0) + 1);
    }
  }
  const drop = new Set([...left].filter(([m, n]) => n * 2 > (looked.get(m) ?? 0)).map(([m]) => m));
  const unreliable = new Set<number>();
  for (const key of over) {
    const total = totals.get(key)!;
    const sum = [...periods.get(key)!].reduce((s, [m, v]) => (drop.has(m) ? s : s + v), 0);
    if (Math.abs(sum / total - 1) > OVERLAP_TOLERANCE) unreliable.add(key);
  }
  return { drop, unreliable };
}

export interface ContributionPlan extends Separation {
  /** periods where the members' change exceeds the company's: they show no contributions */
  blocked: Set<number>;
}

/** What may be drawn as contributions to growth: `periods` and `totals` are TTM, keyed by
 * periodKey, so `key - 4` is the same quarter a year earlier. */
export function contributionPlan(periods: Map<number, Map<string, number>>, totals: Map<number, number>): ContributionPlan {
  const { drop, unreliable } = separateMembers(periods, totals);
  const blocked = new Set<number>();
  for (const [key, now] of periods) {
    const before = periods.get(key - 4);
    const total = totals.get(key);
    const base = totals.get(key - 4);
    if (!before || !total || !base || base <= 0) continue;
    let change = 0;
    for (const [m, v] of now) {
      const old = before.get(m);
      if (old !== undefined && !drop.has(m)) change += v - old;
    }
    const growth = total - base;
    const exceeds = growth >= 0 ? change > growth + OVERLAP_TOLERANCE * base : change < growth - OVERLAP_TOLERANCE * base;
    if (exceeds) blocked.add(key);
  }
  return { drop, unreliable, blocked };
}

const num = (v: string): number | null => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** Every member of the layer's axis and metric, by period key. */
function axisPeriods(rows: SegmentRow[], r: SegmentRequest["segment"], label: (x: SegmentRow) => string | null): Map<number, Map<string, number>> {
  const out = new Map<number, Map<string, number>>();
  for (const row of rows) {
    if (row.axis !== r.axis || row.metric !== r.metric) continue;
    const key = periodKey(label(row));
    const v = num(row.value);
    if (key === null || v === null) continue;
    const m = out.get(key) ?? new Map<string, number>();
    m.set(row.member, v);
    out.set(key, m);
  }
  return out;
}

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

  // contributions are read against the whole axis: whatever this layer's members, a member that
  // overlaps the rest draws nothing and a period that can't be separated draws no bars
  const plan = r.transform === "contrib" ? contributionPlan(axisPeriods(rows, r, label), new Map([...total].map(([k, t]) => [k, t.value]))) : null;
  const unit = rows.find((x) => x.metric === r.metric)?.unit ?? "USD";
  const points: FormulaPoint[] = [];
  for (const key of [...byKey.keys()].sort((a, b) => a - b)) {
    if (plan?.blocked.has(key)) continue;
    const now = plan ? new Map([...byKey.get(key)!].filter(([m]) => !plan.drop.has(m))) : byKey.get(key)!;
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
