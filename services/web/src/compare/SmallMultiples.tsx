import { useEffect, useRef, useState } from "react";

import { evaluateFormula, failureReason, type FormulaRequest } from "../api";
import { cached, remember } from "../chart/cache";
import { LayerChart } from "../chart/LayerChart";
import { companiesChart, emptyChart, MAX_COMPANY_LINES, request, type Range } from "../chart/model";
import { toSeries, type Series, type Source } from "../chart/series";
import { Skeleton } from "../components/States";
import { go } from "../route";
import type { FormulaSeries } from "../types";
import { chartMetric, COMPARE_COLUMNS } from "./state";

export const MULTIPLES_RANGE: Range = 3;

/** Dollar charts only: a $38M company beside $98B reads as zero on a dollar axis, so they can be
 * drawn on a log scale, or indexed to the first value in range. Margins and multiples already
 * compare as they are. Not saved: a way of looking, for this screen. */
type Scale = "dollars" | "log" | "indexed";
const SCALES: [Scale, string, string][] = [
  ["dollars", "Dollars", "As reported"],
  ["log", "Log scale", "Each gridline is ten times the one below; zero and negative values aren't drawn"],
  ["indexed", "Indexed", "The first value in the range = 100, so growth compares whatever the size"],
];
/** The largest company past this many times the smallest: the small ones flatten at zero. */
const WIDE_SPREAD = 100;

/** The dollar charts' note: which charts, by their own metric, have the largest company's latest
 * value over WIDE_SPREAD times the smallest's (both positive). Measured per chart, never across
 * them: revenue 14 times apart and free cash flow 130 times apart says so of free cash flow only
 * (analyst test 4, finding 17). */
export function spreadNote(charts: { name: string; latest: number[] }[]): string | null {
  const wide = charts.filter(({ latest }) => {
    const v = latest.filter((x) => Number.isFinite(x) && x > 0);
    return v.length > 1 && Math.max(...v) / Math.min(...v) >= WIDE_SPREAD;
  }).map((c) => c.name);
  if (!wide.length) return null;
  const names = wide.length === 1 ? wide[0]! : `${wide.slice(0, -1).join(", ")} and ${wide.at(-1)!}`;
  return `In ${names}, the largest company is over ${WIDE_SPREAD} times the smallest, which reads as zero in dollars: Log scale or Indexed shows them all.`;
}

/** The evaluate requests behind the small multiples: per column, one line per company (at most
 * MAX_COMPANY_LINES, the colours a chart tells apart), calendar quarters, the column's own
 * transform (TTM, quarter end, YoY), the last MULTIPLES_RANGE years. Same layer rules as the
 * chart builder (`request`), so "Chart these" opens the same lines. */
export function multiplesRequests(companyIds: string[], cols: string[], today?: Date) {
  return cols.flatMap((col) => {
    const m = chartMetric(col);
    if (!m) return [];
    const chart = { ...companiesChart(emptyChart(), companyIds.slice(0, MAX_COMPANY_LINES), m.metric, m.transform), range: MULTIPLES_RANGE };
    const c = COMPARE_COLUMNS.find((x) => x.key === col)!;
    return [{
      col,
      name: c.long,
      title: `${c.long}, ${c.period}`,
      layers: chart.layers.flatMap((l) => {
        // balances (quarter end) have no TTM; everything else here does
        const req = request(l, chart, () => !col.endsWith("_q"), today);
        return req ? [{ layer: l, req, key: JSON.stringify(req) }] : [];
      }),
    }];
  });
}

type Answer = { state: "ok"; data: FormulaSeries } | { state: "error"; message: string } | { state: "loading" };

/** Compare's side-by-side charts: one small chart per selected column, every readable company
 * a line in the same colour on each. Answers come from the session chart cache when there. */
export function SmallMultiples({ token, companyIds, cols, label }: {
  token: string;
  /** readable companies, target first */
  companyIds: string[];
  cols: string[];
  /** company id → ticker or name, for the legends */
  label: (companyId: string) => string;
}) {
  const charts = multiplesRequests(companyIds, cols);
  const keys = [...new Set(charts.flatMap((c) => c.layers.map((l) => l.key)))];
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  // null until the reader chooses: then indexed when the companies are far apart in size, else dollars
  const [chosen, setScale] = useState<Scale | null>(null);

  // answers are keyed by request, so one arriving after the columns changed is still kept
  const mounted = useRef(true);
  useEffect(() => () => {
    mounted.current = false;
  }, []);
  useEffect(() => {
    mounted.current = true;
    for (const k of keys) {
      if (answers[k]?.state === "ok" || answers[k]?.state === "loading" || cached(token, k)) continue;
      setAnswers((a) => ({ ...a, [k]: { state: "loading" } }));
      evaluateFormula(token, JSON.parse(k) as FormulaRequest)
        .then((data) => {
          remember(token, k, data);
          if (mounted.current) setAnswers((a) => ({ ...a, [k]: { state: "ok", data } }));
        })
        .catch((e) => mounted.current && setAnswers((a) => ({
          ...a,
          [k]: { state: "error", message: failureReason(e) },
        })));
    }
    // answers is read only to skip what's already asked
  }, [token, keys.join("\n")]);

  const answer = (k: string): Answer | undefined => {
    const hit = cached(token, k);
    return hit ? { state: "ok", data: hit } : answers[k];
  };
  const open = (src: Source) => {
    if (src.companyId) go({ view: "filing", companyId: src.companyId, accessionNo: src.accessionNo, section: null });
  };

  const isDollars = (c: (typeof charts)[number]) => c.layers.some(({ key }) => {
    const a = answer(key);
    return a?.state === "ok" && a.data.unit === "USD";
  });
  // per chart, by its own metric's latest values
  const spread = spreadNote(charts.filter(isDollars).map((c) => ({
    name: c.name.toLowerCase(),
    latest: c.layers.map(({ key }) => {
      const a = answer(key);
      return a?.state === "ok" ? Number(a.data.points.at(-1)?.value) : NaN;
    }),
  })));
  // a spread of over 100x flattens all but the largest in dollars (Riley 38): such a page opens indexed, and
  // says why; dollars is a click away
  const scale: Scale = chosen ?? (spread ? "indexed" : "dollars");
  // one scale per unit across the charts, when asked (Riley, test 10, finding 32): margins beside margins
  const [shared, setShared] = useState(false);
  const drawn = charts.map((c) => {
    const dollars = isDollars(c);
    const indexed = dollars && scale === "indexed";
    const series: Series[] = c.layers.flatMap(({ layer, key }, i) => {
      const a = answer(key);
      if (a?.state !== "ok") return [];
      const s = toSeries(a.data, indexed ? { ...layer, transform: "idx" } : layer, label(layer.companyId!), i);
      // the target (first) is drawn heavier: told apart by weight as well as colour (finding 32)
      return [i === 0 ? { ...s, strong: true } : s];
    });
    return { c, dollars, indexed, log: dollars && scale === "log", series, unit: series.find((s) => s.points.length)?.unit ?? null };
  });
  const spans = new Map<string, [number, number]>();
  for (const d of drawn) {
    if (!d.unit || d.log) continue;
    const ys = d.series.flatMap((s) => s.points.flatMap((p) => (p.y === null ? [] : [p.y])));
    if (!ys.length) continue;
    const was = spans.get(d.unit);
    spans.set(d.unit, [Math.min(was?.[0] ?? Infinity, ...ys), Math.max(was?.[1] ?? -Infinity, ...ys)]);
  }
  const sharing = drawn.filter((d) => d.unit && drawn.filter((x) => x.unit === d.unit).length > 1).length > 0;

  return (
    <div className="multiples">
      {sharing && (
        <div className="multiples-bar">
          <label className="chk small multiples-shared" title="Charts in the same unit (percent, dollars, multiples) on one scale, so their lines compare by height">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            <span>One scale for charts in the same unit</span>
          </label>
        </div>
      )}
      {charts.some(isDollars) && (
        <div className="multiples-bar">
          <span className="seg" role="group" aria-label="Dollar charts' scale">
            {SCALES.map(([s, text, hint]) => (
              <button key={s} className={scale === s ? "on" : undefined} aria-pressed={scale === s} title={hint} onClick={() => setScale(s)}>
                {text}
              </button>
            ))}
          </span>
          <span className="faint small">
            {spread && scale === "dollars" ? spread : spread && !chosen ? "Indexed: the companies are too far apart in size to read in dollars. Dollars shows their sizes." : "For the dollar charts."}
          </span>
        </div>
      )}
      {drawn.map(({ c, indexed, log, series, unit }) => {
        const loading = c.layers.some(({ key }) => answer(key)?.state !== "ok" && answer(key)?.state !== "error");
        const failed = c.layers.filter(({ key }) => answer(key)?.state === "error").length;
        return (
          <figure key={c.col} className="card multiple" aria-label={c.title}>
            {series.some((s) => s.points.length) ? (
              <Multiple series={series} title={indexed ? `${c.title}, indexed` : c.title} onOpen={open} log={log}
                span={shared && unit ? spans.get(unit) ?? null : null}
                extra={log ? " Log scale: zero and negative values aren't drawn." : indexed ? " Indexed: first value in the range = 100." : ""} />
            ) : loading ? (
              <Skeleton chart label={`Loading ${c.title}`} />
            ) : (
              <p className="empty">
                <b>{c.title}</b>: {failed ? "couldn't load these lines." : "no values in the last 3 years."}
              </p>
            )}
            {failed > 0 && series.length > 0 && <figcaption className="faint small">{failed} line{failed === 1 ? "" : "s"} couldn't load.</figcaption>}
          </figure>
        );
      })}
    </div>
  );
}

function Multiple({ series, title, onOpen, log, extra, span }: { series: Series[]; title: string; onOpen: (s: Source) => void; log: boolean; extra: string; span: [number, number] | null }) {
  const svg = useRef<SVGSVGElement>(null);
  return (
    <LayerChart compact series={series} title={title} onOpen={onOpen} svgRef={svg} log={log} span={span}
      footnote={[`Source: SEC filings via Tributary. Calendar quarters.${extra}`]} />
  );
}
