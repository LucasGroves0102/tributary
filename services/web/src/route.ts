import { useEffect, useState } from "react";

import { parseScreen, screenQuery, type ScreenFilters } from "./companies/screen";
import { compareQuery, parseCompare, type CompareState } from "./compare/state";
import { parseSearch, searchQuery, type SearchState } from "./search/state";

/** The company page's tabs, in order, with their labels. */
export const COMPANY_TABS = [
  ["overview", "Overview"],
  ["statements", "Statements"],
  ["segments", "Segments"],
  ["ratios", "Ratios"],
  ["filings", "Filings"],
  ["notes", "Data notes"],
  ["peers", "Peers"],
] as const;
export type CompanyTab = (typeof COMPANY_TABS)[number][0];
const TABS = new Set<string>(COMPANY_TABS.map(([t]) => t));

export type Route =
  | { view: "home" }
  | { view: "companies"; screen?: ScreenFilters }
  | { view: "company"; companyId: string; tab?: CompanyTab }
  | {
      view: "filing";
      companyId: string;
      accessionNo: string;
      section: string | null;
      /** a passage to scroll to: a document character offset (a citation's char_start) */
      at?: number;
      /** words to highlight (a search) */
      q?: string;
    }
  | { view: "chart" }
  | { view: "compare"; compare?: CompareState }
  | { view: "search"; search?: SearchState }
  | { view: "ask" }
  /** what Tributary covers and doesn't (prices, consensus, transcripts) */
  | { view: "about" }
  /** one saved workspace: its items and reports, "Write a report" */
  | { view: "workspace"; workspaceId: string }
  /** a report the assistant wrote (a workspace item of kind report) */
  | { view: "report"; itemId: string };

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
/** saved-work ids (UUIDs from the Query API); anything else goes Home */
const ID = /^[A-Za-z0-9-]{1,64}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
/** Section keys as the Query API takes them: 10-K "1A", 10-Q "II-1A", 8-K "2.02", "EX-99.1". */
export const SECTION = /^[A-Z0-9.?-]{1,8}$/;
const MAX_Q = 200;
const SIMPLE = new Set(["chart", "ask", "about"]);

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
  if (view === "search") {
    const search = parseSearch(query);
    return search ? { view, search } : { view };
  }
  if (view && SIMPLE.has(view)) return { view: view as "chart" | "ask" | "about" };
  if (view === "explore") return { view: "companies" }; // links from before the v3 shell
  if ((view === "workspace" || view === "report") && ID.test(a ?? "")) {
    return view === "workspace" ? { view, workspaceId: a! } : { view, itemId: a! };
  }
  if (view === "company" && CIK.test(a ?? "")) {
    return b && b !== "overview" && TABS.has(b)
      ? { view: "company", companyId: a!, tab: b as CompanyTab }
      : { view: "company", companyId: a! };
  }
  if (view === "filing" && CIK.test(a ?? "") && ACCESSION.test(b ?? "")) {
    const params = new URLSearchParams(query);
    const at = params.get("at");
    const q = params.get("q")?.slice(0, MAX_Q);
    return {
      view: "filing",
      companyId: a!,
      accessionNo: b!,
      section: c && SECTION.test(c) ? c : null,
      ...(at && /^\d{1,9}$/.test(at) ? { at: Number(at) } : {}),
      ...(q ? { q } : {}),
    };
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
    case "search":
      return route.search ? `#/search?${searchQuery(route.search)}` : "#/search";
    case "workspace":
      return `#/workspace/${encodeURIComponent(route.workspaceId)}`;
    case "report":
      return `#/report/${encodeURIComponent(route.itemId)}`;
    case "company":
      return `#/company/${route.companyId}` + (route.tab && route.tab !== "overview" ? `/${route.tab}` : "");
    case "filing": {
      const params = new URLSearchParams();
      if (route.at !== undefined) params.set("at", String(route.at));
      if (route.q) params.set("q", route.q);
      return `#/filing/${route.companyId}/${route.accessionNo}` +
        (route.section ? `/${encodeURIComponent(route.section)}` : "") +
        (params.size ? `?${params}` : "");
    }
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
