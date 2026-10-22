import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, evaluateFormula, FormulaApiError, type FormulaRequest } from "../api";
import { cached, remember } from "../chart/cache";
import { autoSubtitle, methodNote, sourceFootnote } from "../chart/deck";
import { LayerChart } from "../chart/LayerChart";
import { metricLabel } from "../chart/metrics";
import { basis, frequencyOf, layerColor, layerName, request, type ChartState, type TtmCatalog } from "../chart/model";
import { toSeries, type Series, type Source } from "../chart/series";
import { BAND_LABELS, FILER_LABELS } from "../companies/screen";
import { SmallMultiples } from "../compare/SmallMultiples";
import { COMPARE_COLUMNS, DEFAULT_COLUMNS } from "../compare/state";
import { Skeleton } from "../components/States";
import { go, href } from "../route";
import type { FormulaSeries } from "../types";
import { restoreChartState, restoreCompare, restoreScreen } from "../workspaces/model";
import type { ReportItem } from "./model";

/** Columns a report's comps embed charts: the first few of the saved set. */
export const EMBED_COMPS_COLUMNS = 3;

interface Common {
  token: string;
  item: ReportItem;
  /** company id → ticker or name */
  label: (companyId: string) => string;
}

type Result = { state: "loading" } | { state: "ok"; data: FormulaSeries } | { state: "error"; message: string };

/** A saved item drawn in a report from its saved state: the data is read now, through the
 * Query API as the viewer (an ungranted company shows "no access", as anywhere else). */
export function Embed(props: Common & { readable: ReadonlySet<string>; hasTtm: TtmCatalog; onOpenChart: (s: ChartState) => void }) {
  const { item } = props;
  return (
    <figure className="card report-embed" aria-label={item.name}>
      <figcaption className="embed-h">
        <span className={`tag ${item.kind}`}>{item.kind === "chart" ? "Chart" : item.kind === "comps" ? "Comps" : "Screen"}</span>{" "}
        <b>{item.name}</b>
        <span className="grow" />
        <span className="no-print">
          {item.kind === "chart" ? (
            <button className="btn sm ghost" onClick={() => props.onOpenChart(restoreChartState(item.state))}>
              Open in Chart builder
            </button>
          ) : item.kind === "comps" ? (
            <a className="btn sm ghost" href={href({ view: "compare", compare: restoreCompare(item.state) })}>
              Open in Compare
            </a>
          ) : (
            <a className="btn sm ghost" href={href({ view: "companies", screen: restoreScreen(item.state) })}>
              Open the screen
            </a>
          )}
        </span>
      </figcaption>
      {item.kind === "chart" ? (
        <EmbeddedChart {...props} />
      ) : item.kind === "comps" ? (
        <EmbeddedComps {...props} />
      ) : (
        <EmbeddedScreen {...props} />
      )}
    </figure>
  );
}

function EmbeddedChart({ token, item, label, hasTtm }: Common & { hasTtm: TtmCatalog }) {
  const chart = useMemo(() => restoreChartState(item.state), [item.state]);
  const visible = chart.layers.filter((l) => !l.hidden);
  const requests = visible.flatMap((layer) => {
    const req = request(layer, chart, hasTtm);
    return req ? [{ layer, req, key: JSON.stringify(req) }] : [];
  });
  const keys = requests.map((r) => r.key).join("\n");
  const [results, setResults] = useState<Record<string, Result>>({});
  const live = useRef(true);
  useEffect(() => () => {
    live.current = false;
  }, []);
  useEffect(() => {
    live.current = true;
    for (const { key } of requests) {
      if (results[key] || cached(token, key)) continue;
      setResults((r) => ({ ...r, [key]: { state: "loading" } }));
      evaluateFormula(token, JSON.parse(key) as FormulaRequest)
        .then((data) => {
          remember(token, key, data);
          if (live.current) setResults((r) => ({ ...r, [key]: { state: "ok", data } }));
        })
        .catch((e) => live.current && setResults((r) => ({
          ...r,
          [key]: { state: "error", message: e instanceof FormulaApiError || e instanceof ApiError ? e.message : "the Query API didn't answer" },
        })));
    }
    // results is read only to skip what's already asked
  }, [token, keys]);

  const result = (key: string): Result | undefined => {
    const hit = cached(token, key);
    return hit ? { state: "ok", data: hit } : results[key];
  };
  const frequency = frequencyOf(chart);
  const series: Series[] = requests.flatMap(({ layer, key }) => {
    const r = result(key);
    if (r?.state !== "ok") return [];
    return [toSeries(r.data, layer, layerName(layer, label, metricLabel, frequency),
      layerColor(layer, chart.layers.findIndex((x) => x.id === layer.id)), frequency)];
  });
  const errors = requests.flatMap(({ key }) => {
    const r = result(key);
    return r?.state === "error" ? [r.message] : [];
  });
  const loading = requests.some(({ key }) => !result(key) || result(key)?.state === "loading");
  const asOf = requests.reduce<string | null>((a, { key }) => {
    const r = result(key);
    return r?.state === "ok" && (!a || r.data.as_of > a) ? r.data.as_of : a;
  }, null);
  const svg = useRef<SVGSVGElement>(null);
  const open = (src: Source) => {
    if (src.companyId) go({ view: "filing", companyId: src.companyId, accessionNo: src.accessionNo, section: null });
  };

  if (!series.some((s) => s.points.length)) {
    if (loading) return <Skeleton chart label={`Loading ${item.name}`} />;
    return (
      <p className="empty">
        {errors.length ? `This chart couldn't be drawn: ${[...new Set(errors)].join("; ")}.` : "No values in the saved range."}
      </p>
    );
  }
  const effective = basis(chart);
  return (
    <>
      <LayerChart
        series={series}
        title={chart.title}
        subtitle={chart.deck ? chart.subtitle?.trim() || autoSubtitle(series, frequency, effective) : ""}
        footnote={[sourceFootnote(series, label, asOf), methodNote(effective, frequency, new Set(visible.map((l) => l.transform)))]}
        svgRef={svg}
        deck={chart.deck}
        onOpen={open}
      />
      {errors.length > 0 && <p className="faint small">{errors.length} line{errors.length === 1 ? "" : "s"} couldn't be drawn: {[...new Set(errors)].join("; ")}.</p>}
    </>
  );
}

function EmbeddedComps({ token, item, label, readable }: Common & { readable: ReadonlySet<string> }) {
  const s = restoreCompare(item.state);
  const ids = [s.target, ...(s.peers ?? [])].filter((x): x is string => !!x);
  const mine = ids.filter((id) => readable.has(id));
  const lost = ids.filter((id) => !readable.has(id));
  const cols = (s.cols?.length ? s.cols : DEFAULT_COLUMNS).filter((c) => COMPARE_COLUMNS.some((x) => x.key === c)).slice(0, EMBED_COMPS_COLUMNS);
  return (
    <>
      {mine.length ? (
        <SmallMultiples token={token} companyIds={mine} cols={cols} label={label} />
      ) : (
        <p className="empty">None of these companies' financials are on your plan.</p>
      )}
      {lost.length > 0 && (
        <p className="faint small">No access to {lost.map(label).join(", ")}: your plan doesn't include their financials.</p>
      )}
    </>
  );
}

function EmbeddedScreen({ item }: Common) {
  const f = restoreScreen(item.state);
  const parts = [
    f.q && `name or ticker “${f.q}”`,
    f.industry && `industry contains “${f.industry}”`,
    f.sic && `SIC ${f.sic}`,
    f.band && `revenue ${BAND_LABELS[f.band]}`,
    f.growth !== undefined && `revenue growth at least ${f.growth}%`,
    f.margin !== undefined && `operating margin at least ${f.margin}%`,
    f.fye && `fiscal year ends in month ${f.fye}`,
    f.exchange && `listed on ${f.exchange}`,
    f.filer && `${FILER_LABELS[f.filer].toLowerCase()} filers`,
    f.mine && "only companies on your plan",
  ].filter(Boolean);
  return (
    <p className="embed-screen">
      Screen: {parts.length ? parts.join(" · ") : "all companies"}
      {f.sort && <span className="muted"> · sorted by {f.sort.replace(/^-/, "").replace(/_/g, " ")}{f.sort.startsWith("-") ? ", highest first" : ""}</span>}
    </p>
  );
}
