/** The assistant's UI actions (agent-api `action` events) applied to client state, and the
 * screen context sent with each question. Pure: the hook that applies them with Undo is
 * useUiActions. agent-api has already checked every company and metric as this user; the
 * chart then reads data through the Query API like any other layer, so nothing here can
 * widen access. */

import { clean, type ScreenFilters } from "../companies/screen";
import { cleanCompare, columnsOf, type CompareState } from "../compare/state";
import { addLayer, MAX_LAYERS, type ChartState, type Layer } from "../chart/model";
import type { Route } from "../route";
import type { UiAction, UiContext, UiLayer } from "../types";

type ChartAction = Extract<
  UiAction,
  { action: "set_chart_layers" | "add_chart_layer" | "remove_chart_layer" | "set_chart_options" }
>;

const CHART_ACTIONS = new Set(
  ["set_chart_layers", "add_chart_layer", "remove_chart_layer", "set_chart_options"],
);

export function isChartAction(a: UiAction): a is ChartAction {
  return CHART_ACTIONS.has(a.action);
}

function toLayer(l: UiLayer): Omit<Layer, "id"> {
  const style = { transform: l.transform, type: l.type, axis: l.axis };
  return l.kind === "formula"
    ? { kind: "formula", formula: l.formula ?? "", ...(l.company_id ? { defaultCompany: l.company_id } : {}), ...style }
    : { kind: "metric", companyId: l.company_id ?? undefined, metric: l.metric, ...style };
}

export function applyChartAction(s: ChartState, a: ChartAction): ChartState {
  switch (a.action) {
    case "set_chart_layers": {
      const empty: ChartState = { ...s, layers: [], title: a.title || a.label.replace(/^Chart: /, "") };
      return a.layers.slice(0, MAX_LAYERS).reduce<ChartState>((acc, l) => addLayer(acc, toLayer(l)), empty);
    }
    case "add_chart_layer":
      return addLayer(s, toLayer(a.layer));
    case "remove_chart_layer":
      return {
        ...s,
        layers: s.layers.filter(
          (l) =>
            !(
              (a.company_id === null || l.companyId === a.company_id || l.defaultCompany === a.company_id) &&
              (a.metric === null || l.metric === a.metric)
            ),
        ),
      };
    case "set_chart_options": {
      const o = a.options;
      return {
        ...s,
        ...(o.range ? { range: o.range } : {}),
        ...(o.align ? { align: o.align } : {}),
        ...(o.title !== undefined ? { title: o.title } : {}),
        layers: s.layers.map((l) => ({
          ...l,
          ...(o.transform ? { transform: o.transform } : {}),
          ...(o.type ? { type: o.type } : {}),
        })),
      };
    }
  }
}

/** Comps after a set_compare action: its target and peers, and its columns or the current
 * ones. */
export function compareAfter(
  current: CompareState,
  a: Extract<UiAction, { action: "set_compare" }>,
): CompareState {
  return cleanCompare({
    target: a.target.company_id,
    peers: a.peers.map((p) => p.company_id),
    cols: a.columns ?? current.cols,
  });
}

/** Filters after a set_screen_filters action: merged into the current ones unless reset. */
export function screenAfter(current: ScreenFilters, filters: ScreenFilters, reset: boolean): ScreenFilters {
  return clean({ ...(reset ? {} : current), ...filters, page: undefined });
}

const MAX_CONTEXT = 3800; // agent-api refuses more than 4000 bytes

/** What the screen shows, for the agent: the page, its company, and the chart or screen when
 * that's the page. `name` labels companies the way the user sees them. */
export function uiContext(route: Route, chart: ChartState, name: (id: string) => string | null): UiContext {
  const ctx: UiContext = { page: route.view };
  if (route.view === "company" || route.view === "filing") ctx.company_id = route.companyId;
  if (route.view === "company" && route.tab) ctx.tab = route.tab;
  if (route.view === "compare" && route.compare) {
    const c = route.compare;
    ctx.compare = { ...(c.target ? { target: c.target } : {}), peers: c.peers ?? [], columns: columnsOf(c) };
  }
  if (route.view === "search" && route.search) {
    ctx.query = route.search.q;
    if (route.search.company) ctx.company_id = route.search.company;
  }
  if (route.view === "filing") {
    ctx.accession_no = route.accessionNo;
    if (route.section) ctx.section_key = route.section;
  }
  if (route.view === "companies" && route.screen) ctx.screen = { ...route.screen, page: undefined } as UiContext["screen"];
  if (route.view === "chart") {
    const layers = chart.layers.map((l) => ({
      kind: l.kind,
      ...(l.companyId ? { company_id: l.companyId } : l.defaultCompany ? { company_id: l.defaultCompany } : {}),
      ...((l.companyId ?? l.defaultCompany) && name((l.companyId ?? l.defaultCompany)!)
        ? { label: name((l.companyId ?? l.defaultCompany)!)!.slice(0, 40) }
        : {}),
      ...(l.metric ? { metric: l.metric } : {}),
      ...(l.formula ? { formula: l.formula.slice(0, 300) } : {}),
      transform: l.transform,
      type: l.type,
      axis: l.axis,
      hidden: l.hidden ?? false,
    }));
    ctx.chart = { title: chart.title.slice(0, 200), range: chart.range, frequency: chart.frequency ?? "quarterly", align: chart.align, layers };
    while (JSON.stringify(ctx).length > MAX_CONTEXT && ctx.chart.layers.length) ctx.chart.layers.pop();
  }
  return JSON.parse(JSON.stringify(ctx)) as UiContext; // drops undefined fields
}
