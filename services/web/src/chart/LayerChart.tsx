import { useMemo, useState, type MouseEvent, type RefObject } from "react";

import { day } from "../format";
import { layout, nearest } from "./layout";
import { formatValue } from "./metrics";
import { pointAt, type Series } from "./series";

interface Props {
  series: Series[];
  title: string;
  /** source note, drawn into the SVG so exports carry it */
  footnote: string[];
  svgRef: RefObject<SVGSVGElement | null>;
}

/** The chart builder's chart: plain SVG (no inline styles, so the CSP holds), with title,
 * legend and source footnote inside the SVG for deck exports. Hover shows every layer's value,
 * period and source filing. */
export function LayerChart({ series, title, footnote, svgRef }: Props) {
  const lay = useMemo(() => layout(series, Boolean(title)), [series, title]);
  const [hover, setHover] = useState<number | null>(null);
  const { plot } = lay;

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width || !lay.ticks.length) return;
    setHover(nearest(lay, ((e.clientX - box.left) * lay.width) / box.width));
  };
  const tick = hover !== null ? lay.ticks[hover] : undefined;
  const periodEnd = tick
    ? series.flatMap((s) => pointAt(s, tick.key)?.periodEnd ?? []).sort().at(-1)
    : undefined;

  return (
    <div className="lchart">
      <svg
        ref={svgRef}
        className="lchart-svg"
        viewBox={`0 0 ${lay.width} ${lay.height}`}
        role="img"
        aria-label={title || "Chart"}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <rect className="ch-bg" x={0} y={0} width={lay.width} height={lay.height} />
        {title && (
          <text className="ch-title" x={plot.l} y={20}>
            {title}
          </text>
        )}
        <g className="ch-legend">
          {lay.legend.map((g) => (
            <g key={g.id} className={`c${g.color}`}>
              <line x1={g.x} x2={g.x + 14} y1={g.y - 4} y2={g.y - 4} stroke="currentColor"
                strokeWidth={g.dashed ? 2 : 4} strokeDasharray={g.dashed ? "4 2" : undefined} />
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
        <g className="ch-axis">
          {lay.xLabels.map((x) => (
            <text key={x.label} x={x.x} y={plot.b + 18} textAnchor="middle">
              {x.label}
            </text>
          ))}
        </g>
        {lay.drawn.map((d) => (
          <g key={d.id} className={`c${d.color}`}>
            {d.area && <path d={d.area} fill="currentColor" fillOpacity={0.15} />}
            {d.path && (
              <path d={d.path} fill="none" stroke="currentColor" strokeWidth={2}
                strokeDasharray={d.dashed ? "5 3" : undefined} />
            )}
            {d.bars?.map((b, i) => (
              <rect key={i} x={b.x} y={b.y} width={Math.max(1, b.w)} height={b.h} fill="currentColor" fillOpacity={0.85} />
            ))}
          </g>
        ))}
        {hover !== null && (
          <line className="hover-only ch-hover" x1={lay.x[hover]} x2={lay.x[hover]} y1={plot.t} y2={plot.b} />
        )}
        {footnote.map((line, i) => (
          <text key={i} className="ch-foot" x={plot.l} y={lay.foot + i * 14}>
            {line}
          </text>
        ))}
      </svg>
      {tick && (
        <div className={`tip ${lay.x[hover!]! > lay.width / 2 ? "at-l" : "at-r"}`} role="tooltip">
          <div className="h">
            {tick.period}
            {periodEnd && <span className="faint"> · to {day(periodEnd, true)}</span>}
          </div>
          {series.map((s) => {
            const p = pointAt(s, tick.key);
            return (
              <div key={s.id} className="tip-r">
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
        </div>
      )}
    </div>
  );
}
