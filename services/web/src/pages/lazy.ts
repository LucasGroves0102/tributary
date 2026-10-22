/** Screens loaded on first use (code splitting): everything but Home and the shell. Each import
 * is its own chunk, so the first screen downloads and parses less; `prefetchScreens` then loads
 * them in the background once the browser is idle, so moving to one is still instant. Sizes per
 * chunk are in services/web/README.md ("Performance"). */

import { lazy } from "react";

const screens = {
  chart: () => import("./ChartBuilder"),
  companies: () => import("./Companies"),
  company: () => import("./Company"),
  compare: () => import("./Compare"),
  search: () => import("./Search"),
  about: () => import("./About"),
  reader: () => import("../components/explore/FilingReader"),
  answers: () => import("../components/AssistantMessage"),
};

export const ChartBuilder = lazy(() => screens.chart().then((m) => ({ default: m.ChartBuilder })));
export const Companies = lazy(() => screens.companies().then((m) => ({ default: m.Companies })));
export const Company = lazy(() => screens.company().then((m) => ({ default: m.Company })));
export const Compare = lazy(() => screens.compare().then((m) => ({ default: m.Compare })));
export const Search = lazy(() => screens.search().then((m) => ({ default: m.Search })));
export const About = lazy(() => screens.about().then((m) => ({ default: m.About })));
export const FilingReader = lazy(() => screens.reader().then((m) => ({ default: m.FilingReader })));

/** Every screen's module, loaded (tests render lazy screens without waiting on imports). */
export const loadScreens = () => Promise.all(Object.values(screens).map((load) => load()));

/** Loads every screen's chunk when the browser is idle (or after a short delay without
 * requestIdleCallback). Failures are ignored: the screen loads again when opened. */
export function prefetchScreens(): void {
  const run = () => Object.values(screens).forEach((load) => void load().catch(() => {}));
  const w = window as Window & { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number };
  if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 4000 });
  else setTimeout(run, 2000);
}
