/** The company Revenue view: revenue by segment and by geography over time, growth
 * decomposition and revenue quality. Pure, tested without rendering; Revenue.tsx draws it.
 * Every chart here is an ordinary chart state (segment layers, chart/segments.ts; metric and
 * formula layers), so it can be saved to a workspace, embedded in a thesis or opened in the
 * chart builder.
 *
 * Members (gold.segments, `/v1/companies/{id}/segments`):
 *   - The API merges a member renamed between filings (`member_aliases`); members still differ
 *     period to period when a company reorganises its segments or discloses a country only in
 *     some filings. A member missing a period is shown as a gap ("—"), never as zero, and a
 *     member reported only in part of the window says so ("from FY2024 Q1", "until FY2023 Q4").
 *   - At most SHOWN members are drawn by name (the largest by their latest value); the rest are
 *     folded into one "Other (n members)" row and layer: the sum of those that report each
 *     period. Reconciliation members (corporate, eliminations) are left out, as on Segments.
 *   - Overlap (chart/segments.ts `separateMembers`): a member that is part of another, or another
 *     cut of the same revenue, stays in the table, marked, but is left out of every sum. Beyond
 *     SHOWN, only the members that don't overlap are folded; the overlapping ones past SHOWN are
 *     counted, not listed.
 *   - Coverage: per period, the sum of the members that don't overlap over the company's reported
 *     revenue (the same view, from `/financials`). They can still cover only part of revenue (a few
 *     named countries); bars stack only when every period's coverage is within COVERAGE_TOLERANCE
 *     of 100%, and only those members, else each member is a line, and the table says how far off.
 *
 * Growth decomposition (over TTM values, year over year): a member's contribution to revenue
 * growth in percentage points = (member TTM − member TTM four quarters earlier) / (company TTM
 * revenue four quarters earlier), over members that report both periods ("Other" folded the
 * same way). "Not attributed" = total growth − the sum of contributions: members missing a
 * period, a breakdown that doesn't add up to revenue, and rounding. Members that overlap aren't in
 * it. A period where the rest still change by more than revenue did has no contributions at all
 * (`blocked`), in the table and the chart: they overlap or changed basis, and it says so.
 *
 * Revenue quality, like with like (H3): receivables at a quarter end are what that quarter's sales
 * left unpaid, so they're read against that quarter's revenue, never a trailing twelve months (a
 * company growing fast through the year has TTM growth well below its latest quarter's: Vertiv's
 * receivables +55.8% against TTM revenue +35.3% looked alarming, against the quarter's +61.2% they
 * aren't). DSO = receivables at the quarter end / the quarter's revenue × the quarter's days.
 * Flagged when receivables grew more than QUALITY_FLAG_POINTS (10) percentage points faster than
 * the same quarter's revenue a year earlier (receivables YoY − quarter revenue YoY > 10 pts), which
 * is DSO rising by more than about a tenth: sales booked faster than cash comes in, or customers
 * paying later. Same quarters a year apart, so seasons cancel (Deckers' December quarter). A flag
 * is a prompt to read the filing (MD&A, the receivables note), not a verdict. */

import type { ChartState, Layer } from "../chart/model";
import { contributionPlan, SEGMENT_AXES, separateMembers, type SegmentAxisKind } from "../chart/segments";
import { periodKey } from "../chart/series";
import type { FinancialRow, SegmentRow } from "../types";
import { OVERLAP_TOLERANCE } from "../chart/segments";

export const SHOWN = 6;
export const PERIODS = 8;
export const COVERAGE_TOLERANCE = OVERLAP_TOLERANCE;
export const QUALITY_FLAG_POINTS = 10;
export const AXIS_TITLES: Record<SegmentAxisKind, string> = {
  business_segment: "Revenue by business segment",
  geography: "Revenue by geography",
  product_service: "Revenue by product or service",
};

export type View = "quarter" | "ttm";

export interface MemberRow {
  /** members summed in this row: one, or the folded ones */
  members: string[];
  label: string;
  /** "from FY2024 Q1" / "until FY2023 Q4" / "some periods missing" */
  note: string | null;
  values: (number | null)[];
  /** a cell's hover: the filing behind it */
  sources: (string | null)[];
  /** part of another member, or another cut of the same revenue: in no sum */
  overlaps?: boolean;
}

export interface AxisTable {
  axis: SegmentAxisKind;
  periods: { key: number; label: string; end: string }[];
  rows: MemberRow[];
  /** members' sum / reported revenue per period (null: no total that period) */
  coverage: (number | null)[];
  /** every period covers revenue within the tolerance: the chart stacks */
  additive: boolean;
  /** periods whose members can't be separated: they add up to more than revenue whatever's left out */
  unreliable: (boolean)[];
  /** overlapping members past SHOWN that aren't listed */
  moreOverlapping: number;
}

const num = (v: string | undefined | null): number | null => {
  if (v === undefined || v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** Company totals by fiscal period key, for one metric. */
export function totalsByKey(rows: FinancialRow[], metric: string): Map<number, number> {
  const out = new Map<number, number>();
  for (const r of rows) {
    const k = periodKey(r.fiscal_period);
    const v = num(r.value);
    if (r.metric === metric && k !== null && v !== null) out.set(k, v);
  }
  return out;
}

interface Cell {
  value: number;
  row: SegmentRow;
}

/** member → key → cell, for one axis of revenue rows */
function grid(rows: SegmentRow[], axis: SegmentAxisKind): { cells: Map<string, Map<number, Cell>>; labels: Map<string, string>; ends: Map<number, { label: string; end: string }> } {
  const cells = new Map<string, Map<number, Cell>>();
  const labels = new Map<string, string>();
  const ends = new Map<number, { label: string; end: string }>();
  for (const r of rows) {
    if (r.axis !== axis || r.metric !== "revenue") continue;
    const k = periodKey(r.fiscal_period);
    const v = num(r.value);
    if (k === null || v === null) continue;
    const m = cells.get(r.member) ?? new Map<number, Cell>();
    const seen = m.get(k);
    if (!seen || r.period_end > seen.row.period_end) m.set(k, { value: v, row: r });
    cells.set(r.member, m);
    labels.set(r.member, r.member_label || r.member);
    const e = ends.get(k);
    if (!e || r.period_end > e.end) ends.set(k, { label: r.fiscal_period!, end: r.period_end });
  }
  return { cells, labels, ends };
}

const source = (c: Cell | undefined) =>
  c ? `${c.row.citation.form} filed ${c.row.citation.filed}, ${c.row.citation.accession_no} (${c.row.value_source})` : null;

/** Which axes have revenue members (business segment, geography, product/service order). */
export function axesOf(rows: SegmentRow[]): SegmentAxisKind[] {
  return SEGMENT_AXES.filter((a) => rows.some((r) => r.axis === a && r.metric === "revenue"));
}

/** One axis as a table: the latest PERIODS periods, the SHOWN largest members by name and the
 * rest folded, coverage of reported revenue per period. */
export function axisTable(rows: SegmentRow[], axis: SegmentAxisKind, totals: Map<number, number>, periods = PERIODS): AxisTable {
  const { cells, labels, ends } = grid(rows, axis);
  const keys = [...ends.keys()].sort((a, b) => a - b).slice(-periods);
  const inWindow = [...cells.entries()].filter(([, m]) => keys.some((k) => m.has(k)));
  const byPeriod = new Map(keys.map((k) => [k, new Map(inWindow.flatMap(([member, m]): [string, number][] => {
    const c = m.get(k);
    return c ? [[member, c.value]] : [];
  }))]));
  const { drop, unreliable } = separateMembers(byPeriod, totals);
  // largest first by the latest period's value; members that stopped reporting go after the
  // ones still reporting, by their own latest value
  const now = keys.at(-1);
  const latest = (m: Map<number, Cell>) => {
    for (let i = keys.length - 1; i >= 0; i--) {
      const c = m.get(keys[i]!);
      if (c) return c.value;
    }
    return -Infinity;
  };
  const current = (m: Map<number, Cell>) => (now !== undefined ? m.get(now)?.value ?? -Infinity : -Infinity);
  inWindow.sort((a, b) => current(b[1]) - current(a[1]) || latest(b[1]) - latest(a[1]) || a[0].localeCompare(b[0]));
  const separate = inWindow.filter(([m]) => !drop.has(m));
  const named = separate.length > SHOWN + 1 ? separate.slice(0, SHOWN) : separate;
  const folded = separate.length > SHOWN + 1 ? separate.slice(SHOWN) : [];
  const overlapping = inWindow.filter(([m]) => drop.has(m));

  const note = (has: boolean[]): string | null => {
    const first = has.indexOf(true);
    const last = has.lastIndexOf(true);
    if (first > 0 && last === has.length - 1) return `from ${ends.get(keys[first]!)!.label}`;
    if (first === 0 && last < has.length - 1) return `until ${ends.get(keys[last]!)!.label}`;
    if (has.some((h) => !h)) return "some periods missing";
    return null;
  };
  const out: MemberRow[] = named.map(([member, m]) => {
    const has = keys.map((k) => m.has(k));
    return {
      members: [member],
      label: labels.get(member) ?? member,
      note: note(has),
      values: keys.map((k) => m.get(k)?.value ?? null),
      sources: keys.map((k) => source(m.get(k))),
    };
  });
  if (folded.length) {
    const values = keys.map((k) => {
      const present = folded.map(([, m]) => m.get(k)).filter((c): c is Cell => !!c);
      return present.length ? present.reduce((s, c) => s + c.value, 0) : null;
    });
    out.push({
      members: folded.map(([m]) => m),
      label: `Other (${folded.length} members)`,
      note: "the sum of the members that report each period",
      values,
      sources: keys.map(() => null),
    });
  }
  const coverage = keys.map((k, i) => {
    const total = totals.get(k);
    const sum = out.reduce((s, r) => s + (r.values[i] ?? 0), 0);
    return total && total > 0 && out.some((r) => r.values[i] !== null) ? sum / total : null;
  });
  const additive = coverage.length > 0 && coverage.every((c) => c !== null && Math.abs(c - 1) <= COVERAGE_TOLERANCE);
  // listed after what they overlap, in no sum and on no stack
  const listed = overlapping.slice(0, SHOWN).map(([member, m]): MemberRow => ({
    members: [member],
    label: labels.get(member) ?? member,
    note: "overlaps the others",
    values: keys.map((k) => m.get(k)?.value ?? null),
    sources: keys.map((k) => source(m.get(k))),
    overlaps: true,
  }));
  return {
    axis,
    periods: keys.map((k) => ({ key: k, label: ends.get(k)!.label, end: ends.get(k)!.end })),
    rows: [...out, ...listed],
    coverage,
    additive,
    unreliable: keys.map((k) => unreliable.has(k)),
    moreOverlapping: Math.max(0, overlapping.length - SHOWN),
  };
}

function segmentLayers(t: AxisTable, companyId: string, transform: Layer["transform"], type: Layer["type"], startId: number): Layer[] {
  // members that overlap have no place on a stack or among contributions; as lines they're as reported
  const drawn = type === "line" ? t.rows : t.rows.filter((r) => !r.overlaps);
  return drawn.slice(0, 11).map((r, i) => ({
    id: startId + i,
    kind: "segment",
    companyId,
    metric: "revenue",
    segmentAxis: t.axis,
    members: r.members.slice(0, 20),
    transform,
    type,
    axis: "L",
    name: r.label.slice(0, 80),
    color: i % 8,
  }));
}

/** The axis over time as a chart: stacked bars when the members add up to revenue, else lines. */
export function axisChart(t: AxisTable, companyId: string, ticker: string, view: View): ChartState {
  const layers = segmentLayers(t, companyId, view === "quarter" ? "q" : "ttm", t.additive ? "sbar" : "line", 1);
  return {
    title: `${ticker}: ${AXIS_TITLES[t.axis].toLowerCase()}${view === "ttm" ? " (TTM)" : ""}`,
    layers,
    range: 3,
    align: "fiscal",
    nextId: layers.length + 1,
  };
}

export interface Decomposition {
  periods: { key: number; label: string }[];
  /** per member row: contribution in percentage points per period (null: not both periods) */
  rows: { label: string; members: string[]; points: (number | null)[] }[];
  /** company revenue growth, TTM over TTM four quarters earlier, in points */
  total: (number | null)[];
  /** total − the sum of contributions */
  unattributed: (number | null)[];
  /** per period: its members change by more than revenue did, so it has no contributions */
  blocked: boolean[];
}

/** Contributions to TTM revenue growth, year over year, for the table's latest `n` periods.
 * `t` must be built from TTM rows and `totals` from TTM revenue. */
export function decompose(t: AxisTable, rows: SegmentRow[], totals: Map<number, number>, n = 4): Decomposition {
  const { cells } = grid(rows, t.axis);
  const periods = t.periods.slice(-n);
  const byPeriod = new Map<number, Map<string, number>>();
  for (const [member, m] of cells) {
    for (const [key, c] of m) {
      const at = byPeriod.get(key) ?? new Map<string, number>();
      at.set(member, c.value);
      byPeriod.set(key, at);
    }
  }
  const plan = contributionPlan(byPeriod, totals);
  const out = t.rows.filter((r) => !r.overlaps).map((r) => ({
    label: r.label,
    members: r.members,
    points: periods.map(({ key }) => {
      if (plan.blocked.has(key)) return null;
      const both = r.members.filter((m) => !plan.drop.has(m) && cells.get(m)?.has(key) && cells.get(m)?.has(key - 4));
      const base = totals.get(key - 4);
      if (!both.length || !base || base <= 0) return null;
      const delta = both.reduce((s, m) => s + cells.get(m)!.get(key)!.value - cells.get(m)!.get(key - 4)!.value, 0);
      return (delta / base) * 100;
    }),
  }));
  const total = periods.map(({ key }) => {
    const now = totals.get(key);
    const before = totals.get(key - 4);
    return now !== undefined && before && before > 0 ? (now / before - 1) * 100 : null;
  });
  const unattributed = total.map((g, i) => {
    if (g === null) return null;
    const parts = out.map((r) => r.points[i]).filter((p): p is number => p !== null);
    return parts.length ? g - parts.reduce((s, p) => s + p, 0) : null;
  });
  return {
    periods: periods.map(({ key, label }) => ({ key, label })),
    rows: out,
    total,
    unattributed,
    blocked: periods.map(({ key }) => plan.blocked.has(key)),
  };
}

/** The decomposition as a chart: contributions stacked (negatives downward) and revenue growth
 * (TTM YoY, the formula API) as a line. */
export function decompositionChart(t: AxisTable, companyId: string, ticker: string): ChartState {
  const layers = segmentLayers(t, companyId, "contrib", "sbar", 1);
  const id = layers.length + 1;
  return {
    title: `${ticker}: contributions to revenue growth by ${t.axis === "geography" ? "geography" : t.axis === "product_service" ? "product or service" : "business segment"} (TTM, YoY)`,
    layers: [...layers, { id, kind: "metric", companyId, metric: "revenue", transform: "yoy", type: "line", axis: "L", name: "Revenue growth (TTM YoY)", color: 7 }],
    range: 3,
    align: "fiscal",
    nextId: id + 1,
  };
}

export interface QualityRow {
  label: string;
  end: string;
  /** the quarter's revenue (not a TTM) */
  revenue: number;
  receivables: number;
  /** receivables / the quarter's revenue × the quarter's days */
  dso: number;
  receivablesGrowth: number | null;
  /** the quarter's revenue against the same quarter a year earlier */
  revenueGrowth: number | null;
  /** receivables growth − revenue growth, in points */
  gap: number | null;
  flagged: boolean;
}

const DAY_MS = 86_400_000;
/** a quarter's length in days from its own dates (Costco's 16-week Q4 is 112), else a year's quarter */
function quarterDays(r: FinancialRow | undefined): number {
  const days = r?.period_start ? Math.round((Date.parse(r.period_end) - Date.parse(r.period_start)) / DAY_MS) + 1 : NaN;
  return days >= 80 && days <= 120 ? days : 365 / 4;
}

/** Revenue quality per quarter (the latest `n`), from /financials QUARTER rows: the quarter's revenue
 * and receivables at its end, each against the same quarter a year earlier (rules in the header). */
export function revenueQuality(rows: FinancialRow[], n = PERIODS): QualityRow[] {
  const rev = totalsByKey(rows, "revenue");
  const ar = totalsByKey(rows, "receivables");
  const label = new Map<number, { label: string; end: string }>();
  const revenueRow = new Map<number, FinancialRow>();
  for (const r of rows) {
    const k = periodKey(r.fiscal_period);
    if (k !== null && r.metric === "receivables") label.set(k, { label: r.fiscal_period!, end: r.period_end });
    if (k !== null && r.metric === "revenue") revenueRow.set(k, r);
  }
  const keys = [...ar.keys()].filter((k) => rev.has(k) && (rev.get(k) ?? 0) > 0).sort((a, b) => a - b).slice(-n);
  return keys.map((k) => {
    const R = rev.get(k)!;
    const A = ar.get(k)!;
    const R0 = rev.get(k - 4);
    const A0 = ar.get(k - 4);
    const revenueGrowth = R0 && R0 > 0 ? R / R0 - 1 : null;
    const receivablesGrowth = A0 && A0 > 0 ? A / A0 - 1 : null;
    const gap = revenueGrowth !== null && receivablesGrowth !== null ? (receivablesGrowth - revenueGrowth) * 100 : null;
    return {
      label: label.get(k)!.label,
      end: label.get(k)!.end,
      revenue: R,
      receivables: A,
      dso: (A / R) * quarterDays(revenueRow.get(k)),
      receivablesGrowth,
      revenueGrowth,
      gap,
      flagged: gap !== null && gap > QUALITY_FLAG_POINTS + 1e-9, // exactly 10 pts isn't (float noise)
    };
  });
}

/** Receivables vs revenue growth as a chart, the flag rule's two sides: receivables at each quarter
 * end and that quarter's revenue, each against the same quarter a year earlier (formula API, quarter
 * view). */
export function qualityChart(companyId: string, ticker: string): ChartState {
  return {
    title: `${ticker}: receivables vs revenue growth (YoY)`,
    layers: [
      { id: 1, kind: "formula", formula: "revenue / revenue[-4q] - 1", defaultCompany: companyId, transform: "q", type: "line", axis: "L", name: "Revenue growth (quarter YoY)", color: 0 },
      { id: 2, kind: "formula", formula: "receivables / receivables[-4q] - 1", defaultCompany: companyId, transform: "q", type: "line", axis: "L", name: "Receivables growth (YoY)", color: 2 },
    ],
    range: 3,
    align: "fiscal",
    nextId: 3,
  };
}
