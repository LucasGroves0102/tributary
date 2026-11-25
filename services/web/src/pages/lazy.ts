/** Screens loaded on first use (code splitting): everything but Home and the shell. Each import
 * is its own chunk, so the first screen downloads and parses less; `prefetchScreens` then loads
 * them in the background once the browser is idle, so moving to one is still instant. Sizes per
 * chunk are in services/web/DESIGN.md ("Performance"). */

import { lazy, type ComponentType } from "react";

const screens = {
  chart: () => import("./ChartBuilder"),
  companies: () => import("./Companies"),
  company: () => import("./Company"),
  compare: () => import("./Compare"),
  search: () => import("./Search"),
  about: () => import("./About"),
  status: () => import("./Status"),
  api: () => import("./ApiDocs"),
  settings: () => import("./Settings"),
  reader: () => import("../components/explore/FilingReader"),
  answers: () => import("../components/AssistantMessage"),
  workspace: () => import("../reports/WorkspacePage"),
  report: () => import("../reports/ReportView"),
  thesis: () => import("../thesis/ThesisPage"),
  model: () => import("../models/ModelPage"),
  analysis: () => import("../analysis/AnalysisPage"),
  alerts: () => import("../alerts/AlertsPage"),
  portfolio: () => import("../portfolio/PortfolioPage"),
  data: () => import("../data/DataPage"),
  connect: () => import("../connect/Connect"),
};

type Screens = typeof screens;
type Module<K extends keyof Screens> = Awaited<ReturnType<Screens[K]>>;

/** The modules already here (prefetched, or opened once). */
const loaded = new Map<keyof Screens, unknown>();

function load<K extends keyof Screens>(key: K): Promise<Module<K>> {
  return screens[key]().then((m) => {
    loaded.set(key, m);
    return m as Module<K>;
  });
}

/** A screen's lazy component. Once its module is here, the component resolves at once (a thenable that
 * answers synchronously), so a page change into it is one synchronous commit: the view transition
 * (motion.ts) captures the new page, not a loading fallback that hides the old one. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function screen<K extends keyof Screens, T extends ComponentType<any>>(key: K, pick: (m: Module<K>) => T) {
  return lazy<T>(() => {
    const m = loaded.get(key) as Module<K> | undefined;
    if (m) return { then: (done: (v: { default: T }) => void) => done({ default: pick(m) }) } as unknown as Promise<{ default: T }>;
    return load(key).then((x) => ({ default: pick(x) }));
  });
}

export const ChartBuilder = screen("chart", (m) => m.ChartBuilder);
export const Companies = screen("companies", (m) => m.Companies);
export const Company = screen("company", (m) => m.Company);
export const Compare = screen("compare", (m) => m.Compare);
export const Search = screen("search", (m) => m.Search);
export const About = screen("about", (m) => m.About);
export const Status = screen("status", (m) => m.Status);
export const ApiDocs = screen("api", (m) => m.ApiDocs);
export const Settings = screen("settings", (m) => m.Settings);
export const WorkspacePage = screen("workspace", (m) => m.WorkspacePage);
export const ReportView = screen("report", (m) => m.ReportView);
export const ThesisPage = screen("thesis", (m) => m.ThesisPage);
export const ModelPage = screen("model", (m) => m.ModelPage);
export const AnalysisPage = screen("analysis", (m) => m.AnalysisPage);
export const FilingReader = screen("reader", (m) => m.FilingReader);
export const AlertsPage = screen("alerts", (m) => m.AlertsPage);
export const PortfolioPage = screen("portfolio", (m) => m.PortfolioPage);
export const DataPage = screen("data", (m) => m.DataPage);
export const Connect = screen("connect", (m) => m.Connect);

/** Every screen's module, loaded (tests render lazy screens without waiting on imports). */
export const loadScreens = () => Promise.all((Object.keys(screens) as (keyof Screens)[]).map((k) => load(k)));

/** Loads every screen's chunk when the browser is idle (or after a short delay without
 * requestIdleCallback). Failures are ignored: the screen loads again when opened. */
export function prefetchScreens(): void {
  const run = () => (Object.keys(screens) as (keyof Screens)[]).forEach((k) => void load(k).catch(() => {}));
  const w = window as Window & { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number };
  if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 4000 });
  else setTimeout(run, 2000);
}
