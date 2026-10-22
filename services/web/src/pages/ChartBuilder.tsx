import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  ApiError,
  evaluateFormula,
  FormulaApiError,
  screener,
  screenerColumns,
  validateFormula,
  type FormulaRequest,
} from "../api";
import { LayerChart } from "../chart/LayerChart";
import { catalog, metricLabel, type MetricInfo } from "../chart/metrics";
import {
  addLayer,
  applyTemplate,
  basis,
  layerName,
  MAX_LAYERS,
  RANGES,
  removeLayer,
  request,
  TEMPLATES,
  TRANSFORMS,
  updateLayer,
  type ChartState,
  type Layer,
} from "../chart/model";
import { pngBlob, slug, svgText } from "../chart/export";
import { chartCsv, latestSource, toSeries, type Series } from "../chart/series";
import { download } from "../download";
import { day } from "../format";
import type { FormulaProblem, FormulaSeries } from "../types";

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

/** Chart builder: layers of company × metric or formula over /v1/formulas/evaluate. */
export function ChartBuilder({ token, chart, setChart, saveBar }: Props) {
  const companies = useChartCompanies(token);
  const metrics = useMetrics(token);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [note, setNote] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  const byId = useMemo(() => new Map((companies ?? []).map((c) => [c.companyId, c])), [companies]);
  const short = (id: string) => byId.get(id)?.ticker ?? byId.get(id)?.name ?? id;
  const ttm = useMemo(() => {
    const m = new Map((metrics ?? []).map((x) => [x.name, x.ttm]));
    return (name: string) => m.get(name) ?? true;
  }, [metrics]);

  const visible = chart.layers.filter((l) => !l.hidden);
  const requests = visible.map((l) => ({ layer: l, req: request(l, chart, ttm) }));
  const keyOf = (req: FormulaRequest) => JSON.stringify(req);
  const wanted = requests.flatMap(({ req }) => (req ? [keyOf(req)] : []));
  const wantedKey = wanted.join("\n");

  useEffect(() => {
    let live = true;
    for (const k of new Set(wanted)) {
      if (results[k]) continue;
      setResults((r) => ({ ...r, [k]: { state: "loading" } }));
      evaluateFormula(token, JSON.parse(k) as FormulaRequest)
        .then((data) => live && setResults((r) => ({ ...r, [k]: { state: "ok", data } })))
        .catch(
          (e) =>
            live &&
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
    return () => {
      live = false;
    };
    // results is read only to skip requests already made
  }, [token, wantedKey]);

  const series: Series[] = requests.flatMap(({ layer, req }) => {
    const r = req ? results[keyOf(req)] : undefined;
    if (r?.state !== "ok") return [];
    const color = chart.layers.findIndex((l) => l.id === layer.id);
    return [toSeries(r.data, layer, layerName(layer, short, metricLabel), color)];
  });
  const status = (l: Layer): Result | undefined => {
    const req = request(l, chart, ttm);
    return req ? results[keyOf(req)] : undefined;
  };
  const loading = requests.some(({ req }) => req && results[keyOf(req)]?.state === "loading");

  const effective = basis(chart);
  const latest = latestSource(series);
  const footnote = [
    `Source: SEC filings (10-Q, 10-K XBRL) via Tributary. ${effective === "fiscal" ? "Fiscal" : "Calendar"} quarters; TTM = trailing four quarters; YoY over TTM.`,
    latest ? `Latest filing used: ${latest.form} filed ${latest.filed} (${latest.accessionNo}).` : "",
  ].filter(Boolean);

  const fallback = (companies ?? []).slice(0, 2).map((c) => c.companyId);
  const firstCompany = chart.layers.find((l) => l.companyId)?.companyId ?? fallback[0];
  const name = chart.title || "chart";

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
      <div className="row">
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
      {note && <p className="empty error">{note}</p>}

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
            {companies !== null && companies.length === 0 && (
              <p className="faint small">
                Your plan covers no company's financials, so there's nothing to chart yet.
              </p>
            )}
            {chart.layers.map((l, i) => (
              <LayerEditor
                key={l.id}
                token={token}
                layer={l}
                color={i}
                companies={companies ?? []}
                metrics={metrics ?? []}
                basis={effective}
                status={status(l)}
                set={(patch) => setChart((s) => updateLayer(s, l.id, patch))}
                remove={() => setChart((s) => removeLayer(s, l.id))}
              />
            ))}
            <div>
              <div className="faint small">Templates</div>
              <div className="row wrap">
                {TEMPLATES.map((t) => (
                  <button key={t.label} className="btn sm" disabled={!fallback.length && !chart.layers.length}
                    onClick={() => setChart((s) => applyTemplate(s, t, fallback))}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="card grow chart-card">
          <div className="card-h">
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
              {RANGES.map((r) => (
                <button key={r} className={chart.range === r ? "on" : undefined} aria-pressed={chart.range === r}
                  onClick={() => setChart((s) => ({ ...s, range: r }))}>
                  {r}y
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
            <span className="sub">{loading ? "Loading…" : "hover for values and source filings"}</span>
          </div>
          {chart.align === "fiscal" && effective === "calendar" && (
            <p className="note">
              Fiscal quarters only line up within one company, so this chart of several companies
              uses calendar quarters.
            </p>
          )}
          {series.length ? (
            <LayerChart series={series} title={chart.title} footnote={footnote} svgRef={svg} />
          ) : (
            <p className="empty">
              {visible.length
                ? loading
                  ? "Loading the series…"
                  : "No data for these layers in this range. Check the layers' messages."
                : "Add a metric or a formula layer, or start from a template."}
            </p>
          )}
          {latest && (
            <div className="foot">
              Every point comes from a filing: hover for each layer's value, period and accession.
              Latest: {latest.form} filed {day(latest.filed, true)}.
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
  color,
  companies,
  metrics,
  basis,
  status,
  set,
  remove,
}: {
  token: string;
  layer: Layer;
  color: number;
  companies: { companyId: string; name: string; ticker: string | null }[];
  metrics: MetricInfo[];
  basis: "fiscal" | "calendar";
  status: Result | undefined;
  set: (patch: Partial<Layer>) => void;
  remove: () => void;
}) {
  const metric = metrics.find((m) => m.name === layer.metric);
  const companyOptions = companies.map((c) => (
    <option key={c.companyId} value={c.companyId}>
      {c.ticker ? `${c.ticker} · ${c.name}` : c.name}
    </option>
  ));

  return (
    <div className={layer.hidden ? "layer off" : "layer"}>
      <div className="row">
        <svg className={`sw c${color % 8}`} width="9" height="9" aria-hidden="true">
          <rect width="9" height="9" rx="2" fill="currentColor" />
        </svg>
        <b className="grow">{layer.kind === "formula" ? "Formula" : "Metric"}</b>
        <button className="btn sm ghost" onClick={() => set({ hidden: !layer.hidden })}>
          {layer.hidden ? "Show" : "Hide"}
        </button>
        <button className="btn sm ghost" onClick={remove} aria-label="Remove layer">
          ✕
        </button>
      </div>
      {layer.kind === "metric" ? (
        <div className="row">
          <select aria-label="Company" value={layer.companyId ?? ""} onChange={(e) => set({ companyId: e.target.value })}>
            {!companies.some((c) => c.companyId === layer.companyId) && (
              <option value={layer.companyId ?? ""}>{layer.companyId ?? "Company"}</option>
            )}
            {companyOptions}
          </select>
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
        <FormulaInput token={token} layer={layer} basis={basis} companyOptions={companyOptions} set={set} />
      )}
      <div className="row">
        <select aria-label="Transform" value={layer.transform} onChange={(e) => set({ transform: e.target.value as Layer["transform"] })}>
          {TRANSFORMS.map(([v, label]) => (
            <option key={v} value={v} disabled={v === "ttm" && metric !== undefined && !metric.ttm}>
              {label}
            </option>
          ))}
        </select>
        <select aria-label="Type" value={layer.type} onChange={(e) => set({ type: e.target.value as Layer["type"] })}>
          <option value="line">line</option>
          <option value="bar">bar</option>
          <option value="area">area</option>
        </select>
        <select aria-label="Axis" value={layer.axis} onChange={(e) => set({ axis: e.target.value as Layer["axis"] })}>
          <option value="L">Left axis</option>
          <option value="R">Right axis</option>
        </select>
      </div>
      {status?.state === "error" && <div className="err">{status.message}</div>}
      {status?.state === "ok" && status.data.points.length === 0 && (
        <div className="faint small">No values in this range.</div>
      )}
    </div>
  );
}

/** A formula with inline checks (/v1/formulas/validate) as you type; applied on Enter or
 * when the field loses focus. */
function FormulaInput({
  token,
  layer,
  basis,
  companyOptions,
  set,
}: {
  token: string;
  layer: Layer;
  basis: "fiscal" | "calendar";
  companyOptions: ReactNode;
  set: (patch: Partial<Layer>) => void;
}) {
  const [draft, setDraft] = useState(layer.formula ?? "");
  const [check, setCheck] = useState<{ ok: true; unit: string } | { ok: false; problems: FormulaProblem[] } | null>(null);

  useEffect(() => {
    const formula = draft.trim();
    if (!formula) {
      setCheck(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      const body: FormulaRequest = { formula, basis, view: layer.transform === "q" ? "quarter" : "ttm" };
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
      <input
        className="field mono"
        value={draft}
        maxLength={500}
        placeholder="e.g. net_income / revenue or NVDA.revenue / MSFT.revenue"
        aria-label="Formula"
        aria-invalid={check?.ok === false}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      <div className="row">
        <span className="faint small">Default company</span>
        <select aria-label="Default company" className="grow" value={layer.defaultCompany ?? ""}
          onChange={(e) => set({ defaultCompany: e.target.value || undefined })}>
          <option value="">None</option>
          {companyOptions}
        </select>
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
          Metrics by name; TICKER.metric for another company; ttm(), abs(), lags like revenue[-4q];
          + − × ÷ and ( ).
        </div>
      )}
    </>
  );
}
