import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode, type RefObject } from "react";

import { day } from "../format";
import { chartSize, footnoteLines, layout, nearest, nearestSeries, titleLines, WIDTH, type Layout } from "./layout";
import type { Marker } from "./markers";
import { formatValue, unitLabel } from "./metrics";
import { chartNotes } from "./notes";
import { pointAt, xAxis, type Point, type Series, type Source } from "./series";
import { TableScroll } from "../components/TableScroll";

interface Props {
  series: Series[];
  title: string;
  /** source note, drawn into the SVG so exports carry it */
  footnote: string[];
  svgRef: RefObject<SVGSVGElement | null>;
  /** filings on the time axis (10-K, 10-Q, 8-K results) */
  markers?: Marker[];
  /** clicking a point (or Enter on it, or a marker) opens its source filing */
  onOpen?: (source: Source) => void;
  /** a second line under the title (deck output) */
  subtitle?: string;
  /** deck output: the light palette on screen too, as the exports draw it */
  deck?: boolean;
  /** a small multiple: a narrower, shorter viewBox (same text size, so it stays legible) */
  compact?: boolean;
  /** the earliest period end the chart asked for, so it can say when nothing goes back that far */
  from?: string | null;
  /** a log scale on the left axis (Compare's dollar charts) */
  log?: boolean;
  /** false: no notes about gaps (a model's forecast starting where its history stops isn't one) */
  notes?: boolean;
  /** layers (by id) with nothing on file at all: the note says so, not "in this range" */
  emptyOnFile?: number[];
}

/** stroke opacity of a faint series (a model's bear / bull case): an SVG attribute, not a style */
const FAINT = 0.5;
const COMPACT_WIDTH = 460;
const COMPACT_PLOT_H = 200;

/** The newest filing behind a point: the one a click opens. */
export function newestSource(p: Point | undefined): Source | null {
  return p?.sources.reduce<Source | null>((a, b) => (!a || b.filed > a.filed ? b : a), null) ?? null;
}

/** The chart builder's chart: plain SVG (no inline styles, so the CSP holds), with title,
 * legend, source footnote and what the chart leaves out (chart/notes.ts) inside the SVG for deck
 * exports. Hover (or the arrow keys) shows every layer's value, period and source filing; a click
 * or Enter opens the filing in the reader. "View as table" shows the same values as a data table.
 *
 * On screen it is drawn for the width it has (layout.ts `chartSize`), so its text is its real size
 * whatever the window. `svgRef` always gets the standard-size drawing (hidden when that isn't the
 * one on screen), so Export SVG / PNG give the same deck-ready image from a phone or a wide monitor. */
export function LayerChart({ series, title, footnote, svgRef, markers = [], onOpen, subtitle = "", deck = false, compact = false, from = null, log = false, notes = true, emptyOnFile = [] }: Props) {
  const room = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState<number | null>(null);
  useEffect(() => {
    const el = room.current;
    if (!el || compact || typeof ResizeObserver === "undefined") return;
    const seen = new ResizeObserver(([e]) => {
      const w = Math.round(e!.contentRect.width);
      if (w > 0) setMeasured(w);
    });
    seen.observe(el);
    return () => seen.disconnect();
  }, [compact]);
  const fixed = compact ? { width: COMPACT_WIDTH, plotHeight: COMPACT_PLOT_H } : { width: WIDTH };
  const size = compact || measured === null ? fixed : chartSize(measured);
  const said = useMemo(
    () => (notes ? [...footnote, ...chartNotes(series, { from, brief: compact, emptyOnFile: new Set(emptyOnFile) })] : footnote),
    [footnote.join("\n"), series, from, compact, notes, emptyOnFile.join(",")],
  );
  const drawnFor = (o: { width?: number; plotHeight?: number }) => {
    const w = o.width ?? WIDTH;
    const foot = footnoteLines(said, w);
    const heading = titleLines(title, w);
    const lay = layout(series, Boolean(title), markers, { ...o, log, subtitle: Boolean(subtitle), footLines: foot.length, titleLines: heading.length });
    return { lay, foot, heading };
  };
  const shown = useMemo(() => drawnFor(size), [series, title, markers, subtitle, said, size.width, size.plotHeight, log]);
  const standard = useMemo(() => drawnFor(fixed), [series, title, markers, subtitle, said, compact, log]);
  const lay = shown.lay;
  const sameDrawing = shown.lay.width === standard.lay.width && shown.lay.height === standard.lay.height;
  const [hover, setHover] = useState<number | null>(null);
  const [sel, setSel] = useState(0);
  const [table, setTable] = useState(false);
  const help = useId();
  const { plot } = lay;

  const toView = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    return { x: ((e.clientX - box.left) * lay.width) / box.width, y: ((e.clientY - box.top) * lay.height) / box.height };
  };
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const p = toView(e);
    if (!p || !lay.ticks.length) return;
    const i = nearest(lay, p.x);
    setHover(i);
    const k = nearestSeries(lay, i, p.y);
    if (k >= 0) setSel(k);
  };
  const open = (i: number, k: number) => {
    const s = series[k];
    const src = s ? newestSource(pointAt(s, lay.ticks[i]!.key)) : null;
    if (src && onOpen) onOpen(src);
  };
  const onClick = (e: MouseEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest?.(".ch-mk")) return;
    const p = toView(e);
    if (!p || !lay.ticks.length || !onOpen) return;
    const i = nearest(lay, p.x);
    const k = nearestSeries(lay, i, p.y);
    if (k >= 0) open(i, k);
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const n = lay.ticks.length;
    if (!n || (e.target as Element) !== e.currentTarget) return;
    const cur = hover ?? n - 1;
    const moves: Record<string, () => void> = {
      ArrowLeft: () => setHover(Math.max(0, cur - 1)),
      ArrowRight: () => setHover(Math.min(n - 1, cur + 1)),
      Home: () => setHover(0),
      End: () => setHover(n - 1),
      ArrowUp: () => setSel((s) => (s - 1 + series.length) % series.length),
      ArrowDown: () => setSel((s) => (s + 1) % series.length),
      Enter: () => open(cur, sel),
      Escape: () => setHover(null),
    };
    const f = moves[e.key];
    if (f) {
      e.preventDefault();
      if (hover === null && e.key !== "Escape" && e.key !== "Enter") setHover(cur);
      f();
    }
  };
  const tick = hover !== null ? lay.ticks[hover] : undefined;
  const periodEnd = tick
    ? series.flatMap((s) => pointAt(s, tick.key)?.periodEnd ?? []).sort().at(-1)
    : undefined;
  const openMarker = (m: Marker) =>
    onOpen?.({ form: m.kind, filed: m.filed, accessionNo: m.accessionNo, url: null, periodEnd: m.filed, companyId: m.companyId });

  return (
    <div className={["lchart", deck && "deck", compact && "compact"].filter(Boolean).join(" ")}>
      <div className="lchart-bar">
        <span className="visually-hidden" id={help}>
          {onOpen ? "Click a point to open its filing. " : ""}Focus the chart for arrow keys
          {onOpen ? " (Enter opens the filing)" : ""}.
        </span>
        <span className="grow" />
        <button className="btn sm ghost" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "View as chart" : "View as table"}
        </button>
      </div>
      <div hidden={table} ref={room}>
        <svg
          ref={sameDrawing ? svgRef : undefined}
          className={onOpen ? "lchart-svg clickable" : "lchart-svg"}
          viewBox={`0 0 ${lay.width} ${lay.height}`}
          role="group"
          aria-roledescription="chart"
          aria-label={title || "Chart"}
          aria-describedby={help}
          tabIndex={0}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          onClick={onClick}
          onKeyDown={onKey}
          onBlur={() => setHover(null)}
        >
          <Drawing lay={lay} heading={shown.heading} subtitle={subtitle} foot={shown.foot} onMarker={onOpen ? openMarker : undefined}>
            {hover !== null && (
              <line className="hover-only ch-hover" x1={lay.x[hover]} x2={lay.x[hover]} y1={plot.t} y2={plot.b} />
            )}
            {hover !== null && lay.drawn[sel]?.ys[hover] != null && (
              <circle className={`hover-only ch-dot c${lay.drawn[sel]!.color}`} cx={lay.x[hover]} cy={lay.drawn[sel]!.ys[hover]!} r={4} />
            )}
          </Drawing>
        </svg>
        {tick && (
          <div className={`tip ${lay.x[hover!]! > lay.width / 2 ? "at-l" : "at-r"}`} role="tooltip" aria-live="polite">
            <div className="h">
              {tick.period}
              {periodEnd && <span className="faint"> · to {day(periodEnd, true)}</span>}
            </div>
            {series.map((s, k) => {
              const p = pointAt(s, tick.key);
              if (!s.points.some((x) => x.y !== null)) return null;
              return (
                <div key={s.id} className={k === sel ? "tip-r sel" : "tip-r"}>
                  <div className="row">
                    <svg className={`sw c${s.color}`} width="9" height="9" aria-hidden="true">
                      <rect width="9" height="9" rx="2" fill="currentColor" />
                    </svg>
                    <span className="grow">{s.name}</span>
                    <b className="num">{p?.y != null ? formatValue(p.y, s.unit) : "—"}</b>
                  </div>
                  {p && p.sources.length > 0 && (
                    <div className="tip-src">
                      {p.sources.map((src) => (
                        <div key={src.accessionNo}>
                          {src.form} filed {day(src.filed, true)} · {src.accessionNo}
                          {src.periodEnd !== p.periodEnd && ` · period to ${day(src.periodEnd, true)}`}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {onOpen && <div className="faint small">Click or press Enter to open the highlighted layer's filing</div>}
          </div>
        )}
      </div>
      {table && <DataTable series={series} title={title} onOpen={onOpen} />}
      {!sameDrawing && (
        <div hidden className="lchart-export">
          <svg ref={svgRef} viewBox={`0 0 ${standard.lay.width} ${standard.lay.height}`} aria-hidden="true">
            <Drawing lay={standard.lay} heading={standard.heading} subtitle={subtitle} foot={standard.foot} />
          </svg>
        </div>
      )}
    </div>
  );
}

/** What's inside the chart's SVG: the one on screen and the standard size that exports take. */
function Drawing({ lay, heading, subtitle, foot, onMarker, children }: {
  lay: Layout;
  heading: string[];
  subtitle: string;
  foot: string[];
  onMarker?: (m: Marker) => void;
  /** the hover marks, on screen only */
  children?: ReactNode;
}) {
  const { plot } = lay;
  return (
    <>
      <rect className="ch-bg" x={0} y={0} width={lay.width} height={lay.height} />
      {heading.map((line, i) => (
        <text key={i} className="ch-title" x={lay.lead} y={20 + i * 18}>
          {line}
        </text>
      ))}
      {subtitle && (
        <text className="ch-sub" x={lay.lead} y={(heading.length ? 38 : 18) + Math.max(0, heading.length - 1) * 18}>
          {subtitle}
        </text>
      )}
      {lay.units.left && (
        <text className="ch-unit" x={lay.leftCaption.x} y={plot.t - 6} textAnchor={lay.leftCaption.anchor}>
          {lay.units.left}
        </text>
      )}
      {lay.units.right && (
        <text className="ch-unit" x={lay.rightCaption.x} y={plot.t - 6} textAnchor={lay.rightCaption.anchor}>
          {lay.units.right}
        </text>
      )}
      <g className="ch-legend">
        {lay.legend.map((g) => (
          <g key={g.id} className={`c${g.color}`}>
            <line x1={g.x} x2={g.x + 14} y1={g.y - 4} y2={g.y - 4} stroke="currentColor"
              strokeWidth={g.dashed ? 2 : 4} strokeDasharray={g.dashed ? "4 2" : undefined}
              strokeOpacity={g.faint ? FAINT : undefined} />
            <text x={g.x + 18} y={g.y}>
              {g.name}
            </text>
          </g>
        ))}
      </g>
      <g className="ch-grid">
        {lay.grid.map((g, i) => (
          <g key={i}>
            <line x1={plot.l} x2={plot.r} y1={g.y} y2={g.y} />
            <text x={plot.l - 6} y={g.y + 4} textAnchor="end">
              {g.label}
            </text>
          </g>
        ))}
        {lay.rightLabels.map((g, i) => (
          <text key={`r${i}`} x={plot.r + 6} y={g.y + 4}>
            {g.label}
          </text>
        ))}
      </g>
      <g className="ch-gaps">
        {lay.gaps.map((g) => (
          <rect key={g.x} className="ch-gap" x={g.x} y={plot.t} width={g.w} height={plot.b - plot.t} />
        ))}
      </g>
      <g className="ch-axis">
        {lay.xLabels.map((x) => (
          <text key={x.label} x={x.x} y={lay.axisY} textAnchor="middle">
            {x.label}
          </text>
        ))}
      </g>
      {lay.drawn.map((d) => (
        <g key={d.id} className={`c${d.color}`}>
          {d.area && <path d={d.area} fill="currentColor" fillOpacity={d.type === "sarea" ? 0.35 : 0.15} />}
          {d.path && (
            <path d={d.path} fill="none" stroke="currentColor" strokeWidth={d.faint ? 1.5 : 2}
              strokeDasharray={d.dashed ? "5 3" : undefined} strokeOpacity={d.faint ? FAINT : undefined} />
          )}
          {d.bars?.map((b, i) => (
            <rect key={i} x={b.x} y={b.y} width={Math.max(1, b.w)} height={b.h} fill="currentColor" fillOpacity={0.85} />
          ))}
          {d.dots?.map((p) => (
            <circle key={p.x} cx={p.x} cy={p.y} r={2.5} fill="currentColor" fillOpacity={d.faint ? FAINT : undefined} />
          ))}
        </g>
      ))}
      {lay.markers.length > 0 && (
        <g className="ch-markers">
          {lay.markers.map((m) => (
            <g key={`${m.companyId}|${m.accessionNo}`} className={`ch-mk c${m.color}`}
              role={onMarker ? "link" : undefined} tabIndex={onMarker ? 0 : undefined}
              aria-label={m.label}
              onClick={onMarker ? () => onMarker(m) : undefined}
              onKeyDown={onMarker ? (e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  onMarker(m);
                }
              } : undefined}>
              <title>{m.label}</title>
              <line x1={m.x} x2={m.x} y1={plot.b} y2={plot.b + 5} stroke="currentColor" strokeWidth={1} />
              {m.letter && <text x={m.x} y={m.y} textAnchor="middle">{m.glyph}</text>}
            </g>
          ))}
        </g>
      )}
      {children}
      {foot.map((line, i) => (
        <text key={i} className="ch-foot" x={lay.lead} y={lay.foot + i * 14}>
          {line}
        </text>
      ))}
    </>
  );
}

/** The chart's values as a table: one row per period the chart's axis has (a missing one reads
 * "—"), one column per layer, each value with its period and source filing. */
export function DataTable({ series, title, onOpen }: { series: Series[]; title: string; onOpen?: (s: Source) => void }) {
  const ticks = useMemo(() => xAxis(series).reverse().map((t) => [t.key, t.period] as const), [series]);
  const yearly = ticks.length > 0 && !ticks.some(([, period]) => / Q[1-4]$/.test(period));
  return (
    <TableScroll label="Chart values">
      <table className="t">
        <caption className="visually-hidden">{title || "Chart"}: values by {yearly ? "year" : "quarter"}, newest first</caption>
        <thead>
          <tr>
            <th className="first" scope="col">Period</th>
            {series.map((s) => (
              <th key={s.id} className="n" scope="col">
                {s.name} <span className="unit">{unitLabel(s.unit)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ticks.map(([key, period]) => (
            <tr key={key}>
              <th className="first" scope="row">{period}</th>
              {series.map((s) => {
                const p = pointAt(s, key);
                const src = newestSource(p);
                const text = p?.y != null ? formatValue(p.y, s.unit) : "—";
                const why = p ? `${s.name}, ${period} (to ${day(p.periodEnd, true)}). Source: ` +
                  p.sources.map((x) => `${x.form} filed ${day(x.filed, true)}, ${x.accessionNo}`).join("; ") : undefined;
                return (
                  <td key={s.id} className={p?.y != null ? "n" : "n faint"} title={why}>
                    {src && onOpen && p?.y != null ? (
                      <button className="link num" onClick={() => onOpen(src)} aria-label={`${text}: open ${src.form} filed ${day(src.filed, true)}`}>
                        {text}
                      </button>
                    ) : (
                      text
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

