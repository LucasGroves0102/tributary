/** Which axis each series draws on. An axis holds one kind of number, and one kind of number has
 * one axis: dollars and dollars per share, an index and a percent, or a percent and a multiple never
 * share one, because the smaller of them would flatten at zero under the other's ticks; and two
 * percents (an operating margin and an FCF margin) never get a scale each, because two scales for
 * one unit make lines cross where the values don't (analyst test 4, finding 12). A series asks for an
 * axis (the layer's Axis control): it gets it when that side is free and its unit has no side yet;
 * when its unit already has a side it joins it; when the side it asked for reads in another unit it
 * moves to the other side; and when both sides are taken by other units it isn't drawn (a third unit
 * has no honest place: the chart says so, and its values stay in the tooltip, the table and the CSV).
 * The first series to ask for a side claims it, in layer order. A series with no values claims
 * nothing. Pure and idempotent: layout.ts plans the drawing with it, and the chart builder (each
 * layer's Axis control shows the side it is drawn on) and the deck subtitle read the same answer. */

import { unitLabel } from "./metrics";
import type { Axis, Series } from "./series";

export interface AxisPlan {
  /** per series, in order: the axis it draws on, null when it isn't drawn */
  axes: (Axis | null)[];
  /** the unit caption of each side, null when nothing draws there */
  left: string | null;
  right: string | null;
}

/** Units read on the same scale: a share, a change and points of growth are all "%". */
function family(unit: string): string {
  return unit === "ratio" || unit === "pct" || unit === "pts" ? "percent" : unit;
}

export const hasValues = (s: Series) => s.points.some((p) => p.y !== null);

/** One unit for an axis's ticks and caption: its series' own when they agree; otherwise, as they
 * can only differ inside the percent family, points of growth if any are, else a plain percent. */
export function axisUnit(series: Series[]): string {
  const units = [...new Set(series.filter(hasValues).map((s) => s.unit))];
  if (units.length <= 1) return units[0] ?? series[0]?.unit ?? "";
  return units.includes("pts") ? "pts" : "ratio";
}

export function planAxes(series: Series[]): AxisPlan {
  const claimed: Record<Axis, string | null> = { L: null, R: null };
  const sideOf = (f: string): Axis | null => (claimed.L === f ? "L" : claimed.R === f ? "R" : null);
  // the sides asked for, in layer order: a unit that already has one doesn't take the other
  for (const s of series) {
    const f = family(s.unit);
    if (hasValues(s) && claimed[s.axis] === null && sideOf(f) === null) claimed[s.axis] = f;
  }
  const axes = series.map((s): Axis | null => {
    if (!hasValues(s)) return s.axis;
    const f = family(s.unit);
    const own = sideOf(f);
    if (own) return own;
    const other: Axis = s.axis === "L" ? "R" : "L";
    const free = claimed[s.axis] === null ? s.axis : claimed[other] === null ? other : null;
    if (free) claimed[free] = f;
    return free;
  });
  const caption = (side: Axis) => {
    const on = series.filter((s, i) => axes[i] === side && hasValues(s));
    return on.length ? unitLabel(axisUnit(on)) : null;
  };
  return { axes, left: caption("L"), right: caption("R") };
}

/** The series that really draw: they have values and an axis takes them. */
export function drawnIds(series: Series[]): Set<number> {
  const { axes } = planAxes(series);
  return new Set(series.flatMap((s, i) => (axes[i] && hasValues(s) ? [s.id] : [])));
}

/** What each series asks for once the user puts series `id` on `side` (the layer's Axis control):
 * its whole unit goes there (one unit, one axis), a unit drawn on that side moves to the other, and
 * every other series asks for the side it is drawn on, so the next plan honours the choice. Series
 * with no values aren't in the answer (they keep their own ask). */
export function askSides(series: Series[], id: number, side: Axis): Map<number, Axis> {
  const { axes } = planAxes(series);
  const out = new Map<number, Axis>();
  const moved = series.find((s) => s.id === id && hasValues(s));
  if (!moved) return out.set(id, side);
  const f = family(moved.unit);
  const other: Axis = side === "L" ? "R" : "L";
  series.forEach((s, i) => {
    if (!hasValues(s)) return;
    const on = axes[i];
    if (family(s.unit) === f) out.set(s.id, side);
    else if (on === side) out.set(s.id, other);
    else if (on) out.set(s.id, on);
  });
  return out;
}
