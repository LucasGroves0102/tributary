/** Which axis each series draws on. An axis holds one kind of number: dollars and dollars per
 * share, an index and a percent, or a percent and a multiple never share one, because the smaller
 * of them would flatten at zero under the other's ticks. A series asks for an axis (the layer's
 * Axis control); when that one already reads in another unit it moves to the other side, and when
 * both are taken it isn't drawn (a third unit has no honest place: the chart says so, and its
 * values stay in the tooltip, the table and the CSV). The first series on a side claims it, in
 * layer order. A series with no values claims nothing. Pure and idempotent: layout.ts plans the
 * drawing with it, and the chart builder and the deck subtitle read the same answer. */

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
  for (const s of series) {
    if (hasValues(s) && claimed[s.axis] === null) claimed[s.axis] = family(s.unit);
  }
  const axes = series.map((s): Axis | null => {
    if (!hasValues(s)) return s.axis;
    const f = family(s.unit);
    const other: Axis = s.axis === "L" ? "R" : "L";
    if (claimed[s.axis] === f) return s.axis;
    if (claimed[other] === f) return other;
    if (claimed[other] === null) {
      claimed[other] = f;
      return other;
    }
    return null;
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
