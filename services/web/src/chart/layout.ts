/** Chart geometry in viewBox units: scales, gridlines, paths and bars for each series. Pure,
 * so the drawing is tested without a browser; LayerChart only turns it into SVG elements. */

import { formatValue } from "./metrics";
import { pointAt, xAxis, type Series, type Tick } from "./series";

export const WIDTH = 900;
export const PLOT_H = 300;
const PAD = { l: 64, r: 16, rAxis: 64, t: 10, b: 26 };
const LEGEND_LINE = 18;
const CHAR_W = 6.6; // approximate width of a legend character at 11.5px

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
  /** line/area: the path; area also gets `area` closed to the zero line */
  path?: string;
  area?: string;
  bars?: { x: number; y: number; w: number; h: number }[];
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
  /** y of the footnote's first line (a second one sits 14 below) */
  foot: number;
}

function scaleFor(series: Series[]): Scale | null {
  const ys = series.flatMap((s) => s.points.map((p) => p.y)).filter((y): y is number => y !== null);
  return ys.length ? niceScale(Math.min(...ys), Math.max(...ys), series[0]!.unit) : null;
}

export function layout(series: Series[], title: boolean): Layout {
  const ticks = xAxis(series);
  const hasRight = series.some((s) => s.axis === "R");
  const l = PAD.l;
  const r = WIDTH - (hasRight ? PAD.rAxis : PAD.r);

  // title, then the legend wrapped to the width
  let y = title ? 34 : 14;
  let x = l;
  const legend: LegendItem[] = [];
  for (const s of series) {
    const name = s.name + (s.axis === "R" ? " (right axis)" : "");
    const w = 18 + name.length * CHAR_W + 16;
    if (x + w > WIDTH - PAD.r && x > l) {
      x = l;
      y += LEGEND_LINE;
    }
    legend.push({ id: s.id, color: s.color, name, x, y, dashed: s.axis === "R" && s.type !== "bar" });
    x += w;
  }
  const top = (series.length ? y : y - LEGEND_LINE) + 14;
  const t = top + PAD.t;
  const b = t + PLOT_H - PAD.t - PAD.b;

  const left = scaleFor(series.filter((s) => s.axis === "L"));
  const right = scaleFor(series.filter((s) => s.axis === "R"));
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

  const bars = series.filter((s) => s.type === "bar");
  const slot = n ? (r - l) / Math.max(n, 1) : 0;
  const bw = Math.max(2, (slot * 0.7) / Math.max(1, bars.length));
  const drawn: Drawn[] = series.map((s) => {
    const sc = s.axis === "R" ? right : left;
    const base = { id: s.id, color: s.color, type: s.type, dashed: s.axis === "R" && s.type !== "bar" };
    if (!sc) return base;
    const at = ticks.map((tk) => pointAt(s, tk.key)?.y ?? null);
    if (s.type === "bar") {
      const bi = bars.indexOf(s);
      const zero = yOf(sc, Math.max(0, sc.lo));
      return {
        ...base,
        bars: at.flatMap((v, i) => {
          if (v === null) return [];
          const yv = yOf(sc, v);
          return [{ x: xs[i]! - (bw * bars.length) / 2 + bi * bw, y: Math.min(yv, zero), w: bw - 1, h: Math.abs(zero - yv) }];
        }),
      };
    }
    let path = "";
    let pen = false;
    at.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      path += `${pen ? "L" : "M"}${xs[i]!.toFixed(1)},${yOf(sc, v).toFixed(1)}`;
      pen = true;
    });
    const out: Drawn = { ...base, path };
    if (s.type === "area") {
      const zero = yOf(sc, Math.max(0, sc.lo)).toFixed(1);
      const pts = at.flatMap((v, i) => (v === null ? [] : [[xs[i]!, yOf(sc, v)] as const]));
      if (pts.length) {
        out.area = `M${pts[0]![0].toFixed(1)},${zero}` +
          pts.map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`).join("") +
          `L${pts.at(-1)![0].toFixed(1)},${zero}Z`;
      }
    }
    return out;
  });

  return {
    width: WIDTH,
    height: b + PAD.b + 46,
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
    foot: b + PAD.b + 18,
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
