import { useEffect, useState } from "react";

import { parseScreen, screenQuery, type ScreenFilters } from "./companies/screen";
import { compareQuery, parseCompare, type CompareState } from "./compare/state";

/** The company page's tabs, in order, with their labels. */
export const COMPANY_TABS = [
  ["overview", "Overview"],
  ["statements", "Statements"],
  ["segments", "Segments"],
  ["ratios", "Ratios"],
  ["filings", "Filings"],
  ["peers", "Peers"],
] as const;
export type CompanyTab = (typeof COMPANY_TABS)[number][0];
const TABS = new Set<string>(COMPANY_TABS.map(([t]) => t));

export type Route =
  | { view: "home" }
  | { view: "companies"; screen?: ScreenFilters }
  | { view: "company"; companyId: string; tab?: CompanyTab }
  | { view: "filing"; companyId: string; accessionNo: string; section: string | null }
  | { view: "chart" }
  | { view: "compare"; compare?: CompareState }
  | { view: "ask" };

/** Screens in the left nav: routes without parameters. */
export type NavView = "home" | "companies" | "chart" | "compare" | "ask";

/** The left nav: label and the key after "g" that opens it. */
export const NAV: { view: NavView; label: string; key: string }[] = [
  { view: "home", label: "Home", key: "h" },
  { view: "companies", label: "Companies", key: "c" },
  { view: "chart", label: "Chart builder", key: "b" },
  { view: "compare", label: "Compare", key: "x" },
  { view: "ask", label: "Ask", key: "a" },
];

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const SIMPLE = new Set(["chart", "ask"]);

/** "#/company/0001045810" → route; anything unrecognized is Home. Companies keeps its
 * screener filters in a query ("#/companies?band=1b-10b"), Compare its comps
 * ("#/compare?target=…&peers=…"), the company page its tab ("#/company/…/statements"). */
export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?", 2) as [string, string?];
  const [view, a, b, c] = path.split("/").map(decodeURIComponent);
  if (view === "companies") {
    const screen = parseScreen(query);
    return Object.keys(screen).length ? { view, screen } : { view };
  }
  if (view === "compare") {
    const compare = parseCompare(query);
    return Object.keys(compare).length ? { view, compare } : { view };
  }
  if (view && SIMPLE.has(view)) return { view: view as NavView };
  if (view === "explore") return { view: "companies" }; // links from before the v3 shell
  if (view === "company" && CIK.test(a ?? "")) {
    return b && b !== "overview" && TABS.has(b)
      ? { view: "company", companyId: a!, tab: b as CompanyTab }
      : { view: "company", companyId: a! };
  }
  if (view === "filing" && CIK.test(a ?? "") && ACCESSION.test(b ?? "")) {
    return { view: "filing", companyId: a!, accessionNo: b!, section: c || null };
  }
  return { view: "home" };
}

export function href(route: Route): string {
  switch (route.view) {
    case "home":
      return "#/";
    case "companies": {
      const q = route.screen ? screenQuery(route.screen) : "";
      return q ? `#/companies?${q}` : "#/companies";
    }
    case "compare": {
      const q = route.compare ? compareQuery(route.compare) : "";
      return q ? `#/compare?${q}` : "#/compare";
    }
    case "company":
      return `#/company/${route.companyId}` + (route.tab && route.tab !== "overview" ? `/${route.tab}` : "");
    case "filing":
      return `#/filing/${route.companyId}/${route.accessionNo}` +
        (route.section ? `/${encodeURIComponent(route.section)}` : "");
    default:
      return `#/${route.view}`;
  }
}

export function go(route: Route): void {
  window.location.hash = href(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
