/** Chart geometry in viewBox units: scales, gridlines, paths and bars for each series, stacks,
 * and filing markers on the time axis. Pure, so the drawing is tested without a browser;
 * LayerChart only turns it into SVG elements.
 *
 * Truth rules, for every chart that draws through here: a series goes on the axis its unit allows
 * (axes.ts), a series with nothing to draw isn't in the legend, a period with no value is a hole
 * (series.ts `xAxis`: paths and areas break there, a point alone gets a dot), and margins are as
 * wide as the tick labels so no label is clipped. chart/notes.ts says the rest in words. */

import { axisUnit, hasValues, planAxes } from "./axes";
import type { Marker } from "./markers";
import { formatValue, unitLabel } from "./metrics";
import { pointAt, xAxis, type Series, type Tick } from "./series";

export const WIDTH = 900;
export const PLOT_H = 300;
const PAD = { l: 64, r: 16, rAxis: 64, t: 10, b: 26 };
const LEGEND_LINE = 18;
const CHAR_W = 6.6; // approximate width of a legend character at 11.5px
const FOOT_CHAR_W = 5.3; // of a footnote character at 10.5px italic
const TITLE_CHAR_W = 9; // of a title character at 15px semibold, the wider fallback fonts included
const TITLE_LINE = 18;
/** below this width the left margin shrinks, and titles and notes start at the card's edge */
const NARROW = 520;
const MIN_W = 300;
const MAX_W = 1400;
const MARKER_BAND = 14;
const TICK_CHAR_W = 6; // of an axis label at 10.5px
const CAPTION_CHAR_W = 6.2; // of an axis caption at 10px semibold
const LABEL_GAP = 10; // between period labels
const MARKER_LETTER_W = 9;
const MOST_BAR_W = 56;

/** The viewBox for the width a chart really has, so its text is drawn at its real size and never
 * scaled up past the page heading or down to nothing: a title is 15px at any width. */
export function chartSize(measured: number): { width: number; plotHeight: number } {
  const width = Math.round(Math.max(MIN_W, Math.min(MAX_W, measured)));
  return { width, plotHeight: Math.round(Math.max(210, Math.min(360, width * 0.34))) };
}

/** Text wrapped to the chart's width: SVG text doesn't wrap and is cut at the edge of its viewBox. */
export function wrapToWidth(lines: string[], width: number, charWidth: number): string[] {
  const most = Math.max(20, Math.floor(width / charWidth));
  return lines.flatMap((line) => {
    const out: string[] = [];
    let cur = "";
    for (const word of line.split(" ")) {
      if (cur && cur.length + 1 + word.length > most) {
        out.push(cur);
        cur = word;
      } else {
        cur = cur ? `${cur} ${word}` : word;
      }
    }
    if (cur) out.push(cur);
    return out;
  });
}

/** Where titles and notes start, and how much room they have, at a chart's width. */
export const textLeft = (width: number) => (width < NARROW ? 8 : PAD.l);
export const footnoteLines = (lines: string[], width: number) =>
  wrapToWidth(lines, width - textLeft(width) - 8, FOOT_CHAR_W);
export const titleLines = (title: string, width: number) =>
  title ? wrapToWidth([title], width - textLeft(width) - 8, TITLE_CHAR_W) : [];

export interface Scale {
  lo: number;
  hi: number;
  step: number;
  unit: string;
  /** ticks are powers of ten from lo to hi; a value at or below zero has no place on it */
  log?: boolean;
}

/** Round bounds and step covering [min, max] with about four intervals; includes zero. A value
 * a sliver past zero (a -$8K quarter beside $30M) doesn't earn a whole step of empty axis. */
export function niceScale(min: number, max: number, unit: string): Scale {
  let lo = Math.min(0, min);
  let hi = Math.max(0, max);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const raw = (hi - lo) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const err = raw / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const sliver = step * 0.02;
  return {
    lo: lo < 0 && -lo < sliver ? 0 : Math.floor(lo / step) * step,
    hi: hi > 0 && hi < sliver ? 0 : Math.ceil(hi / step) * step,
    step,
    unit,
  };
}

/** Whole decades around the positive values, or null without any. */
function logScale(values: number[], unit: string): Scale | null {
  const positive = values.filter((v) => v > 0);
  if (!positive.length) return null;
  const lo = Math.floor(Math.log10(Math.min(...positive)));
  const hi = Math.ceil(Math.log10(Math.max(...positive)));
  return { lo: 10 ** lo, hi: 10 ** Math.max(hi, lo + 1), step: 0, unit, log: true };
}

/** Where the gridlines go: every step, or every decade (every other one when there are many). */
function tickValues(sc: Scale | null): number[] {
  if (!sc) return [];
  const out: number[] = [];
  if (sc.log) {
    const from = Math.round(Math.log10(sc.lo));
    const to = Math.round(Math.log10(sc.hi));
    const by = to - from > 6 ? 2 : 1;
    for (let e = from; e <= to; e += by) out.push(10 ** e);
    return out;
  }
  for (let v = sc.lo; v <= sc.hi + sc.step / 1e6; v += sc.step) out.push(Math.abs(v) < sc.step / 1e6 ? 0 : v);
  return out;
}

const tickLabel = (sc: Scale, v: number) => formatValue(v, sc.unit, true, sc.log ? undefined : sc.step);
const widestLabel = (sc: Scale | null) => Math.max(0, ...tickValues(sc).map((v) => tickLabel(sc!, v).length));

export interface LegendItem {
  id: number;
  color: number;
  name: string;
  x: number;
  y: number;
  dashed: boolean;
  /** drawn lighter (a model's bear / bull case) */
  faint?: boolean;
}

export interface Drawn {
  id: number;
  color: number;
  type: Series["type"];
  dashed: boolean;
  faint?: boolean;
  /** line/area: the path; area also gets `area` closed to the zero line (or the stack below) */
  path?: string;
  area?: string;
  bars?: { x: number; y: number; w: number; h: number; partial?: boolean }[];
  /** the line's last stretch into an annual chart's year in progress (dashed), and its point */
  tail?: string;
  tailDot?: { x: number; y: number };
  /** per tick: the y of the point's value (the top of its stack segment), null where none */
  ys: (number | null)[];
  /** a value with no neighbour either side: a line can't show it, so a dot does */
  dots?: { x: number; y: number }[];
}

export interface PlacedMarker extends Marker {
  x: number;
  /** the baseline of its letter */
  y: number;
  /** whether its letter fits here; without it the marker is its stem, the hover still names it */
  letter: boolean;
}

export interface Layout {
  width: number;
  height: number;
  /** x where the title, legend and footnote start */
  lead: number;
  /** top of the plot (below title and legend) */
  top: number;
  plot: { l: number; r: number; t: number; b: number };
  ticks: Tick[];
  x: number[];
  left: Scale | null;
  right: Scale | null;
  grid: { y: number; label: string }[];
  rightLabels: { y: number; label: string }[];
  xLabels: { x: number; label: string }[];
  /** y of the period labels: below the plot, under the filing markers when there are any */
  axisY: number;
  /** the stretches of the axis no series has a value for, shaded */
  gaps: { x: number; w: number }[];
  legend: LegendItem[];
  drawn: Drawn[];
  markers: PlacedMarker[];
  /** y of the footnote's first line (each next one sits 14 below) */
  foot: number;
  /** axis unit captions above the plot ("USD", "%"), null or "" without one */
  units: { left: string | null; right: string | null };
  /** a caption too wide for its margin runs from the chart's edge over the plot's top */
  leftCaption: { x: number; anchor: "start" | "end" };
  rightCaption: { x: number; anchor: "start" | "end" };
}

export interface LayoutOptions {
  /** a subtitle line under the title (deck output) */
  subtitle?: boolean;
  /** footnote lines to leave room for (default 2) */
  footLines?: number;
  /** viewBox width (default WIDTH); small multiples draw narrower so their text stays legible */
  width?: number;
  /** plot height (default PLOT_H) */
  plotHeight?: number;
  /** title lines, when a narrow chart wraps it (default 1) */
  titleLines?: number;
  /** a log scale on the left axis, for lines of positive dollars (Compare); zero and negative
   * values aren't drawn on it */
  log?: boolean;
}

const stacked = (t: Series["type"]) => t === "sbar" || t === "sarea";
const barLike = (t: Series["type"]) => t === "bar" || t === "sbar";
/** Right-axis lines are dashed; a series may ask for it itself (a forecast). */
const dashedOf = (s: Series) => !barLike(s.type) && (s.dashed ?? s.axis === "R");

/** Per series per tick, the span it covers: [0, v] alone, or its segment of the stack it
 * belongs to (same stacked type and axis; positives stack up, negatives down). */
export function spans(series: Series[], ticks: Tick[]): ([number, number] | null)[][] {
  const pos = new Map<string, number[]>();
  const neg = new Map<string, number[]>();
  return series.map((s) => {
    const group = `${s.type}|${s.axis}`;
    if (stacked(s.type) && !pos.has(group)) {
      pos.set(group, ticks.map(() => 0));
      neg.set(group, ticks.map(() => 0));
    }
    return ticks.map((tk, i) => {
      const v = pointAt(s, tk.key)?.y ?? null;
      if (v === null) return null;
      if (!stacked(s.type)) return [0, v];
      const p = pos.get(group)!;
      const n = neg.get(group)!;
      if (v >= 0) {
        const lo = p[i]!;
        p[i] = lo + v;
        return [lo, lo + v];
      }
      const hi = n[i]!;
      n[i] = hi + v;
      return [hi + v, hi];
    });
  });
}

/** The latest period end plotted at each tick (for placing filings by date). */
function tickEnds(series: Series[], ticks: Tick[]): (string | null)[] {
  return ticks.map((tk) =>
    series.map((s) => pointAt(s, tk.key)?.periodEnd ?? "").sort().at(-1) || null);
}

/** x for a date: between the ticks whose period ends surround it; up to one tick spacing past
 * the last one (filings come after their period ends), clamped to the plot. */
export function dateX(date: string, ends: (string | null)[], xs: number[], right: number): number | null {
  const t = Date.parse(`${date.slice(0, 10)}T00:00:00Z`);
  const known = ends.flatMap((e, i) => (e ? [[Date.parse(`${e}T00:00:00Z`), xs[i]!] as const] : []));
  if (!known.length || Number.isNaN(t)) return null;
  if (t < known[0]![0]) return null;
  for (let i = 1; i < known.length; i++) {
    const [t0, x0] = known[i - 1]!;
    const [t1, x1] = known[i]!;
    if (t <= t1) return t1 === t0 ? x1 : x0 + ((t - t0) / (t1 - t0)) * (x1 - x0);
  }
  const [tl, xl] = known.at(-1)!;
  const [tf, xf] = known[0]!;
  const spacing = known.length > 1 ? (xl - xf) / (known.length - 1) : 40;
  const period = known.length > 1 ? (tl - tf) / (known.length - 1) : 91 * 86400000;
  const x = xl + ((t - tl) / period) * spacing;
  return x - xl > spacing * 1.2 ? null : Math.min(x, right);
}

function scaleFor(series: Series[], sp: ([number, number] | null)[][], log: boolean): Scale | null {
  const ys = sp.flat().flatMap((v) => (v ? [v[0], v[1]] : []));
  if (!ys.length || !series.length) return null;
  const unit = axisUnit(series);
  if (log) {
    const sc = logScale(sp.flat().flatMap((v) => (v ? [v[1]] : [])), unit);
    if (sc) return sc;
  }
  return niceScale(Math.min(...ys), Math.max(...ys), unit);
}

/** One shaded stretch per run of neighbouring periods nothing has. */
function gapRuns(ticks: Tick[], xs: number[], spacing: number): { x: number; w: number }[] {
  const out: { x: number; w: number }[] = [];
  const reach = spacing * 0.4;
  ticks.forEach((tk, i) => {
    if (!tk.gap) return;
    const last = out.at(-1);
    if (last && ticks[i - 1]?.gap) last.w = xs[i]! + reach - last.x;
    else out.push({ x: xs[i]! - reach, w: 2 * reach });
  });
  return out;
}

/** Letters where they fit: 10-Ks first, then 8-Ks, then 10-Qs, none touching another. */
function placeLetters(placed: PlacedMarker[]): PlacedMarker[] {
  const rank = { "10-K": 0, "8-K": 1, "10-Q": 2 } as const;
  const taken: number[] = [];
  const order = [...placed].sort((a, b) => rank[a.kind] - rank[b.kind] || a.x - b.x);
  const fits = new Set<PlacedMarker>();
  for (const m of order) {
    if (taken.every((x) => Math.abs(x - m.x) >= MARKER_LETTER_W)) {
      taken.push(m.x);
      fits.add(m);
    }
  }
  return placed.map((m) => ({ ...m, letter: fits.has(m) }));
}

export function layout(input: Series[], title: boolean, markers: Marker[] = [], opts: LayoutOptions = {}): Layout {
  // each series on the axis its unit allows; one that doesn't fit draws nothing (and says so)
  const plan = planAxes(input);
  const series = input.map((s, i) => {
    const axis = plan.axes[i];
    return axis ? { ...s, axis } : { ...s, points: [] };
  });
  const ticks = xAxis(series);
  const sp = spans(series, ticks);
  const on = (axis: "L" | "R") => series.flatMap((s, i) => (s.axis === axis && hasValues(s) ? [i] : []));
  const left = scaleFor(on("L").map((i) => series[i]!), on("L").map((i) => sp[i]!), !!opts.log);
  const right = scaleFor(on("R").map((i) => series[i]!), on("R").map((i) => sp[i]!), false);
  const hasRight = right !== null;

  const W = opts.width ?? WIDTH;
  const narrow = W < NARROW;
  const l = Math.max(narrow ? 48 : PAD.l, widestLabel(left) * TICK_CHAR_W + 10);
  const r = W - (hasRight ? Math.max(narrow ? 48 : PAD.rAxis, widestLabel(right) * TICK_CHAR_W + 10) : PAD.r);
  const lead = textLeft(W);

  // title, then the legend wrapped to the width: only what is drawn
  const extraTitle = title ? Math.max(0, (opts.titleLines ?? 1) - 1) * TITLE_LINE : 0;
  let y = (title ? (opts.subtitle ? 52 : 34) : opts.subtitle ? 32 : 14) + extraTitle;
  let x = lead;
  const legend: LegendItem[] = [];
  for (const s of series.filter(hasValues)) {
    const name = s.name + (s.axis === "R" ? " (right axis)" : "");
    const w = 18 + name.length * CHAR_W + 16;
    if (x + w > W - PAD.r && x > lead) {
      x = lead;
      y += LEGEND_LINE;
    }
    legend.push({ id: s.id, color: s.color, name, x, y, dashed: dashedOf(s), ...(s.faint ? { faint: true } : {}) });
    x += w;
  }
  const top = (legend.length ? y : y - LEGEND_LINE) + 14;
  const t = top + PAD.t;
  const b = t + (opts.plotHeight ?? PLOT_H) - PAD.t - PAD.b;

  const yOf = (sc: Scale, v: number) => {
    const at = sc.log
      ? (Math.log10(v) - Math.log10(sc.lo)) / (Math.log10(sc.hi) - Math.log10(sc.lo))
      : (v - sc.lo) / (sc.hi - sc.lo);
    return b - at * (b - t);
  };
  const n = ticks.length;
  // bars need half a bar slot at each end, or the first and last overprint the axis labels
  const inset = series.some((s) => barLike(s.type)) && n ? Math.max(14, (r - l) / n / 2) : 14;
  const xs = ticks.map((_, i) => (n < 2 ? (l + r) / 2 : l + inset + (i * (r - l - 2 * inset)) / (n - 1)));

  const lines = (sc: Scale | null) => tickValues(sc).map((v) => ({ y: yOf(sc!, v), label: tickLabel(sc!, v) }));

  // as many period labels as fit; the last always has its own, one running into it gives way
  const room = Math.max(0, ...ticks.map((tk) => tk.period.length)) * TICK_CHAR_W + LABEL_GAP;
  const spacing = n > 1 ? (xs[n - 1]! - xs[0]!) / (n - 1) : room;
  const every = Math.max(1, Math.ceil(room / spacing));
  const xLabels = ticks.flatMap((tk, i) =>
    (i % every === 0 && xs[n - 1]! - xs[i]! >= room) || i === n - 1 ? [{ x: xs[i]!, label: tk.partial ? `${tk.period}*` : tk.period }] : [],
  );

  // bar slots: one per plain bar layer, one per stacked-bar group (axis)
  const slotKey = (s: Series) => (s.type === "sbar" ? `sbar|${s.axis}` : `bar|${s.id}`);
  const slots = [...new Set(series.filter((s) => barLike(s.type) && hasValues(s)).map(slotKey))];
  const slot = n ? (r - l) / Math.max(n, 1) : 0;
  const bw = Math.max(2, Math.min(MOST_BAR_W, (slot * 0.7) / Math.max(1, slots.length)));
  const drawn: Drawn[] = series.map((s, si) => {
    const sc = s.axis === "R" ? right : left;
    const base = { id: s.id, color: s.color, type: s.type, dashed: dashedOf(s), ...(s.faint ? { faint: true } : {}) };
    if (!sc) return { ...base, ys: ticks.map(() => null) };
    const at = sp[si]!.map((v) => (sc.log && v !== null && v[1] <= 0 ? null : v));
    const val = (v: [number, number]) => (stacked(s.type) ? (v[1] > 0 || v[0] === 0 ? v[1] : v[0]) : v[1]);
    const ys = at.map((v) => (v === null ? null : yOf(sc, val(v))));
    if (barLike(s.type)) {
      const bi = slots.indexOf(slotKey(s));
      return {
        ...base,
        ys,
        bars: at.flatMap((v, i) => {
          if (v === null) return [];
          const lo = stacked(s.type) ? v[0] : Math.max(0, sc.lo);
          const hi = stacked(s.type) ? v[1] : v[1];
          const y0 = yOf(sc, lo);
          const y1 = yOf(sc, hi);
          return [{ x: xs[i]! - (bw * slots.length) / 2 + bi * bw, y: Math.min(y0, y1), w: bw - 1, h: Math.abs(y0 - y1), ...(ticks[i]!.partial ? { partial: true } : {}) }];
        }),
      };
    }
    let path = "";
    let pen = false;
    let tail: string | undefined;
    let tailDot: { x: number; y: number } | undefined;
    ys.forEach((py, i) => {
      if (py === null) {
        pen = false;
        return;
      }
      if (ticks[i]!.partial) {
        // the year in progress: a dashed stretch from the last year end, and a hollow point
        const prev = i > 0 ? ys[i - 1] : null;
        if (prev != null) tail = `M${xs[i - 1]!.toFixed(1)},${prev.toFixed(1)}L${xs[i]!.toFixed(1)},${py.toFixed(1)}`;
        tailDot = { x: xs[i]!, y: py };
        pen = false;
        return;
      }
      path += `${pen ? "L" : "M"}${xs[i]!.toFixed(1)},${py.toFixed(1)}`;
      pen = true;
    });
    // the year in progress has its own hollow point, and the dashed stretch shows the year end before it
    const dots = ys.flatMap((py, i) => (py !== null && !ticks[i]!.partial && ys[i - 1] == null && ys[i + 1] == null ? [{ x: xs[i]!, y: py }] : []));
    const out: Drawn = { ...base, ys, path, ...(dots.length ? { dots } : {}), ...(tail ? { tail } : {}), ...(tailDot ? { tailDot } : {}) };
    if (s.type === "area" || s.type === "sarea") {
      // one closed shape per run of values: an area doesn't bridge a gap either
      const zero = yOf(sc, Math.max(0, sc.lo));
      const runs: (readonly [number, number, number])[][] = [];
      let run: (readonly [number, number, number])[] | null = null;
      at.forEach((v, i) => {
        if (v === null) {
          run = null;
          return;
        }
        if (!run) {
          run = [];
          runs.push(run);
        }
        run.push([xs[i]!, ys[i]!, s.type === "sarea" ? yOf(sc, v[1] > 0 || v[0] === 0 ? v[0] : v[1]) : zero]);
      });
      if (runs.length) {
        out.area = runs.map((pts) =>
          `M${pts[0]![0].toFixed(1)},${pts[0]![2].toFixed(1)}` +
          pts.map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`).join("") +
          [...pts].reverse().map(([px, , pb]) => `L${px.toFixed(1)},${pb.toFixed(1)}`).join("") + "Z").join("");
      }
    }
    return out;
  });

  const ends = tickEnds(series, ticks);
  const placed: PlacedMarker[] = [];
  if (n) {
    for (const m of markers) {
      const mx = dateX(m.filed, ends, xs, r);
      if (mx !== null) placed.push({ ...m, x: mx, y: b + 16, letter: true });
    }
  }
  const band = markers.length ? MARKER_BAND : 0;
  const extraFoot = Math.max(0, (opts.footLines ?? 2) - 2) * 14;
  const leftUnit = left ? unitLabel(left.unit) + (left.log ? ", log scale" : "") : null;
  const rightUnit = right ? unitLabel(right.unit) : null;

  return {
    width: W,
    height: b + PAD.b + 46 + band + extraFoot,
    lead,
    top,
    plot: { l, r, t, b },
    ticks,
    x: xs,
    left,
    right,
    grid: lines(left),
    rightLabels: lines(right),
    xLabels,
    axisY: b + 18 + band,
    gaps: gapRuns(ticks, xs, spacing),
    legend,
    drawn,
    markers: placeLetters(placed),
    foot: b + PAD.b + 18 + band,
    units: { left: leftUnit, right: rightUnit },
    leftCaption: (leftUnit?.length ?? 0) * CAPTION_CHAR_W <= l - 8
      ? { x: l - 6, anchor: "end" }
      : { x: 4, anchor: "start" },
    rightCaption: (rightUnit?.length ?? 0) * CAPTION_CHAR_W <= W - r - 8
      ? { x: r + 6, anchor: "start" }
      : { x: W - 4, anchor: "end" },
  };
}

/** The tick index nearest an x in viewBox units. */
export function nearest(lay: Layout, x: number): number {
  let best = 0;
  lay.x.forEach((px, i) => {
    if (Math.abs(px - x) < Math.abs(lay.x[best]! - x)) best = i;
  });
  return best;
}

/** The series (index into drawn) whose point at tick `i` is nearest y, or -1 if none has one. */
export function nearestSeries(lay: Layout, i: number, y: number): number {
  let best = -1;
  lay.drawn.forEach((d, k) => {
    const py = d.ys[i];
    if (py == null) return;
    if (best < 0 || Math.abs(py - y) < Math.abs(lay.drawn[best]!.ys[i]! - y)) best = k;
  });
  return best;
}
