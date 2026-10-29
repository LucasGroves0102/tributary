import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import {
  ApiError,
  evaluateFormula,
  filingIndex,
  filings as fetchFilings,
  FormulaApiError,
  screener,
  screenerColumns,
  validateFormula,
  type FormulaRequest,
} from "../api";
import { apply as applyCompletion, complete, type Suggestion, type Vocabulary } from "../chart/autocomplete";
import { cached, remember } from "../chart/cache";
import { autoSubtitle, methodNote, sourceFootnote } from "../chart/deck";
import { LayerChart } from "../chart/LayerChart";
import { filingMarkers, MARKER_FORMS, type Marker } from "../chart/markers";
import { catalog, metricLabel, type MetricInfo } from "../chart/metrics";
import {
  addLayer,
  applyTemplate,
  autoName,
  basis,
  COLOR_NAMES,
  frequencyOf,
  LAYER_TYPES,
  layerColor,
  layerName,
  MAX_LAYER_NAME,
  MAX_LAYERS,
  moveLayer,
  RANGES,
  rangeDates,
  rangeLabel,
  removeLayer,
  request,
  SHARES_METRIC,
  TEMPLATES,
  TRANSFORMS,
  updateLayer,
  type ChartState,
  type Layer,
} from "../chart/model";
import { pngBlob, slug, svgText } from "../chart/export";
import { chartCsv, latestSource, toSeries, type Series, type Source } from "../chart/series";
import { ErrorPanel, Skeleton } from "../components/States";
import { download } from "../download";
import { day } from "../format";
import { go } from "../route";
import type { Filing, FormulaProblem, FormulaSeries, IndexedFiling } from "../types";

export interface ChartCompany {
  companyId: string;
  name: string;
  ticker: string | null;
}

type Result =
  | { state: "loading" }
  | { state: "ok"; data: FormulaSeries }
  | { state: "error"; message: string };

interface Props {
  token: string;
  chart: ChartState;
  setChart: (f: (s: ChartState) => ChartState) => void;
  /** Save / Save as (workspaces/SaveBar) */
  saveBar?: ReactNode;
}

/** Companies the user can chart: the screener restricted to companies whose financials they
 * can read (it also gives their tickers). */
function useChartCompanies(token: string): ChartCompany[] | null {
  const [out, setOut] = useState<ChartCompany[] | null>(null);
  useEffect(() => {
    let live = true;
    screener(token, { access: "financials", columns: ["revenue_ttm"], sort: "name", limit: 500 })
      .then((r) =>
        live &&
        setOut(r.rows.map((x) => ({ companyId: x.company_id, name: x.name ?? x.company_id, ticker: x.tickers?.[0] ?? null }))),
      )
      .catch(() => live && setOut([]));
    return () => {
      live = false;
    };
  }, [token]);
  return out;
}

function useMetrics(token: string): MetricInfo[] | null {
  const [out, setOut] = useState<MetricInfo[] | null>(null);
  useEffect(() => {
    let live = true;
    screenerColumns(token)
      .then((r) => live && setOut(catalog(r.columns)))
      .catch(() => live && setOut([]));
    return () => {
      live = false;
    };
  }, [token]);
  return out;
}

type Filings = { index: IndexedFiling[]; parsed: Filing[] };

/** Each company's 10-K/10-Q index (public) and parsed filings (8-K items; filings tier, empty
 * without it), fetched only while markers are on. */
function useFilings(token: string, companyIds: string[], from: string | null, on: boolean): Record<string, Filings> {
  const [out, setOut] = useState<Record<string, Filings>>({});
  const want = on ? companyIds.map((c) => `${c}|${from ?? ""}`) : [];
  const wantKey = want.join(",");
  useEffect(() => {
    let live = true;
    for (const k of want) {
      if (out[k]) continue;
      const [companyId] = k.split("|") as [string];
      setOut((o) => ({ ...o, [k]: { index: [], parsed: [] } }));
      Promise.all([
        filingIndex(token, companyId, { forms: MARKER_FORMS, from: from ?? undefined, limit: 100 }).then((r) => r.filings).catch(() => []),
        fetchFilings(token, companyId).catch(() => [] as Filing[]),
      ]).then(([index, parsed]) => live && setOut((o) => ({ ...o, [k]: { index, parsed } })));
    }
    return () => {
      live = false;
    };
    // out is read only to skip companies already fetched
  }, [token, wantKey]);
  return Object.fromEntries(want.flatMap((k) => (out[k] ? [[k.split("|")[0]!, out[k]!]] : [])));
}

/** Chart builder: layers of company × metric or formula over /v1/formulas/evaluate. */
export function ChartBuilder({ token, chart, setChart, saveBar }: Props) {
  const companies = useChartCompanies(token);
  const metrics = useMetrics(token);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [note, setNote] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const svg = useRef<SVGSVGElement>(null);

  const byId = useMemo(() => new Map((companies ?? []).map((c) => [c.companyId, c])), [companies]);
  const short = (id: string) => byId.get(id)?.ticker ?? byId.get(id)?.name ?? id;
  const ttm = useMemo(() => {
    const m = new Map((metrics ?? []).map((x) => [x.name, x.ttm]));
    return (name: string) => m.get(name) ?? true;
  }, [metrics]);
  // per share needs a diluted share count in the catalog; while it loads, assume it's there
  const hasShares = metrics === null || metrics.some((m) => m.name === SHARES_METRIC);

  const frequency = frequencyOf(chart);
  const visible = chart.layers.filter((l) => !l.hidden);
  const requests = visible.map((l) => ({ layer: l, req: request(l, chart, ttm) }));
  const keyOf = (req: FormulaRequest) => JSON.stringify(req);
  // answers already fetched this session (chart/cache.ts) show at once, e.g. back from another page
  const resultOf = (k: string): Result | undefined => {
    const hit = cached(token, k);
    return results[k] ?? (hit ? { state: "ok", data: hit } : undefined);
  };
  const wanted = requests.flatMap(({ req }) => (req ? [keyOf(req)] : []));
  const wantedKey = wanted.join("\n");

  // answers are keyed by request: one that arrives after the layers changed is still kept (a
  // dropped one would stay "loading" when the layer comes back)
  const mounted = useRef(true);
  useEffect(() => () => {
    mounted.current = false;
  }, []);
  useEffect(() => {
    mounted.current = true;
    const live = () => mounted.current;
    for (const k of new Set(wanted)) {
      if ((results[k] && results[k].state !== "error") || cached(token, k)) continue;
      setResults((r) => ({ ...r, [k]: { state: "loading" } }));
      evaluateFormula(token, JSON.parse(k) as FormulaRequest)
        .then((data) => {
          remember(token, k, data);
          if (live()) setResults((r) => ({ ...r, [k]: { state: "ok", data } }));
        })
        .catch(
          (e) =>
            live() &&
            setResults((r) => ({
              ...r,
              [k]: {
                state: "error",
                message:
                  e instanceof FormulaApiError
                    ? e.problems.map((p) => p.message + (p.hint ? `. ${p.hint}` : "")).join("; ")
                    : e instanceof ApiError
                      ? e.message
                      : "the Query API didn't answer",
              },
            })),
        );
    }
    // results is read only to skip requests already made; retry re-asks failed ones
  }, [token, wantedKey, retry]);

  const colorOf = (l: Layer) => layerColor(l, chart.layers.findIndex((x) => x.id === l.id));
  const series: Series[] = requests.flatMap(({ layer, req }) => {
    const r = req ? resultOf(keyOf(req)) : undefined;
    if (r?.state !== "ok") return [];
    return [toSeries(r.data, layer, layerName(layer, short, metricLabel, frequency), colorOf(layer), frequency)];
  });
  const status = (l: Layer): Result | undefined => {
    const req = request(l, chart, ttm);
    return req ? resultOf(keyOf(req)) : undefined;
  };
  const loading = requests.some(({ req }) => req && resultOf(keyOf(req))?.state === "loading");
  const failed = requests.filter(({ req }) => req && resultOf(keyOf(req))?.state === "error").length;
  const asOf = requests.reduce<string | null>((a, { req }) => {
    const r = req ? resultOf(keyOf(req)) : undefined;
    return r?.state === "ok" && (!a || r.data.as_of > a) ? r.data.as_of : a;
  }, null);

  const effective = basis(chart);
  const latest = latestSource(series);
  const transforms = new Set<string>(visible.map((l) => l.transform));
  const method = methodNote(effective, frequency, transforms);
  // deck output: the source line slides need (forms, companies, latest filing, as of)
  const footnote = chart.deck
    ? [sourceFootnote(series, short, asOf), method]
    : [
        `Source: SEC filings (10-Q, 10-K XBRL) via Tributary. ${method}`,
        latest ? `Latest filing used: ${latest.form} filed ${latest.filed} (${latest.accessionNo}).` : "",
      ].filter(Boolean);
  const subtitle = chart.deck ? chart.subtitle?.trim() || autoSubtitle(series, frequency, effective) : "";

  // filing markers: each company a visible metric layer (or formula's default company) reads
  const chartCompanies = [...new Set(visible.flatMap((l) => {
    const c = l.kind === "metric" ? l.companyId : l.defaultCompany;
    return c ? [c] : [];
  }))].slice(0, 8);
  const { from } = rangeDates(chart);
  const filingsBy = useFilings(token, chartCompanies, from, !!chart.markers);
  const markers: Marker[] = chart.markers
    ? chartCompanies.flatMap((c) => {
        const f = filingsBy[c];
        const layer = visible.find((l) => (l.kind === "metric" ? l.companyId : l.defaultCompany) === c);
        return f ? filingMarkers(c, short(c), layer ? colorOf(layer) : 0, f.index, f.parsed, from) : [];
      })
    : [];

  const vocab = useMemo((): Vocabulary => ({
    metrics: metrics ?? [],
    tickers: (companies ?? []).flatMap((c) => (c.ticker ? [{ ticker: c.ticker, name: c.name }] : [])),
  }), [metrics, companies]);
  const fallback = (companies ?? []).slice(0, 2).map((c) => c.companyId);
  const firstCompany = chart.layers.find((l) => l.companyId)?.companyId ?? fallback[0];
  const name = chart.title || "chart";

  const openSource = (src: Source) => {
    if (!src.companyId) {
      setNote(`That value comes from ${src.form} ${src.accessionNo}; the chart doesn't know its company, so open it from the company's Filings tab.`);
      return;
    }
    go({ view: "filing", companyId: src.companyId, accessionNo: src.accessionNo, section: null });
  };

  const exportCsv = () => series.length && download(`${slug(name)}.csv`, new Blob([chartCsv(series)], { type: "text/csv" }));
  const exportSvg = () => {
    if (!svg.current) return;
    download(`${slug(name)}.svg`, new Blob([svgText(svg.current)], { type: "image/svg+xml" }));
  };
  const exportPng = () => {
    if (!svg.current) return;
    const [, , w, h] = svg.current.getAttribute("viewBox")!.split(" ").map(Number);
    setNote(null);
    pngBlob(svgText(svg.current), w!, h!)
      .then((b) => download(`${slug(name)}.png`, b))
      .catch(() => setNote("Couldn't render the PNG in this browser. Export SVG instead."));
  };

  return (
    <div className="page">
      <div className="row wrap">
        <h1 className="grow">Chart builder</h1>
        <button className="btn" onClick={exportCsv} disabled={!series.length}
          title="Exact values from the API with each point's source accessions">
          Export CSV
        </button>
        <button className="btn" onClick={exportSvg} disabled={!series.length}>
          Export SVG
        </button>
        <button className="btn" onClick={exportPng} disabled={!series.length}>
          Export PNG
        </button>
        {saveBar}
      </div>
      {note && <p className="empty error" role="alert">{note}</p>}

      <div className="builder">
        <section className="card layers" aria-label="Layers">
          <div className="card-h">
            <h2 className="grow">Layers</h2>
            <button
              className="btn sm"
              disabled={!firstCompany || chart.layers.length >= MAX_LAYERS}
              onClick={() =>
                setChart((s) =>
                  addLayer(s, { kind: "metric", companyId: firstCompany, metric: "revenue", transform: "ttm", type: "line", axis: "L" }),
                )
              }
            >
              + Metric
            </button>
            <button
              className="btn sm"
              disabled={chart.layers.length >= MAX_LAYERS}
              onClick={() =>
                setChart((s) =>
                  addLayer(s, { kind: "formula", formula: "", defaultCompany: firstCompany, transform: "ttm", type: "line", axis: "R" }),
                )
              }
            >
              + Formula
            </button>
          </div>
          <div className="card-b layer-list">
            {companies === null && <Skeleton lines={3} label="Loading your companies" />}
            {companies !== null && companies.length === 0 && (
              <p className="faint small">
                Your plan covers no company's financials yet, so there's nothing to chart. Ask
                for a financials grant, or try the demo for a sandbox with 20 companies.
              </p>
            )}
            {chart.layers.map((l, i) => (
              <LayerEditor
                key={l.id}
                token={token}
                layer={l}
                index={i}
                count={chart.layers.length}
                color={colorOf(l)}
                auto={autoName(l, short, metricLabel, frequency)}
                annual={frequency === "annual"}
                vocab={vocab}
                companies={companies ?? []}
                metrics={metrics ?? []}
                hasShares={hasShares}
                basis={effective}
                status={status(l)}
                set={(patch) => setChart((s) => updateLayer(s, l.id, patch))}
                move={(by) => setChart((s) => moveLayer(s, l.id, by))}
                remove={() => setChart((s) => removeLayer(s, l.id))}
              />
            ))}
            {!hasShares && (
              <p className="faint small">
                Per share isn't available: the metric catalog has no diluted share count. QoQ,
                YoY and indexed still are.
              </p>
            )}
            <div>
              <div className="faint small">Templates</div>
              <div className="row wrap">
                {TEMPLATES.map((t) => (
                  <button key={t.label} className="btn sm" disabled={!fallback.length && !chart.layers.length}
                    title={t.hint + (t.per === "one" ? " (the chart's first company)" : "")}
                    onClick={() => setChart((s) => applyTemplate(s, t, fallback))}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="card grow chart-card">
          <div className="card-h wrap">
            <input
              className="field title-field"
              value={chart.title}
              maxLength={200}
              placeholder="Chart title"
              aria-label="Chart title"
              onChange={(e) => {
                const title = e.target.value;
                setChart((s) => ({ ...s, title }));
              }}
            />
            <span className="seg" role="group" aria-label="Range">
              {[...RANGES, "custom" as const].map((r) => (
                <button key={r} className={chart.range === r ? "on" : undefined} aria-pressed={chart.range === r}
                  title={r === "max" ? "Every quarter the API has" : r === "custom" ? "Choose the first and last period end" : `The last ${r} year${r === 1 ? "" : "s"}`}
                  onClick={() => setChart((s) => ({ ...s, range: r }))}>
                  {rangeLabel(r)}
                </button>
              ))}
            </span>
            {chart.range === "custom" && (
              <span className="row">
                <input type="date" className="field narrow" aria-label="First period end" value={chart.start ?? ""}
                  max={chart.end} onChange={(e) => {
                    const start = e.target.value || undefined;
                    setChart((s) => ({ ...s, start }));
                  }} />
                <span className="faint">to</span>
                <input type="date" className="field narrow" aria-label="Last period end" value={chart.end ?? ""}
                  min={chart.start} onChange={(e) => {
                    const end = e.target.value || undefined;
                    setChart((s) => ({ ...s, end }));
                  }} />
              </span>
            )}
            <span className="seg" role="group" aria-label="Frequency">
              {(["quarterly", "annual"] as const).map((f) => (
                <button key={f} className={frequency === f ? "on" : undefined} aria-pressed={frequency === f}
                  title={f === "annual" ? "One point per year: flows are year totals (TTM at Q4), balances year-end values" : "Every quarter"}
                  onClick={() => setChart((s) => ({ ...s, frequency: f === "annual" ? "annual" : undefined }))}>
                  {f === "annual" ? "Annual" : "Quarterly"}
                </button>
              ))}
            </span>
            <span className="seg" role="group" aria-label="Quarters">
              {(["calendar", "fiscal"] as const).map((a) => (
                <button key={a} className={chart.align === a ? "on" : undefined} aria-pressed={chart.align === a}
                  onClick={() => setChart((s) => ({ ...s, align: a }))}>
                  {a === "calendar" ? "Calendar" : "Fiscal"}
                </button>
              ))}
            </span>
            <label className="chk" title="10-K (K), 10-Q (Q) and 8-K results (8, item 2.02, where parsed) on the time axis">
              <input type="checkbox" checked={!!chart.markers}
                onChange={(e) => {
                  const markers = e.target.checked;
                  setChart((s) => ({ ...s, markers: markers || undefined }));
                }} />
              Filing markers
            </label>
            <label className="chk" title="For slides: light background, a subtitle with periods and units, and a source footnote naming forms, companies and the as-of date; Export SVG / PNG keep it">
              <input type="checkbox" checked={!!chart.deck}
                onChange={(e) => {
                  const deck = e.target.checked;
                  setChart((s) => ({ ...s, deck: deck || undefined }));
                }} />
              Deck output
            </label>
            <span className="sub">{loading ? "Loading…" : "hover for values and source filings"}</span>
          </div>
          {chart.deck && (
            <div className="card-h deck-h">
              <input className="field grow" value={chart.subtitle ?? ""} maxLength={200}
                placeholder={series.length ? autoSubtitle(series, frequency, effective) : "Subtitle (automatic: periods and units)"}
                aria-label="Subtitle"
                onChange={(e) => {
                  const subtitle = e.target.value;
                  setChart((s) => ({ ...s, subtitle: subtitle || undefined }));
                }} />
              <span className="sub">deck output: light background · source footnote added automatically</span>
            </div>
          )}
          {chart.align === "fiscal" && effective === "calendar" && (
            <p className="note">
              Fiscal quarters only line up within one company, so this chart of several companies
              uses calendar quarters.
            </p>
          )}
          {series.some((s) => s.points.length > 0) ? (
            <LayerChart series={series} title={chart.title} footnote={footnote} svgRef={svg}
              markers={markers} onOpen={openSource} subtitle={subtitle} deck={!!chart.deck} />
          ) : visible.length && loading ? (
            <Skeleton chart label="Loading the series" />
          ) : visible.length && failed ? (
            <ErrorPanel what="these layers" detail="Each layer below says what went wrong."
              onRetry={() => setRetry((n) => n + 1)} />
          ) : (
            <p className="empty">
              {visible.length
                ? frequency === "annual"
                  ? "No full years for these layers in this range. Try a longer range, or Quarterly."
                  : "No values for these layers in this range. Try a longer range (10y or Max), or another metric."
                : chart.layers.length
                  ? "Every layer is hidden. Show one, or add a metric or formula layer."
                  : "Start with + Metric (a company and a metric), + Formula (e.g. net_income / revenue), or one of the templates."}
            </p>
          )}
          {latest && (
            <div className="foot">
              Every point comes from a filing: hover for each layer's value, period and accession;
              click to open it in the reader. Latest: {latest.form} filed {day(latest.filed, true)}.
              {chart.markers && " Markers: K = 10-K, Q = 10-Q, 8 = 8-K results (item 2.02; only where the filing is parsed and your plan covers filings)."}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function LayerEditor({
  token,
  layer,
  index,
  count,
  color,
  auto,
  annual,
  vocab,
  companies,
  metrics,
  hasShares,
  basis,
  status,
  set,
  move,
  remove,
}: {
  token: string;
  layer: Layer;
  index: number;
  count: number;
  color: number;
  /** the automatic legend name (the placeholder of the name field) */
  auto: string;
  /** annual frequency: QoQ means nothing there (it charts as YoY) */
  annual: boolean;
  vocab: Vocabulary;
  companies: { companyId: string; name: string; ticker: string | null }[];
  metrics: MetricInfo[];
  hasShares: boolean;
  basis: "fiscal" | "calendar";
  status: Result | undefined;
  set: (patch: Partial<Layer>) => void;
  move: (by: -1 | 1) => void;
  remove: () => void;
}) {
  const metric = metrics.find((m) => m.name === layer.metric);
  const [name, setName] = useState(layer.name ?? "");
  const label = layer.name?.trim() || auto;
  const companyId = layer.kind === "metric" ? layer.companyId : layer.defaultCompany;
  const company = companies.find((c) => c.companyId === companyId);
  const companyOptions = companies.map((c) => (
    <option key={c.companyId} value={c.companyId}>
      {c.ticker ? `${c.ticker} · ${c.name}` : c.name}
    </option>
  ));
  // per share divides dollars by shares: only for USD metrics (formulas are checked by the API)
  const perShareOk = hasShares && (layer.kind === "formula" || !metric || metric.unit === "USD");
  const commitName = () => {
    const n = name.trim().slice(0, MAX_LAYER_NAME);
    if (n !== (layer.name ?? "")) set({ name: n || undefined });
  };

  return (
    <div className={layer.hidden ? "layer off" : "layer"} role="group" aria-label={`Layer ${index + 1}: ${label}`}>
      <div className="row">
        <svg className={`sw c${color}`} width="9" height="9" aria-hidden="true">
          <rect width="9" height="9" rx="2" fill="currentColor" />
        </svg>
        <b className="grow" title={company?.name}>
          {layer.kind === "formula" ? "Formula" : "Metric"}
          {company && <span className="faint"> · {company.ticker ?? company.name}</span>}
        </b>
        <button className="btn sm ghost icon" onClick={() => move(-1)} disabled={index === 0}
          aria-label={`Move ${label} up`} title="Move up">
          ↑
        </button>
        <button className="btn sm ghost icon" onClick={() => move(1)} disabled={index === count - 1}
          aria-label={`Move ${label} down`} title="Move down">
          ↓
        </button>
        <button className="btn sm ghost" onClick={() => set({ hidden: !layer.hidden })} aria-pressed={!!layer.hidden}
          aria-label={`${layer.hidden ? "Show" : "Hide"} ${label}`}>
          {layer.hidden ? "Show" : "Hide"}
        </button>
        <button className="btn sm ghost" onClick={remove} aria-label={`Remove ${label}`}>
          ✕
        </button>
      </div>
      <label className="row layer-company">
        <span className="small">Company</span>
        {layer.kind === "metric" ? (
          <select className="grow" aria-label="Company" value={layer.companyId ?? ""} onChange={(e) => set({ companyId: e.target.value })}>
            {!company && <option value={layer.companyId ?? ""}>{layer.companyId ?? "Choose a company"}</option>}
            {companyOptions}
          </select>
        ) : (
          <select className="grow" aria-label="Default company" value={layer.defaultCompany ?? ""}
            title="The company for metrics written without a ticker; TICKER.metric reads another company"
            onChange={(e) => set({ defaultCompany: e.target.value || undefined })}>
            <option value="">None (tickers only)</option>
            {companyOptions}
          </select>
        )}
      </label>
      {layer.kind === "metric" ? (
        <div className="row">
          <select className="grow" aria-label="Metric" value={layer.metric ?? ""} onChange={(e) => set({ metric: e.target.value })}>
            {!metric && <option value={layer.metric ?? ""}>{layer.metric ? metricLabel(layer.metric) : "Metric"}</option>}
            {metrics.map((m) => (
              <option key={m.name} value={m.name}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <FormulaInput token={token} layer={layer} basis={basis} vocab={vocab} set={set} />
      )}
      <div className="row">
        <input className="field grow" value={name} maxLength={MAX_LAYER_NAME} placeholder={auto}
          aria-label="Layer name (legend)" onChange={(e) => setName(e.target.value)} onBlur={commitName}
          onKeyDown={(e) => e.key === "Enter" && commitName()} />
        <select aria-label="Colour" value={color} onChange={(e) => set({ color: Number(e.target.value) })}>
          {COLOR_NAMES.map((c, i) => (
            <option key={c} value={i}>{c}</option>
          ))}
        </select>
      </div>
      <div className="row">
        <select aria-label="Transform" value={layer.transform} onChange={(e) => set({ transform: e.target.value as Layer["transform"] })}>
          {TRANSFORMS.filter(([v]) => v !== "ps" || hasShares || layer.transform === "ps").map(([v, label]) => (
            <option key={v} value={v}
              disabled={(v === "ttm" && metric !== undefined && !metric.ttm) || (v === "ps" && !perShareOk) || (v === "qoq" && annual)}>
              {annual && v === "qoq" ? `${label} (quarterly only)` : label}
            </option>
          ))}
        </select>
        <select aria-label="Type" value={layer.type} onChange={(e) => set({ type: e.target.value as Layer["type"] })}>
          {LAYER_TYPES.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <select aria-label="Axis" value={layer.axis} onChange={(e) => set({ axis: e.target.value as Layer["axis"] })}>
          <option value="L">Left axis</option>
          <option value="R">Right axis</option>
        </select>
      </div>
      {status?.state === "error" && <div className="err" role="alert">{status.message}</div>}
      {status?.state === "ok" && status.data.points.length === 0 && (
        <div className="faint small">No values in this range: try 10y or Max.</div>
      )}
    </div>
  );
}

const KIND_LABEL: Record<Suggestion["kind"], string> = {
  metric: "metric", ticker: "company", function: "function", operator: "operator", lag: "lag",
};

/** A formula with autocomplete (chart/autocomplete.ts: metrics, your companies' tickers,
 * functions, operators) and inline checks (/v1/formulas/validate) as you type; applied on
 * Enter or when the field loses focus. Tab takes the highlighted suggestion; ↑ ↓ move, and
 * Enter takes one once you've moved to it; Escape closes the list. */
function FormulaInput({
  token,
  layer,
  basis,
  vocab,
  set,
}: {
  token: string;
  layer: Layer;
  basis: "fiscal" | "calendar";
  vocab: Vocabulary;
  set: (patch: Partial<Layer>) => void;
}) {
  const [draft, setDraft] = useState(layer.formula ?? "");
  // the cursor while suggesting (null: list closed), the highlighted row, and whether the user
  // moved to it (only then does Enter take it instead of applying the formula)
  const [cursor, setCursor] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const [moved, setMoved] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<number | null>(null);
  const listId = useId();
  const comp = cursor === null ? null : complete(draft, cursor, vocab);
  const open = !!comp && comp.items.length > 0;

  useEffect(() => {
    if (pending.current !== null && input.current) {
      input.current.setSelectionRange(pending.current, pending.current);
      pending.current = null;
    }
  }, [draft]);

  const choose = (item: Suggestion) => {
    if (!comp) return;
    const r = applyCompletion(draft, comp, item);
    pending.current = r.cursor;
    setDraft(r.text);
    setCursor(r.cursor);
    setActive(0);
    setMoved(false);
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (open && comp) {
      const n = comp.items.length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => (a + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
        setMoved(true);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && moved)) {
        e.preventDefault();
        choose(comp.items[Math.min(active, n - 1)]!);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setCursor(null);
        return;
      }
    }
    if (e.key === "Enter") {
      setCursor(null);
      commit();
    }
  };
  const [check, setCheck] = useState<{ ok: true; unit: string } | { ok: false; problems: FormulaProblem[] } | null>(null);

  useEffect(() => {
    const formula = draft.trim();
    if (!formula) {
      setCheck(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      const body: FormulaRequest = { formula, basis, view: layer.transform === "q" || layer.transform === "qoq" ? "quarter" : "ttm" };
      if (layer.defaultCompany) body.default_company = layer.defaultCompany;
      validateFormula(token, body)
        .then((v) => live && setCheck(v.valid ? { ok: true, unit: v.unit } : { ok: false, problems: v.errors }))
        .catch((e) => live && setCheck(e instanceof FormulaApiError ? { ok: false, problems: e.problems } : null));
    }, 350);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [token, draft, basis, layer.transform, layer.defaultCompany]);

  const commit = () => {
    if (draft.trim() !== (layer.formula ?? "").trim()) set({ formula: draft.trim() });
  };

  return (
    <>
      <div className="ac">
        <input
          ref={input}
          className="field mono"
          value={draft}
          maxLength={500}
          placeholder="e.g. net_income / revenue or NVDA.revenue / MSFT.revenue"
          aria-label="Formula"
          aria-invalid={check?.ok === false}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open ? `${listId}-${active}` : undefined}
          onChange={(e) => {
            setDraft(e.target.value);
            setCursor(e.target.selectionStart ?? e.target.value.length);
            setActive(0);
            setMoved(false);
          }}
          onBlur={() => {
            setCursor(null);
            commit();
          }}
          onKeyDown={onKey}
        />
        {open && comp && (
          <ul className="ac-list" id={listId} role="listbox" aria-label="Formula suggestions">
            {comp.items.map((item, i) => (
              <li key={`${item.kind}-${item.label}`} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                className={i === active ? "on" : undefined}
                onMouseDown={(e) => {
                  e.preventDefault(); // keep focus in the field
                  choose(item);
                }}>
                <span className={`ac-k ${item.kind}`}>{KIND_LABEL[item.kind]}</span>
                <b className="mono">{item.label}</b>
                {item.detail && <span className="faint">{item.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      {check?.ok === false ? (
        check.problems.map((p, i) => (
          <div key={i} className="err">
            {p.message}
            {draft.length > 0 && p.pos < draft.length && (
              <span className="mono"> at “{draft.slice(p.pos, p.pos + p.length)}”</span>
            )}
            {p.hint && <span className="faint"> · {p.hint}</span>}
          </div>
        ))
      ) : (
        <div className="faint small">
          {check?.ok ? `Valid · ${check.unit}. ` : ""}
          Metrics by name read the company above; TICKER.metric reads another; ttm(), abs(), lags like revenue[-4q];
          + − × ÷ and ( ). Suggestions as you type: Tab completes.
        </div>
      )}
    </>
  );
}
