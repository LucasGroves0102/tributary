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
  const [scale, setScale] = useState<Scale>("dollars");

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
  const spread = charts.filter(isDollars).some((c) => {
    const latest = c.layers.flatMap(({ key }) => {
      const a = answer(key);
      const v = a?.state === "ok" ? Number(a.data.points.at(-1)?.value) : NaN;
      return v > 0 ? [v] : [];
    });
    return latest.length > 1 && Math.max(...latest) / Math.min(...latest) >= WIDE_SPREAD;
  });

  return (
    <div className="multiples">
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
            {scale === "dollars" && spread
              ? `The largest company here is over ${WIDE_SPREAD} times the smallest, which reads as zero in dollars: Log scale or Indexed shows them all.`
              : "For the dollar charts."}
          </span>
        </div>
      )}
      {charts.map((c) => {
        const dollars = isDollars(c);
        const indexed = dollars && scale === "indexed";
        const log = dollars && scale === "log";
        const series: Series[] = c.layers.flatMap(({ layer, key }, i) => {
          const a = answer(key);
          return a?.state === "ok" ? [toSeries(a.data, indexed ? { ...layer, transform: "idx" } : layer, label(layer.companyId!), i)] : [];
        });
        const loading = c.layers.some(({ key }) => answer(key)?.state !== "ok" && answer(key)?.state !== "error");
        const failed = c.layers.filter(({ key }) => answer(key)?.state === "error").length;
        return (
          <figure key={c.col} className="card multiple" aria-label={c.title}>
            {series.some((s) => s.points.length) ? (
              <Multiple series={series} title={indexed ? `${c.title}, indexed` : c.title} onOpen={open} log={log}
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

function Multiple({ series, title, onOpen, log, extra }: { series: Series[]; title: string; onOpen: (s: Source) => void; log: boolean; extra: string }) {
  const svg = useRef<SVGSVGElement>(null);
  return (
    <LayerChart compact series={series} title={title} onOpen={onOpen} svgRef={svg} log={log}
      footnote={[`Source: SEC filings via Tributary. Calendar quarters.${extra}`]} />
  );
}
