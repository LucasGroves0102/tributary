/** Chart geometry in viewBox units: scales, gridlines, paths and bars for each series, stacks,
 * and filing markers on the time axis. Pure, so the drawing is tested without a browser;
 * LayerChart only turns it into SVG elements. */

import type { Marker } from "./markers";
import { formatValue, unitLabel } from "./metrics";
import { pointAt, xAxis, type Series, type Tick } from "./series";

export const WIDTH = 900;
export const PLOT_H = 300;
const PAD = { l: 64, r: 16, rAxis: 64, t: 10, b: 26 };
const LEGEND_LINE = 18;
const CHAR_W = 6.6; // approximate width of a legend character at 11.5px
const MARKER_BAND = 18;
const DAY = 86400000;

export interface Scale {
  lo: number;
  hi: number;
  step: number;
  unit: string;
}

/** Round bounds and step covering [min, max] with about four intervals; includes zero. */
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
  return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step, unit };
}

export interface LegendItem {
  id: number;
  color: number;
  name: string;
  x: number;
  y: number;
  dashed: boolean;
}

export interface Drawn {
  id: number;
  color: number;
  type: Series["type"];
  dashed: boolean;
  /** line/area: the path; area also gets `area` closed to the zero line (or the stack below) */
  path?: string;
  area?: string;
  bars?: { x: number; y: number; w: number; h: number }[];
  /** per tick: the y of the point's value (the top of its stack segment), null where none */
  ys: (number | null)[];
}

export interface PlacedMarker extends Marker {
  x: number;
  y: number;
}

export interface Layout {
  width: number;
  height: number;
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
  legend: LegendItem[];
  drawn: Drawn[];
  markers: PlacedMarker[];
  /** y of the footnote's first line (each next one sits 14 below) */
  foot: number;
  /** axis unit captions above the plot ("USD", "%"), null without that axis */
  units: { left: string | null; right: string | null };
}

export interface LayoutOptions {
  /** a subtitle line under the title (deck output) */
  subtitle?: boolean;
  /** footnote lines to leave room for (default 2) */
  footLines?: number;
}

const stacked = (t: Series["type"]) => t === "sbar" || t === "sarea";
const barLike = (t: Series["type"]) => t === "bar" || t === "sbar";

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
 * the last one (filings come after their quarter ends), clamped to the plot. */
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
  const spacing = known.length > 1 ? (xl - known[0]![1]) / (known.length - 1) : 40;
  const x = xl + ((t - tl) / (91 * DAY)) * spacing;
  return x - xl > spacing * 1.2 ? null : Math.min(x, right);
}

function scaleFor(series: Series[], sp: ([number, number] | null)[][]): Scale | null {
  const ys = sp.flat().flatMap((v) => (v ? [v[0], v[1]] : []));
  return ys.length && series.length ? niceScale(Math.min(...ys), Math.max(...ys), series[0]!.unit) : null;
}

export function layout(series: Series[], title: boolean, markers: Marker[] = [], opts: LayoutOptions = {}): Layout {
  const ticks = xAxis(series);
  const hasRight = series.some((s) => s.axis === "R");
  const l = PAD.l;
  const r = WIDTH - (hasRight ? PAD.rAxis : PAD.r);

  // title, then the legend wrapped to the width
  let y = title ? (opts.subtitle ? 52 : 34) : opts.subtitle ? 32 : 14;
  let x = l;
  const legend: LegendItem[] = [];
  for (const s of series) {
    const name = s.name + (s.axis === "R" ? " (right axis)" : "");
    const w = 18 + name.length * CHAR_W + 16;
    if (x + w > WIDTH - PAD.r && x > l) {
      x = l;
      y += LEGEND_LINE;
    }
    legend.push({ id: s.id, color: s.color, name, x, y, dashed: s.axis === "R" && !barLike(s.type) });
    x += w;
  }
  const top = (series.length ? y : y - LEGEND_LINE) + 14;
  const t = top + PAD.t;
  const b = t + PLOT_H - PAD.t - PAD.b;

  const sp = spans(series, ticks);
  const on = (axis: "L" | "R") => series.flatMap((s, i) => (s.axis === axis ? [i] : []));
  const left = scaleFor(on("L").map((i) => series[i]!), on("L").map((i) => sp[i]!));
  const right = scaleFor(on("R").map((i) => series[i]!), on("R").map((i) => sp[i]!));
  const yOf = (sc: Scale, v: number) => b - ((v - sc.lo) / (sc.hi - sc.lo)) * (b - t);
  const n = ticks.length;
  const xs = ticks.map((_, i) => (n < 2 ? (l + r) / 2 : l + 14 + (i * (r - l - 28)) / (n - 1)));

  const lines = (sc: Scale | null) => {
    if (!sc) return [];
    const out = [];
    for (let v = sc.lo; v <= sc.hi + sc.step / 1e6; v += sc.step) {
      out.push({ y: yOf(sc, v), label: formatValue(Math.abs(v) < sc.step / 1e6 ? 0 : v, sc.unit, true) });
    }
    return out;
  };
  const every = Math.ceil(n / 10);
  const xLabels = ticks.flatMap((tk, i) =>
    i % every === 0 || i === n - 1 ? [{ x: xs[i]!, label: tk.period }] : [],
  );

  // bar slots: one per plain bar layer, one per stacked-bar group (axis)
  const slotKey = (s: Series) => (s.type === "sbar" ? `sbar|${s.axis}` : `bar|${s.id}`);
  const slots = [...new Set(series.filter((s) => barLike(s.type)).map(slotKey))];
  const slot = n ? (r - l) / Math.max(n, 1) : 0;
  const bw = Math.max(2, (slot * 0.7) / Math.max(1, slots.length));
  const drawn: Drawn[] = series.map((s, si) => {
    const sc = s.axis === "R" ? right : left;
    const base = { id: s.id, color: s.color, type: s.type, dashed: s.axis === "R" && !barLike(s.type) };
    if (!sc) return { ...base, ys: ticks.map(() => null) };
    const at = sp[si]!;
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
          return [{ x: xs[i]! - (bw * slots.length) / 2 + bi * bw, y: Math.min(y0, y1), w: bw - 1, h: Math.abs(y0 - y1) }];
        }),
      };
    }
    let path = "";
    let pen = false;
    ys.forEach((py, i) => {
      if (py === null) {
        pen = false;
        return;
      }
      path += `${pen ? "L" : "M"}${xs[i]!.toFixed(1)},${py.toFixed(1)}`;
      pen = true;
    });
    const out: Drawn = { ...base, ys, path };
    if (s.type === "area" || s.type === "sarea") {
      const zero = yOf(sc, Math.max(0, sc.lo));
      const pts = at.flatMap((v, i) => (v === null ? [] : [[xs[i]!, ys[i]!, s.type === "sarea" ? yOf(sc, v[1] > 0 || v[0] === 0 ? v[0] : v[1]) : zero] as const]));
      if (pts.length) {
        out.area = `M${pts[0]![0].toFixed(1)},${pts[0]![2].toFixed(1)}` +
          pts.map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`).join("") +
          [...pts].reverse().map(([px, , pb]) => `L${px.toFixed(1)},${pb.toFixed(1)}`).join("") + "Z";
      }
    }
    return out;
  });

  const ends = tickEnds(series, ticks);
  const placed: PlacedMarker[] = [];
  if (n) {
    for (const m of markers) {
      const mx = dateX(m.filed, ends, xs, r);
      if (mx !== null) placed.push({ ...m, x: mx, y: b + PAD.b + 10 });
    }
  }
  const band = markers.length ? MARKER_BAND : 0;
  const extraFoot = Math.max(0, (opts.footLines ?? 2) - 2) * 14;

  return {
    width: WIDTH,
    height: b + PAD.b + 46 + band + extraFoot,
    top,
    plot: { l, r, t, b },
    ticks,
    x: xs,
    left,
    right,
    grid: lines(left),
    rightLabels: lines(right),
    xLabels,
    legend,
    drawn,
    markers: placed,
    foot: b + PAD.b + 18 + band,
    units: { left: left ? unitLabel(left.unit) : null, right: right ? unitLabel(right.unit) : null },
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
