import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  filingIndex,
  filings as fetchFilings,
  failureReason,
  FormulaApiError,
  screener,
  validateFormula,
  type FormulaRequest,
} from "../api";
import { type Vocabulary } from "../chart/autocomplete";
import { FormulaCombo, useMetricCatalog } from "../chart/FormulaCombo";
import { cached, remember } from "../chart/cache";
import { fetchLayer } from "../chart/fetch";
import { memberName, SEGMENT_AXIS_LABELS, SEGMENT_TRANSFORMS } from "../chart/segments";
import { PRICE_TRANSFORMS } from "../chart/prices";
import { isConnected, useMarketData } from "../market/useMarketData";
import { autoSubtitle, methodNote, sourceFootnote } from "../chart/deck";
import { LayerChart } from "../chart/LayerChart";
import { askSides, drawnIds, planAxes } from "../chart/axes";
import { filingMarkers, MARKER_FORMS, markerKey as keyToMarkers, markersFor, type Marker } from "../chart/markers";
import { metricLabel, type MetricInfo } from "../chart/metrics";
import {
  addLayer,
  applyTemplate,
  autoName,
  autoTitle,
  basis,
  basisReason,
  chartEnd,
  chartTitle,
  COLOR_NAMES,
  DISPLAYS,
  formulaReads,
  frequencyOf,
  LAYER_TYPES,
  layerColor,
  layerKey,
  layerName,
  MAX_LAYER_NAME,
  MAX_LAYERS,
  moveLayer,
  RANGES,
  rangeDates,
  rangeLabel,
  readsBalances,
  removeLayer,
  SHARES_METRIC,
  TEMPLATES,
  TRANSFORMS,
  updateLayer,
  type ChartState,
  type Display,
  type Layer,
} from "../chart/model";
import { pngBlob, slug, svgText } from "../chart/export";
import { UNNAMED, useDirectory } from "../companies/directory";
import { chartCsv, latestSource, toSeries, type Series, type Source } from "../chart/series";
import type { Readable } from "../access";
import { CompanyPicker, CompanySearch } from "../components/CompanySearch";
import { MenuButton } from "../components/MenuButton";
import { ErrorPanel, Skeleton } from "../components/States";
import { dismissTopic, toast, useToastOn } from "../components/Toasts";
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
  /** `formula`: the formula itself is wrong, which its own input already says */
  | { state: "error"; message: string; formula?: boolean };

interface Props {
  token: string;
  chart: ChartState;
  setChart: (f: (s: ChartState) => ChartState) => void;
  /** Save / Save as (workspaces/SaveBar) */
  saveBar?: ReactNode;
  /** the companies whose financials the user can read (access.ts readableFor); default: what
   * the API's access=financials answers */
  readable?: Readable;
  /** the user's own companies (the watchlist): what a template starts from on an empty chart */
  preferred?: string[];
  /** "New chart": empties the chart, keeping the draft it replaces for `onRestore` */
  onNew?: () => void;
  /** set while there is a replaced draft to bring back */
  onRestore?: (() => void) | null;
  /** says what replaced the draft (shown beside the Restore button) */
  restoreNote?: string | null;
  /** about to replace the layers: keep the draft for `onRestore` (`why` says what happened) */
  onReplace?: (why: string) => void;
  /** something the page that opened this chart needs to say about it */
  notice?: string | null;
}

const ANYTHING_THE_API_ALLOWS: Readable = { has: () => true };
const NO_PREFERENCE: string[] = [];

/** Screened once, for the two things that aren't a search: whether the plan can chart anything,
 * and which companies start a template on an empty chart (the user's own first). Everything else
 * asks the server what was typed (CompanySearch): no list of ~6,000 names in the browser. */
function useChartSeed(token: string, preferred: string[]): { none: boolean; ids: string[] } | null {
  const [rows, setRows] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    screener(token, { access: "financials", columns: ["revenue_ttm"], sort: "name", limit: 2 })
      .then((r) => live && setRows(r.rows.map((x) => x.company_id)))
      .catch(() => live && setRows([]));
    return () => {
      live = false;
    };
  }, [token]);
  if (rows === null) return null;
  return { none: rows.length === 0, ids: [...new Set([...preferred, ...rows])].slice(0, 2) };
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
export function ChartBuilder({ token, chart, setChart, saveBar, readable = ANYTHING_THE_API_ALLOWS, preferred = NO_PREFERENCE, onNew, onRestore, restoreNote, onReplace, notice }: Props) {
  const seed = useChartSeed(token, preferred);
  const onChart = [...new Set(chart.layers.flatMap((l) => {
    const id = l.kind === "formula" ? l.defaultCompany : l.companyId;
    return id ? [id] : [];
  }))];
  // name and ticker of the companies on the chart: from the pick itself when just chosen, else asked
  const directory = useDirectory(token);
  const known = useMemo(
    () => new Map(onChart.flatMap((id): [string, ChartCompany][] => {
      const c = directory.company(id);
      return c ? [[id, c]] : [];
    })),
    [directory, onChart.join(","), directory.snapshot()],
  );
  const learn = useCallback((c: ChartCompany) => directory.learn([c]), [directory]);
  const metrics = useMetricCatalog(token);
  // "+ Price" only with the user's own market-data connection (chart/prices.ts)
  const market = useMarketData(token);
  const priced = isConnected(market);
  const [results, setResults] = useState<Record<string, Result>>({});
  // a problem with an action (PNG, opening a source) and what the opener says: toasts, never banners that stay
  const setNote = (text: string | null) => text && toast({ text, error: true, topic: "chart-note" });
  useToastOn(notice, { topic: "chart-notice" });
  // a replaced draft: said once as a toast with Restore, and kept in the bar while it can be restored
  const restoreRef = useRef(onRestore);
  restoreRef.current = onRestore;
  useEffect(() => {
    if (onRestore) toast({ text: restoreNote ?? "Started a new chart.", topic: "chart-draft", action: { label: "Restore the previous draft", onClick: () => restoreRef.current?.() } });
    else dismissTopic("chart-draft");
  }, [!!onRestore, restoreNote]);
  const [retry, setRetry] = useState(0);
  const svg = useRef<SVGSVGElement>(null);

  const short = directory.short;
  const ttm = useMemo(() => {
    const m = new Map((metrics ?? []).map((x) => [x.name, x.ttm]));
    return (name: string) => m.get(name) ?? true;
  }, [metrics]);
  // per share needs a diluted share count in the catalog; while it loads, assume it's there
  const hasShares = metrics === null || metrics.some((m) => m.name === SHARES_METRIC);

  const frequency = frequencyOf(chart);
  // a formula whose text doesn't check right now: off the chart, its title and legend until it does, so
  // the chart never shows the previous formula under the new one's error (Riley 16)
  const [broken, setBroken] = useState<ReadonlySet<number>>(new Set());
  const markBroken = useCallback((id: number, is: boolean) => setBroken((b) => {
    if (b.has(id) === is) return b;
    const next = new Set(b);
    if (is) next.add(id);
    else next.delete(id);
    return next;
  }), []);
  const visible = chart.layers.filter((l) => !l.hidden && !broken.has(l.id));
  // each layer's request as its cache key (chart/model.ts layerKey: evaluate or segments)
  const requests = visible.map((l) => ({ layer: l, req: layerKey(l, chart, ttm) }));
  const keyOf = (req: string) => req;
  // answers already fetched this session (chart/cache.ts) show at once, e.g. back from another page
  const resultOf = (k: string): Result | undefined => {
    const hit = cached(token, k);
    return results[k] ?? (hit ? { state: "ok", data: hit } : undefined);
  };
  // whether a layer's answer draws anything; undefined while there is no answer. One that doesn't is
  // asked once over everything on file, so the advice under it is true: "try Max" only if Max has some
  const draws = (l: Layer, k: string | null): boolean | undefined => {
    const r = k ? resultOf(keyOf(k)) : undefined;
    return r?.state === "ok" ? toSeries(r.data, l, "", 0, frequency).points.some((p) => p.y !== null) : undefined;
  };
  const onFile: ChartState = { ...chart, range: "max", start: undefined, end: undefined };
  const probes = chart.range === "max" ? [] : requests.flatMap(({ layer, req }) => {
    const whole = draws(layer, req) === false ? layerKey(layer, onFile, ttm) : null;
    return whole ? [keyOf(whole)] : [];
  });
  const wanted = [...requests.flatMap(({ req }) => (req ? [keyOf(req)] : [])), ...probes];
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
      fetchLayer(token, k)
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
                    : failureReason(e),
                formula: e instanceof FormulaApiError,
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
    const req = layerKey(l, chart, ttm);
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
  // price layers cite no filing: name where the viewer's own prices come from
  const priceNote = visible.some((l) => l.kind === "price") && market?.enabled
    ? `Share prices (calendar quarter-end closes): ${market.providers
        .filter((p) => market.connections.some((c) => c.provider === p.id))
        .map((p) => p.attribution).join(" ") || "your market-data connection."}`
    : "";
  // which company's marks are whose, once there are several (the companies the markers below read)
  const markerKey = chart.markers ? keyToMarkers(frequency, [...new Set(visible.flatMap((l) => (l.kind === "formula" ? [] : l.companyId ? [l.companyId] : [])))].map(short)) : "";
  const footnote = chart.deck
    ? [sourceFootnote(series, short, asOf), method, priceNote, markerKey].filter(Boolean)
    : [
        `Source: SEC filings (10-Q, 10-K XBRL) via Tributary. ${method}`,
        latest ? `Latest filing used: ${latest.companyId ? `${short(latest.companyId)} ` : ""}${latest.form} filed ${day(latest.filed, true)}.` : "",
        priceNote,
        markerKey,
      ].filter(Boolean);
  // the time axis runs to the chart's end, so a layer that stopped shows its gap (chart/series.ts xAxis)
  const through = chartEnd(chart, asOf);
  const subtitle = chart.deck ? chart.subtitle?.trim() || autoSubtitle(series, frequency, effective, through) : "";

  // filing markers: each company a visible layer reads. A formula's are the companies its answer read
  // (TICKER.metric included: "MSFT.capex / MSFT.revenue" with another default company drew none,
  // analyst test 4, finding 14), or its default company until it has an answer
  const markedBy = (l: Layer): string[] => {
    if (l.kind !== "formula") return l.companyId ? [l.companyId] : [];
    const r = status(l);
    if (r?.state === "ok") return [...new Set(r.data.inputs.flatMap((i) => (i.company_id ? [i.company_id] : [])))];
    return formulaReads(l.formula ?? "").bare && l.defaultCompany ? [l.defaultCompany] : [];
  };
  const chartCompanies = [...new Set(visible.flatMap(markedBy))].slice(0, 8);
  const { from } = rangeDates(chart);
  const filingsBy = useFilings(token, chartCompanies, from, !!chart.markers);
  const markers: Marker[] = chart.markers
    ? markersFor(chartCompanies.flatMap((c) => {
        const f = filingsBy[c];
        const layer = visible.find((l) => markedBy(l).includes(c));
        return f ? filingMarkers(c, short(c), layer ? colorOf(layer) : 0, f.index, f.parsed, from) : [];
      }), frequency)
    : [];

  const vocab = useMemo((): Vocabulary => ({
    metrics: metrics ?? [],
    tickers: [...known.values()].flatMap((c) => (c.ticker ? [{ ticker: c.ticker, name: c.name }] : [])),
  }), [metrics, known]);
  const fallback = seed?.ids ?? [];
  const firstCompany = chart.layers.find((l) => l.companyId)?.companyId ?? fallback[0];
  // named for what draws, once that's known: a layer with nothing to show isn't in the title
  const drawn = drawnIds(series);
  const answered = new Set(series.map((x) => x.id));
  const shows = (l: Layer) => !broken.has(l.id) && (answered.has(l.id) ? drawn.has(l.id) : status(l)?.state !== "error");
  const title = chartTitle(chart, short, metricLabel, shows);
  const name = title || "chart";
  const whyCalendar = basisReason(chart);
  // which axis each plotted layer ended up on (chart/axes.ts: one unit per axis)
  const plan = planAxes(series);
  const placedOn = (l: Layer) => {
    const i = series.findIndex((s) => s.id === l.id);
    return i < 0 || !series[i]!.points.some((p) => p.y !== null) ? undefined : plan.axes[i];
  };
  // the Axis control shows the side a layer is drawn on; choosing one moves the layer's unit there
  const sideCaption = (side: "L" | "R") => (side === "L" ? plan.left : plan.right);
  const sideNote = (l: Layer): string | null => {
    const on = placedOn(l);
    if (!on || on === l.axis || l.hidden) return null;
    const word = (side: "L" | "R") => (side === "R" ? "right" : "left");
    const asked = sideCaption(l.axis);
    return asked
      ? `On the ${word(on)} axis: the ${word(l.axis)} one reads in ${asked}.`
      : `On the ${word(on)} axis with the other layers in ${sideCaption(on) || "plain numbers"}: one unit, one axis.`;
  };
  const putOnSide = (l: Layer, side: "L" | "R") => {
    const asks = askSides(series, l.id, side);
    setChart((s) => ({ ...s, layers: s.layers.map((x) => (asks.has(x.id) ? { ...x, axis: asks.get(x.id)! } : x)) }));
  };
  // a layer with an answer but nothing to draw: what would change that, and only that
  const what = frequency === "annual" ? "No full year" : "No values";
  const onFileNote = frequency === "annual" ? "No full year on file: try Quarterly." : "No values on file.";
  const nothingNote = (l: Layer): string | null => {
    if (draws(l, layerKey(l, chart, ttm)) !== false) return null;
    if (chart.range === "max") return onFileNote;
    const whole = layerKey(l, onFile, ttm);
    const ever = draws(l, whole);
    if (ever === undefined) return `${what} in this range.`;
    if (!ever) return onFileNote;
    const r = resultOf(keyOf(whole!));
    const latest = r?.state === "ok" ? toSeries(r.data, l, "", 0, frequency).points.filter((p) => p.y !== null).at(-1)?.period : null;
    return `${what} in this range${latest ? `; the latest is ${latest}` : ""}: try Max.`;
  };
  // the chart's footnote says what the layer cards say: nothing on file isn't "in this range"
  const emptyOnFile = frequency === "annual" ? [] : visible.filter((l) => nothingNote(l) === onFileNote).map((l) => l.id);
  const emptyNote = (() => {
    const notes = visible.map(nothingNote).filter((n): n is string => n !== null);
    if (notes.some((n) => n.endsWith("try Max."))) return `${what} for these layers in this range: try Max.`;
    return notes.length && notes.every((n) => n === onFileNote) ? `${what} on file for these layers.` : `${what} for these layers in this range.`;
  })();

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
      <div className="row page-bar chart-bar">
        <h1>Chart builder</h1>
        {seed !== null && !seed.none && (
          <div className="chart-co">
            <CompanySearch token={token} label="Chart a company" readable={readable}
              placeholder={chart.layers.length >= MAX_LAYERS ? "Layer limit reached" : "Chart a company, e.g. AMZN"}
              disabled={chart.layers.length >= MAX_LAYERS}
              onPick={(id, name, ticker) => {
                learn({ companyId: id, name, ticker });
                setChart((s) => addLayer(s, { kind: "metric", companyId: id, metric: "revenue", transform: "ttm", type: "line", axis: "L" }));
              }} />
          </div>
        )}
        <span className="grow" />
        {onNew && (
          <button className="btn" onClick={onNew} disabled={!chart.layers.length && !chart.title}
            title="Start an empty chart. Your draft can be restored until you start another">
            New chart
          </button>
        )}
        {onRestore && (
          <button className="btn ghost" onClick={onRestore} title={restoreNote ?? "Bring back the chart this one replaced"}>
            Restore the previous draft
          </button>
        )}
        <MenuButton label="Export" disabled={!series.length} items={[
          { label: "CSV, exact values", onSelect: exportCsv, disabled: !series.length, title: "Exact values, with the filing behind each point" },
          { label: "SVG, for slides", onSelect: exportSvg, disabled: !series.length },
          { label: "PNG, for slides", onSelect: exportPng, disabled: !series.length },
        ]} />
        {saveBar}
      </div>

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
            {priced && (
              <button
                className="btn sm"
                disabled={!firstCompany || chart.layers.length >= MAX_LAYERS}
                title="Your own end-of-day prices (calendar quarter-end closes)"
                onClick={() =>
                  setChart((s) =>
                    addLayer(s, { kind: "price", companyId: firstCompany, transform: "q", type: "line", axis: "R" }),
                  )
                }
              >
                + Price
              </button>
            )}
          </div>
          <div className="card-b layer-list">
            {seed === null && <Skeleton lines={3} label="Loading your companies" />}
            {seed !== null && seed.none && (
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
                company={known.get((l.kind === "formula" ? l.defaultCompany : l.companyId) ?? "") ?? null}
                readable={readable}
                learn={learn}
                metrics={metrics ?? []}
                hasShares={hasShares}
                basis={effective}
                status={status(l)}
                placedOn={placedOn(l)}
                sides={{ L: plan.left, R: plan.right }}
                sideNote={sideNote(l)}
                putOnSide={(side) => putOnSide(l, side)}
                nothingNote={nothingNote(l)}
                onBroken={(is) => markBroken(l.id, is)}
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
            {/* one menu, not seven buttons (Riley 60, 101); replacing layers says so with Restore (a toast) */}
            <div className="templates">
              <MenuButton label={chart.layers.length ? "Replace with a template" : "Start from a template"} align="start"
                disabled={!fallback.length && !chart.layers.length}
                items={TEMPLATES.map((t) => ({
                  label: t.label,
                  title: t.hint + (t.per === "one" ? " (the chart's first company)" : "") + (chart.layers.length ? ". Replaces the layers; you can restore them" : ""),
                  onSelect: () => {
                    if (chart.layers.length || chart.title) onReplace?.(`Replaced your layers with the ${t.label} template.`);
                    setChart((s) => applyTemplate(s, t, fallback));
                  },
                }))} />
            </div>
          </div>
        </section>

        <section className="card grow chart-card">
          <div className="card-h wrap">
            <input
              className="field title-field"
              value={chart.title}
              maxLength={200}
              placeholder={autoTitle(chart, short, metricLabel, shows) || "Chart title"}
              title={chart.title.trim() ? "Your own title: it stays whatever the layers show" : "Written from the layers until you type your own"}
              aria-label="Chart title"
              onChange={(e) => {
                const title = e.target.value;
                setChart((s) => ({ ...s, title }));
              }}
            />
            {chart.title.trim() && (
              <span className="sub title-mode">
                Custom title{" "}
                <button className="btn sm ghost" onClick={() => setChart((s) => ({ ...s, title: "" }))}>Use automatic</button>
              </span>
            )}
            <span className="seg" role="group" aria-label="Range">
              {[...RANGES, "custom" as const].map((r) => (
                <button key={r} className={chart.range === r ? "on" : undefined} aria-pressed={chart.range === r}
                  title={r === "max" ? "Every quarter on file" : r === "custom" ? "Choose the first and last period end" : `The last ${r} year${r === 1 ? "" : "s"}`}
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
                <button key={a} className={effective === a ? "on" : undefined} aria-pressed={effective === a}
                  disabled={a === "fiscal" && whyCalendar !== null}
                  title={a === "fiscal" && whyCalendar ? whyCalendar : a === "fiscal" ? "Each company's own fiscal quarters" : "Calendar quarters"}
                  onClick={() => setChart((s) => ({ ...s, align: a }))}>
                  {a === "calendar" ? "Calendar" : "Fiscal"}
                </button>
              ))}
            </span>
            <MenuButton label="Show" title="Filing markers on the time axis, and the deck look for slides" items={[
              { label: "Filing markers", checked: !!chart.markers, title: "10-K (K), 10-Q (Q) and 8-K results (8, item 2.02, where parsed) on the time axis",
                onCheckedChange: (markers) => setChart((s) => ({ ...s, markers: markers || undefined })) },
              { label: "Deck output", checked: !!chart.deck, title: "For slides: light background, a subtitle with periods and units, and a source footnote naming forms, companies and the as-of date; Export SVG / PNG keep it",
                onCheckedChange: (deck) => setChart((s) => ({ ...s, deck: deck || undefined })) },
            ]} />
            {loading && <span className="sub" role="status">Loading…</span>}
          </div>
          {chart.deck && (
            <div className="card-h deck-h">
              <input className="field grow" value={chart.subtitle ?? ""} maxLength={200}
                placeholder={series.length ? autoSubtitle(series, frequency, effective, through) : "Subtitle (automatic: periods and units)"}
                aria-label="Subtitle"
                onChange={(e) => {
                  const subtitle = e.target.value;
                  setChart((s) => ({ ...s, subtitle: subtitle || undefined }));
                }} />
              <span className="sub">Deck output: a light background, and the source footnote is added for you</span>
            </div>
          )}
          {whyCalendar && <p className="chart-why">{whyCalendar}</p>}
          {series.some((s) => s.points.some((p) => p.y !== null)) ? (
            <LayerChart series={series} title={title} footnote={footnote} svgRef={svg}
              markers={markers} onOpen={openSource} subtitle={subtitle} deck={!!chart.deck} from={from} through={through} emptyOnFile={emptyOnFile} />
          ) : visible.length && loading ? (
            <Skeleton chart label="Loading the series" />
          ) : visible.length && failed ? (
            <ErrorPanel what="these layers" detail="Each layer below says what went wrong."
              onRetry={() => setRetry((n) => n + 1)} />
          ) : (
            <p className="empty">
              {visible.length
                ? requests.every(({ req }) => !req)
                  ? "Type a formula in the layer to plot it. It draws as soon as it's valid."
                  : emptyNote
                : chart.layers.length
                  ? "Every layer is hidden. Show one, or add a metric or formula layer."
                  : "Start with + Metric (a company and a metric), + Formula (e.g. net_income / revenue), or one of the templates."}
            </p>
          )}
          {latest && <div className="foot">Point at a value, or tap it, for its period and filing; click it to open the filing in the reader.</div>}
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
  company,
  readable,
  learn,
  metrics,
  hasShares,
  basis,
  status,
  placedOn,
  sides,
  sideNote,
  putOnSide,
  nothingNote,
  onBroken,
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
  /** name and ticker of the layer's company, once known */
  company: ChartCompany | null;
  readable: Readable;
  learn: (c: ChartCompany) => void;
  metrics: MetricInfo[];
  hasShares: boolean;
  basis: "fiscal" | "calendar";
  status: Result | undefined;
  /** where it drew (chart/axes.ts planAxes), null when neither axis could take it, undefined with
   * nothing to draw */
  placedOn: "L" | "R" | null | undefined;
  /** each side's unit caption as drawn, null when nothing draws there */
  sides: { L: string | null; R: string | null };
  /** why it isn't on the side it asked for, when it isn't */
  sideNote: string | null;
  /** the Axis control: puts this layer's unit on that side (chart/axes.ts askSides) */
  putOnSide: (side: "L" | "R") => void;
  /** it has an answer with nothing to draw: what to do about it */
  nothingNote: string | null;
  /** a formula's text doesn't check: the chart leaves the layer out until it does */
  onBroken: (broken: boolean) => void;
  set: (patch: Partial<Layer>) => void;
  move: (by: -1 | 1) => void;
  remove: () => void;
}) {
  const metric = metrics.find((m) => m.name === layer.metric);
  const [name, setName] = useState(layer.name ?? "");
  const label = layer.name?.trim() || auto;
  const companyId = layer.kind === "formula" ? layer.defaultCompany : layer.companyId;
  const segment = layer.kind === "segment";
  const price = layer.kind === "price";
  // per share divides dollars by shares: only for USD metrics (formulas are checked by the API)
  const perShareOk = hasShares && (layer.kind === "formula" || !metric || metric.unit === "USD");
  const commitName = () => {
    const n = name.trim().slice(0, MAX_LAYER_NAME);
    if (n !== (layer.name ?? "")) set({ name: n || undefined });
  };
  // who a layer reads: for a formula what its text names, not the company in the box
  const reads = layer.kind === "formula" ? formulaReads(layer.formula ?? "") : null;
  const who = reads
    ? [...(reads.bare && company ? [company.ticker ?? company.name] : []), ...reads.tickers].join(", ")
    : company ? company.ticker ?? company.name : "";
  // annual: a flow's TTM at Q4 and its "quarterly" are the same year total, so one choice, "Annual"
  const options = (segment ? SEGMENT_TRANSFORMS : price ? PRICE_TRANSFORMS : TRANSFORMS.filter(([v]) => v !== "ps" || hasShares || layer.transform === "ps"))
    .filter(([v]) => segment || price || !annual || v !== (layer.transform === "q" ? "ttm" : "q"));
  const optionLabel = (v: string, label: string) => {
    if (!annual || segment || price) return label;
    if (v === "q" || v === "ttm") return "Annual";
    if (v === "qoq") return layer.transform === "qoq" ? "YoY % (QoQ needs quarters)" : `${label} (quarterly only)`;
    return label;
  };
  // a balance has no TTM: the control shows what is drawn, its quarter-end value (chart/model.ts readsBalances)
  const balanceTtm = !segment && !price && !annual && layer.transform === "ttm" && readsBalances(layer);
  // only a dimensionless value has a choice: a change, an index or per share reads as that
  const dimensionless = status?.state === "ok" && status.data.unit === "ratio" && (layer.transform === "q" || layer.transform === "ttm");
  const asked = status?.state === "ok" ? status.data.display : null;

  return (
    <div className={layer.hidden ? "layer off" : "layer"} role="group" aria-label={`Layer ${index + 1}: ${label}`}>
      <div className="row">
        <svg className={`sw c${color}`} width="9" height="9" aria-hidden="true">
          <rect width="9" height="9" rx="2" fill="currentColor" />
        </svg>
        <b className="grow" title={company?.name}>
          {layer.kind === "formula" ? "Formula" : segment ? "Segments" : price ? "Price" : "Metric"}
          {who && <span className="faint"> {who}</span>}
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
      <div className="row layer-company">
        <span className="small">{layer.kind === "formula" ? "Default" : "Company"}</span>
        {segment ? (
          <span className="grow">{company ? (company.ticker ?? company.name) : UNNAMED}</span>
        ) : layer.kind === "metric" || price ? (
          <CompanyPicker token={token} label="Company" value={company ?? (companyId ? { companyId, name: UNNAMED } : null)}
            emptyLabel="Choose a company" readable={readable}
            onChange={(c) => {
              learn({ companyId: c.companyId, name: c.name, ticker: c.ticker ?? null });
              set({ companyId: c.companyId });
            }} />
        ) : (
          <CompanyPicker token={token} label="Default company" value={company ?? (companyId ? { companyId, name: UNNAMED } : null)}
            emptyLabel="None (tickers only)" readable={readable}
            title="The company for metrics written without a ticker; TICKER.metric reads another company"
            onChange={(c) => {
              learn({ companyId: c.companyId, name: c.name, ticker: c.ticker ?? null });
              set({ defaultCompany: c.companyId });
            }}
            onClear={() => set({ defaultCompany: undefined })} />
        )}
      </div>
      {segment ? (
        <p className="small muted segment-members" title={(layer.members ?? []).join(", ")}>
          {metricLabel(layer.metric ?? "revenue")} by {SEGMENT_AXIS_LABELS[layer.segmentAxis!] ?? "segment"}:{" "}
          {(layer.members ?? []).length === 1 ? memberName(layer.members![0]!) : `${layer.members?.length ?? 0} members summed`}
          <span className="faint"> (from the company's Revenue tab)</span>
        </p>
      ) : price ? (
        <p className="small muted">Share price from your own market-data connection, on calendar quarters.</p>
      ) : layer.kind === "metric" ? (
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
        <FormulaInput token={token} layer={layer} basis={basis} vocab={vocab} set={set} onBroken={onBroken} />
      )}
      <div className="row">
        <input className="field grow" value={name} maxLength={MAX_LAYER_NAME} placeholder={auto}
          aria-label="Layer name (legend)" onChange={(e) => setName(e.target.value)} onBlur={commitName}
          onKeyDown={(e) => e.key === "Enter" && commitName()} />
      </div>
      <Swatches color={color} label={label} onPick={(i) => set({ color: i })} />
      <div className="row layer-draw">
        <select aria-label="Transform" value={balanceTtm ? "q" : layer.transform} onChange={(e) => set({ transform: e.target.value as Layer["transform"] })}>
          {options.map(([v, label]) => (
            <option key={v} value={v}
              disabled={!segment && !price && ((v === "ttm" && metric !== undefined && !metric.ttm && !annual) || (v === "ps" && !perShareOk) || (v === "qoq" && annual && layer.transform !== "qoq"))}>
              {optionLabel(v, label)}
            </option>
          ))}
        </select>
        <select aria-label="Type" value={layer.type} onChange={(e) => set({ type: e.target.value as Layer["type"] })}>
          {LAYER_TYPES.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        {/* two words, not a select cut to "Left axis (U" (Riley 102): the unit is each side's hover */}
        <span className="seg axis-seg" role="group" aria-label="Axis">
          {(["L", "R"] as const).map((side) => {
            const named = `${side === "L" ? "Left" : "Right"} axis${sides[side] ? ` (${sides[side]})` : ""}`;
            return (
              <button key={side} type="button" aria-pressed={(placedOn ?? layer.axis) === side} aria-label={named} title={named}
                onClick={() => putOnSide(side)}>
                {side === "L" ? "Left" : "Right"}
              </button>
            );
          })}
        </span>
      </div>
      {dimensionless && (
        <div className="row">
          <label className="small layer-reads">
            Reads as{" "}
            <select aria-label="Reads as" value={layer.display ?? "auto"}
              onChange={(e) => set({ display: e.target.value === "auto" ? undefined : (e.target.value as Display) })}>
              <option value="auto">Automatic ({DISPLAYS.find(([d]) => d === (asked ?? "percent"))![1].toLowerCase()})</option>
              {DISPLAYS.map(([d, label]) => <option key={d} value={d}>{label}</option>)}
            </select>
          </label>
        </div>
      )}
      {status?.state === "error" && !(layer.kind === "formula" && status.formula) && <div className="err" role="alert">{status.message}</div>}
      {nothingNote && <div className="faint small">{nothingNote}</div>}
      {placedOn === null && !layer.hidden && (
        <div className="err">Not drawn: both axes already read in other units. Hide a layer, or give this one its own chart.</div>
      )}
      {sideNote && <div className="faint small">{sideNote}</div>}
    </div>
  );
}

/** A layer's colour as the colours themselves (Riley 15: a select of names drew another colour than it
 * said). Each swatch is the series class the line draws with (`.c0`..`.c7`), so the control can't
 * disagree with the chart; a radio group, arrow keys between swatches. */
function Swatches({ color, label, onPick }: { color: number; label: string; onPick: (i: number) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const step = (by: number) => {
    const next = (color + by + COLOR_NAMES.length) % COLOR_NAMES.length;
    onPick(next);
    refs.current[next]?.focus();
  };
  return (
    <div className="swatches" role="radiogroup" aria-label={`Colour of ${label}`}>
      {COLOR_NAMES.map((c, i) => (
        <button key={c} ref={(el) => {
          refs.current[i] = el;
        }} type="button" role="radio" aria-checked={color === i} aria-label={c} title={c}
          tabIndex={color === i ? 0 : -1} className={`swatch c${i}`} onClick={() => onPick(i)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowDown") {
              e.preventDefault();
              step(1);
            } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
              e.preventDefault();
              step(-1);
            }
          }} />
      ))}
    </div>
  );
}

/** "Valid · ...": what the chart will read it as, in a person's words. */
function readsAs(unit: string, display: FormulaSeries["display"]): string {
  if (unit === "ratio") return display === "multiple" ? "a multiple, like 1.50x" : display === "number" ? "a plain number" : "a percent";
  return ({ USD: "dollars", shares: "shares", "USD/shares": "dollars per share" } as Record<string, string>)[unit] ?? unit;
}

/** A formula with autocomplete (chart/autocomplete.ts: metrics, your companies' tickers,
 * functions) and inline checks (/v1/formulas/validate) as you type; the chart takes it as soon
 * as it's valid (the check's own 350 ms is the debounce), or on Enter or blur whatever it is, when
 * the layer shows what's wrong. Tab takes the highlighted suggestion; ↑ ↓ move, and Enter takes
 * one once you've moved to it; Escape closes the list. */
function FormulaInput({
  token,
  layer,
  basis,
  vocab,
  set,
  onBroken,
}: {
  token: string;
  layer: Layer;
  basis: "fiscal" | "calendar";
  vocab: Vocabulary;
  set: (patch: Partial<Layer>) => void;
  onBroken?: (broken: boolean) => void;
}) {
  const [draft, setDraft] = useState(layer.formula ?? "");
  const [check, setCheck] = useState<{ ok: true; unit: string; display: FormulaSeries["display"] } | { ok: false; problems: FormulaProblem[] } | null>(null);
  const [focused, setFocused] = useState(false);
  const isBroken = check?.ok === false;
  useEffect(() => {
    onBroken?.(isBroken);
  }, [isBroken]);
  useEffect(() => () => onBroken?.(false), []);

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
        .then((v) => {
          if (!live) return;
          setCheck(v.valid ? { ok: true, unit: v.unit, display: v.display } : { ok: false, problems: v.errors });
          if (v.valid && formula !== (layer.formula ?? "").trim()) set({ formula });
        })
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
      <FormulaCombo token={token} value={draft} onChange={setDraft} vocab={vocab} invalid={check?.ok === false}
        placeholder="e.g. net_income / revenue or NVDA.revenue / MSFT.revenue" onCommit={commit} onEnter={commit}
        onFocusChange={setFocused} />
      {check?.ok === false
        ? check.problems.map((p, i) => {
            const at = draft.slice(p.pos, p.pos + p.length);
            return (
              <div key={i} className="err">
                {p.message}
                {at && !p.message.includes(at) && <span className="mono"> at “{at}”</span>}
                {p.hint && <span className="faint">. {p.hint}</span>}
              </div>
            );
          })
        : check?.ok && <div className="faint small">Valid: reads as {readsAs(check.unit, check.display)}.</div>}
      {check?.ok === false && <div className="faint small">Not on the chart until the formula checks.</div>}
      {check?.ok !== false && (focused || !draft.trim()) && (
        <div className="faint small">
          Metrics by name read the company above; TICKER.metric reads another; ttm(), abs(), lags like revenue[-4q];
          + − × ÷ and ( ). Tab takes a suggestion.
        </div>
      )}
    </>
  );
}
