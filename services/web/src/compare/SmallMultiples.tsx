import { useEffect, useRef, useState } from "react";

import { ApiError, evaluateFormula, FormulaApiError, type FormulaRequest } from "../api";
import { cached, remember } from "../chart/cache";
import { LayerChart } from "../chart/LayerChart";
import { companiesChart, emptyChart, MAX_COMPANY_LINES, request, type Range } from "../chart/model";
import { toSeries, type Series, type Source } from "../chart/series";
import { Skeleton } from "../components/States";
import { go } from "../route";
import type { FormulaSeries } from "../types";
import { chartMetric, COMPARE_COLUMNS } from "./state";

export const MULTIPLES_RANGE: Range = 3;

/** The evaluate requests behind the small multiples: per column, one line per company (at most
 * MAX_COMPANY_LINES, the colours a chart tells apart), calendar quarters, the column's own
 * transform (TTM, quarter end, YoY), the last MULTIPLES_RANGE years. Same layer rules as the
 * chart builder (`request`), so "Chart these" opens the same lines. */
export function multiplesRequests(companyIds: string[], cols: string[], today?: Date) {
  return cols.flatMap((col) => {
    const m = chartMetric(col);
    if (!m) return [];
    const chart = { ...companiesChart(emptyChart(), companyIds.slice(0, MAX_COMPANY_LINES), m.metric, m.transform, m.title), range: MULTIPLES_RANGE };
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
          [k]: { state: "error", message: e instanceof FormulaApiError || e instanceof ApiError ? e.message : "the Query API didn't answer" },
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

  return (
    <div className="multiples">
      {charts.map((c) => {
        const series: Series[] = c.layers.flatMap(({ layer, key }, i) => {
          const a = answer(key);
          return a?.state === "ok" ? [toSeries(a.data, layer, label(layer.companyId!), i)] : [];
        });
        const loading = c.layers.some(({ key }) => answer(key)?.state !== "ok" && answer(key)?.state !== "error");
        const failed = c.layers.filter(({ key }) => answer(key)?.state === "error").length;
        return (
          <figure key={c.col} className="card multiple" aria-label={c.title}>
            {series.some((s) => s.points.length) ? (
              <Multiple series={series} title={c.title} onOpen={open} />
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

function Multiple({ series, title, onOpen }: { series: Series[]; title: string; onOpen: (s: Source) => void }) {
  const svg = useRef<SVGSVGElement>(null);
  return (
    <LayerChart compact series={series} title={title} onOpen={onOpen} svgRef={svg}
      footnote={["Source: SEC filings via Tributary. Calendar quarters."]} />
  );
}
