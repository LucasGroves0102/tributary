import { useEffect, useState } from "react";

import { parseScreen, screenQuery, type ScreenFilters } from "./companies/screen";
import { parseStatementView, statementQuery, type StatementView } from "./company/statementView";
import { compareQuery, parseCompare, type CompareState } from "./compare/state";
import { parseSearch, searchQuery, type SearchState } from "./search/state";
import type { ProjectStep } from "./types";

/** An analysis project's guided steps, in order (analysis/project.ts STEPS has their labels). */
export const PROJECT_STEPS: readonly ProjectStep[] = ["business", "numbers", "earnings", "changes", "peers", "model", "thesis", "monitor"];
const STEP_SET = new Set<string>(PROJECT_STEPS);
/** steps whose saved item can be named in the route (#/analysis/{id}/model/{item}) */
const ITEM_STEPS = new Set<string>(["model", "thesis"]);

/** The company page's tabs, in order, with their labels. */
export const COMPANY_TABS = [
  ["overview", "Overview"],
  ["earnings", "Earnings"],
  ["statements", "Statements"],
  ["revenue", "Revenue"],
  ["segments", "Segments"],
  ["ratios", "Ratios"],
  ["filings", "Filings"],
  ["notes", "Data notes"],
  ["peers", "Peers"],
  /** "Connect your data": shown only when the server has the feature on (Company.tsx) */
  ["price", "Price"],
] as const;
export type CompanyTab = (typeof COMPANY_TABS)[number][0];
const TABS = new Set<string>(COMPANY_TABS.map(([t]) => t));

export type Route =
  | { view: "home" }
  | { view: "companies"; screen?: ScreenFilters }
  | {
      view: "company";
      companyId: string;
      tab?: CompanyTab;
      /** the Statements tab's choices, in the address so they can be linked */
      statements?: StatementView;
    }
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
  /** how fresh, complete and checked the data is; public (shown signed out too) */
  | { view: "status" }
  /** Settings → Connections: the user's own market-data key ("Connect your data") */
  | { view: "settings" }
  /** one saved workspace: its items and reports, "Write a report" */
  | { view: "workspace"; workspaceId: string }
  /** a report the assistant wrote (a workspace item of kind report) */
  | { view: "report"; itemId: string }
  /** a thesis the user writes (Markdown with embedded items; workspace item of kind thesis) */
  | { view: "thesis"; itemId: string }
  /** a revenue model: history + the user's assumptions → forecast (item of kind model) */
  | { view: "model"; itemId: string }
  /** the Analysis tab: your projects (no projectId), or one project at a guided step (a model
   * or thesis step may name which of the project's items) */
  | { view: "analysis"; projectId?: string; step?: ProjectStep; itemId?: string; start?: string };

/** Screens in the left nav: routes without parameters. */
export type NavView = "home" | "analysis" | "companies" | "chart" | "compare" | "ask";

/** The left nav: label and the key after "g" that opens it. */
export const NAV: { view: NavView; label: string; key: string }[] = [
  { view: "home", label: "Home", key: "h" },
  { view: "analysis", label: "Analysis", key: "p" },
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
const SIMPLE = new Set(["chart", "ask", "about", "status", "settings"]);

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
  if (view && SIMPLE.has(view)) return { view: view as "chart" | "ask" | "about" | "status" | "settings" };
  if (view === "explore") return { view: "companies" }; // links from before the v3 shell
  if (view === "analysis") {
    if (!a || !ID.test(a)) {
      // "#/analysis?start=<company>": the projects list with New analysis open on that company (L5)
      const start = new URLSearchParams(query).get("start");
      return start && CIK.test(start) ? { view: "analysis", start } : { view: "analysis" };
    }
    if (!b || !STEP_SET.has(b)) return { view: "analysis", projectId: a };
    const step = b as ProjectStep;
    return c && ITEM_STEPS.has(step) && ID.test(c) ? { view, projectId: a, step, itemId: c } : { view, projectId: a, step };
  }
  if ((view === "workspace" || view === "report" || view === "thesis" || view === "model") && ID.test(a ?? "")) {
    return view === "workspace" ? { view, workspaceId: a! } : { view, itemId: a! };
  }
  if (view === "company" && CIK.test(a ?? "")) {
    if (!b || b === "overview" || !TABS.has(b)) return { view: "company", companyId: a! };
    const statements = b === "statements" ? parseStatementView(query) : {};
    return {
      view: "company",
      companyId: a!,
      tab: b as CompanyTab,
      ...(Object.keys(statements).length ? { statements } : {}),
    };
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
    case "analysis":
      if (!route.projectId) return route.start ? `#/analysis?start=${route.start}` : "#/analysis";
      return `#/analysis/${encodeURIComponent(route.projectId)}` +
        (route.step ? `/${route.step}` : "") +
        (route.step && route.itemId && ITEM_STEPS.has(route.step) ? `/${encodeURIComponent(route.itemId)}` : "");
    case "report":
    case "thesis":
    case "model":
      return `#/${route.view}/${encodeURIComponent(route.itemId)}`;
    case "company": {
      const q = route.tab === "statements" && route.statements ? statementQuery(route.statements) : "";
      return `#/company/${route.companyId}` + (route.tab && route.tab !== "overview" ? `/${route.tab}` : "") + (q ? `?${q}` : "");
    }
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
