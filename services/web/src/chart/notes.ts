/** What a chart has to say about what isn't on it, in words, under the chart: drawn into the SVG
 * by LayerChart, so every chart and its PNG and SVG exports carry them. A layer no axis can hold,
 * a layer with nothing to draw, periods a layer has no value for, and a chart that starts later
 * than it was asked to. Gaps are never joined or filled (series.ts `xAxis`, layout.ts); this is
 * the sentence that goes with the hole. Pure. */

import { hasValues, planAxes } from "./axes";
import { unitLabel } from "./metrics";
import { xAxis, type Series, type Tick } from "./series";

const SHOWN = 3;
const DAY = 86400000;

const names = (list: string[]) =>
  list.length <= SHOWN ? list.join(", ") : `${list.slice(0, SHOWN).join(", ")} and ${list.length - SHOWN} more`;

/** "FY2017", "FY2017 Q2–FY2018 Q1" for runs of neighbouring ticks; a count past a few runs. */
function periods(missing: number[], ticks: Tick[]): string {
  const runs: [number, number][] = [];
  for (const i of missing) {
    const last = runs.at(-1);
    if (last && last[1] === i - 1) last[1] = i;
    else runs.push([i, i]);
  }
  if (runs.length > SHOWN) return `${missing.length} of ${ticks.length} periods`;
  return runs.map(([a, b]) => (a === b ? ticks[a]!.period : `${ticks[a]!.period}–${ticks[b]!.period}`)).join(", ");
}

export function chartNotes(series: Series[], opts: {
  /** the earliest period end the chart asked for (null: everything on file) */
  from?: string | null;
  /** a small multiple: its notes only, the reason goes with the group */
  brief?: boolean;
  /** series (by id) known to have nothing on file at all, not only in this range: the chart says
   * the same as the layer's own note ("No values on file"), never "in this range" beside it */
  emptyOnFile?: ReadonlySet<number>;
} = {}): string[] {
  const plan = planAxes(series);
  const plotted = series.filter((_, i) => plan.axes[i] !== null);
  const ticks = xAxis(plotted);
  const annual = ticks.length > 0 && !ticks.some((t) => / Q[1-4]$/.test(t.period));
  const out: string[] = [];

  for (const s of series.filter((_, i) => plan.axes[i] === null)) {
    out.push(`Not drawn: ${s.name} reads in ${unitLabel(s.unit) || "plain numbers"}, and both axes are taken (${plan.left}, ${plan.right}). Its values are in the table.`);
  }
  const empty = series.filter((s) => !hasValues(s));
  const none = empty.filter((s) => opts.emptyOnFile?.has(s.id));
  const inRange = empty.filter((s) => !opts.emptyOnFile?.has(s.id));
  if (none.length) out.push(`No values on file: ${names(none.map((s) => s.name))}.`);
  if (inRange.length) out.push(`No values in this range: ${names(inRange.map((s) => s.name))}.`);

  // what each series is missing against the chart's own axis
  const remarks: string[] = [];
  for (const s of plotted.filter(hasValues)) {
    const at = ticks.map((t) => s.points.some((p) => p.key === t.key && p.y !== null));
    const first = at.indexOf(true);
    const last = at.lastIndexOf(true);
    const parts: string[] = [];
    if (first > 0) parts.push(`starts ${ticks[first]!.period}`);
    const inner = at.flatMap((has, i) => (!has && i > first && i < last ? [i] : []));
    if (inner.length) parts.push(`no value for ${periods(inner, ticks)}`);
    // companies file at different times: one quarter behind the others is not a gap
    if (ticks.length - 1 - last >= (annual ? 1 : 2)) parts.push(`stops at ${ticks[last]!.period}`);
    if (parts.length) remarks.push(`${s.name}: ${parts.join(", ")}`);
  }
  if (remarks.length) {
    const more = remarks.length - SHOWN;
    out.push(`${remarks.slice(0, SHOWN).join("; ")}${more > 0 ? `; and ${more} more` : ""}.`);
  }

  // the whole chart starting later than it was asked to has nothing on the axis to compare with
  if (opts.from && ticks.length) {
    const firstEnd = plotted.flatMap((s) => s.points.filter((p) => p.key === ticks[0]!.key).map((p) => p.periodEnd)).sort()[0];
    const late = firstEnd ? (Date.parse(`${firstEnd}T00:00:00Z`) - Date.parse(`${opts.from}T00:00:00Z`)) / DAY : 0;
    if (late > (annual ? 450 : 200)) out.push(`Nothing on file before ${ticks[0]!.period} in the range asked for.`);
  }

  for (const s of series) if (s.note) out.push(s.note);

  if (!opts.brief && remarks.length) {
    out.push("A gap is a period with no value on file (a TTM needs all four quarters, a YoY the year before, a formula every input); nothing is filled in.");
  }
  return out;
}
