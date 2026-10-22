/** Saved work: what a chart, comps set or screen is saved as (the Query API validates the same
 * shapes, query_api/workspaces.py), how a saved state is restored (through the same cleaners
 * as the URL and local storage, so a saved item is never trusted more than a hand-edited
 * link), and whether the screen has unsaved changes. Pure, tested without rendering.
 *
 * A saved item grants nothing: it holds company ids and settings, and each page reads the data
 * through the Query API as usual. */

import { emptyChart, restoreChart, type ChartState } from "../chart/model";
import { parseScreen, screenQuery, type ScreenFilters } from "../companies/screen";
import { cleanCompare, compareQuery, parseCompare, type CompareState } from "../compare/state";
import type { Route } from "../route";
import type { SavedKind } from "../types";

export const KIND_LABELS: Record<SavedKind, string> = { chart: "Chart", comps: "Comps", screen: "Screen" };
export const KIND_PAGES: Record<SavedKind, string> = {
  chart: "Chart builder",
  comps: "Compare",
  screen: "Companies",
};
export const WORKSPACE_NAME_MAX = 100;
export const ITEM_NAME_MAX = 120;

/** The state a page saves: the chart without ids, comps and screen as their URL cleaners keep
 * them (the screen without its page). */
export type Current =
  | { kind: "chart"; chart: ChartState }
  | { kind: "comps"; compare: CompareState }
  | { kind: "screen"; screen: ScreenFilters };

export function stateOf(c: Current): Record<string, unknown> {
  switch (c.kind) {
    case "chart":
      return {
        title: c.chart.title,
        range: c.chart.range,
        align: c.chart.align,
        // a metric layer still being filled in isn't saved
        layers: c.chart.layers.filter((l) => l.kind === "formula" || (l.companyId && l.metric)).map((l) => {
          const out: Record<string, unknown> = { kind: l.kind, transform: l.transform, type: l.type, axis: l.axis };
          if (l.kind === "metric") {
            out.companyId = l.companyId;
            out.metric = l.metric;
          } else {
            out.formula = l.formula ?? "";
            if (l.defaultCompany) out.defaultCompany = l.defaultCompany;
          }
          if (l.hidden) out.hidden = true;
          if (l.name) out.name = l.name;
          if (l.color !== undefined) out.color = l.color;
          return out;
        }),
        ...(c.chart.markers ? { markers: true } : {}),
        ...(c.chart.range === "custom" && c.chart.start ? { start: c.chart.start } : {}),
        ...(c.chart.range === "custom" && c.chart.end ? { end: c.chart.end } : {}),
        ...(c.chart.frequency === "annual" ? { frequency: "annual" } : {}),
        ...(c.chart.deck ? { deck: true } : {}),
        ...(c.chart.subtitle ? { subtitle: c.chart.subtitle } : {}),
      };
    case "comps":
      return { ...cleanCompare(c.compare) };
    case "screen":
      return { ...parseScreen(screenQuery({ ...c.screen, page: undefined })) };
  }
}

/** Whether there is anything to save: a chart needs a complete layer, comps a target. */
export function canSave(c: Current): boolean {
  if (c.kind === "chart") return c.chart.layers.some((l) => (l.kind === "metric" ? l.companyId && l.metric : l.formula?.trim()));
  if (c.kind === "comps") return !!cleanCompare(c.compare).target;
  return true;
}

/** A saved state (untrusted: from the API) as a page's state. */
export function restoreChartState(state: unknown): ChartState {
  return state && typeof state === "object" ? restoreChart(state) : emptyChart();
}

export function restoreCompare(state: unknown): CompareState {
  const o = (state && typeof state === "object" ? state : {}) as Record<string, unknown>;
  const list = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  return parseCompare(
    compareQuery({
      target: typeof o.target === "string" ? o.target : undefined,
      peers: list(o.peers),
      cols: list(o.cols),
    }),
  );
}

export function restoreScreen(state: unknown): ScreenFilters {
  const o = (state && typeof state === "object" ? state : {}) as Record<string, unknown>;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) {
    if (k === "page") continue;
    if (typeof v === "string" || typeof v === "number") p.set(k, String(v));
    else if (v === true) p.set(k, "1");
  }
  return parseScreen(p.toString());
}

/** Where opening a saved item goes, with its state. A chart's state goes to the chart builder
 * (it isn't in the URL); comps and screens live in the URL. */
export function itemRoute(kind: SavedKind, state: unknown): Route {
  if (kind === "comps") {
    const compare = restoreCompare(state);
    return Object.keys(compare).length ? { view: "compare", compare } : { view: "compare" };
  }
  if (kind === "screen") {
    const screen = restoreScreen(state);
    return Object.keys(screen).length ? { view: "companies", screen } : { view: "companies" };
  }
  return { view: "chart" };
}

/** Key order doesn't matter: equal states serialize equally. */
export function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v)
      .filter((k) => (v as Record<string, unknown>)[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(v ?? null);
}

/** The saved state normalized the way this page would save it, for the unsaved-changes check
 * (the API fills in defaults such as `hidden: false`). */
export function fingerprint(kind: SavedKind, state: unknown): string {
  switch (kind) {
    case "chart":
      return stable(stateOf({ kind, chart: restoreChartState(state) }));
    case "comps":
      return stable(stateOf({ kind, compare: restoreCompare(state) }));
    case "screen":
      return stable(stateOf({ kind, screen: restoreScreen(state) }));
  }
}

export function currentFingerprint(c: Current): string {
  return fingerprint(c.kind, stateOf(c));
}

/** The item a page has open: which saved item, and its state when last saved or opened. */
export interface OpenItem {
  itemId: string;
  workspaceId: string;
  workspaceName: string;
  name: string;
  saved: string;
}

export type OpenItems = Partial<Record<SavedKind, OpenItem>>;

/** Open items from storage (untrusted): well-formed entries only. */
export function restoreOpen(raw: unknown): OpenItems {
  const out: OpenItems = {};
  if (!raw || typeof raw !== "object") return out;
  for (const kind of ["chart", "comps", "screen"] as const) {
    const o = (raw as Record<string, unknown>)[kind] as Record<string, unknown> | undefined;
    if (
      o &&
      typeof o.itemId === "string" &&
      typeof o.workspaceId === "string" &&
      typeof o.workspaceName === "string" &&
      typeof o.name === "string" &&
      typeof o.saved === "string"
    ) {
      out[kind] = {
        itemId: o.itemId,
        workspaceId: o.workspaceId,
        workspaceName: o.workspaceName.slice(0, WORKSPACE_NAME_MAX),
        name: o.name.slice(0, ITEM_NAME_MAX),
        saved: o.saved,
      };
    }
  }
  return out;
}

/** Validation the API repeats: trimmed, 1..max characters, no control characters. */
export function nameProblem(name: string, max: number): string | null {
  const t = name.trim();
  if (!t) return "Give it a name.";
  if (t.length > max) return `At most ${max} characters.`;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(t)) return "No control characters.";
  return null;
}

/** A workspace by name, ignoring case and surrounding spaces (the assistant names them). */
export function findWorkspace<T extends { name: string }>(list: T[], name: string): T | undefined {
  const key = name.trim().toLowerCase();
  return list.find((w) => w.name.trim().toLowerCase() === key);
}

/** A default name for what's on screen: the chart title, the comps' target ticker. */
export function suggestName(c: Current, label: (companyId: string) => string): string {
  if (c.kind === "chart") return c.chart.title.trim().slice(0, ITEM_NAME_MAX) || "Chart";
  if (c.kind === "comps") {
    const s = cleanCompare(c.compare);
    return s.target ? `${label(s.target)} comps`.slice(0, ITEM_NAME_MAX) : "Comps";
  }
  return "Screen";
}
