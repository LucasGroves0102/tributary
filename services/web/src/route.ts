import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { parseScreen, screenQuery, type ScreenFilters } from "./companies/screen";
import { parseStatementView, statementQuery, type StatementView } from "./company/statementView";
import { compareQuery, parseCompare, type CompareState } from "./compare/state";
import { parseSearch, searchQuery, type SearchState } from "./search/state";
import { transition } from "./motion";
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
  /** an address that names no page (Riley, test 10, finding 11: #/notebook silently showed Home) */
  | { view: "notfound"; path: string }
  | { view: "companies"; screen?: ScreenFilters }
  | {
      view: "company";
      companyId: string;
      tab?: CompanyTab;
      /** the Statements tab's choices, in the address so they can be linked */
      statements?: StatementView;
      /** the Filings tab with What changed open on this 10-K/10-Q (an alert's link) */
      changes?: string;
      /** a past day (YYYY-MM-DD): the figures as they stood at its end (`?asof=`, any tab) */
      asOf?: string;
    }
  /** a company by its ticker ("#/company/NVDA", the address people type or share): found and replaced
   * by its own address (pages/TickerPage), or said to be no company (Marek 16) */
  | { view: "ticker"; ticker: string; tab?: CompanyTab }
  | {
      view: "filing";
      companyId: string;
      accessionNo: string;
      section: string | null;
      /** a passage to scroll to: a document character offset (a citation's char_start) */
      at?: number;
      /** words to highlight (a search) */
      q?: string;
      /** What changed: the filing's changes against its comparable filing, in the reader (`?view=changes`) */
      mode?: "changes";
    }
  | { view: "chart" }
  | { view: "compare"; compare?: CompareState }
  | { view: "search"; search?: SearchState }
  | { view: "ask" }
  /** what Tributary covers and doesn't (prices, consensus, transcripts) */
  | { view: "about" }
  /** how fresh, complete and checked the data is; public (shown signed out too) */
  | { view: "status" }
  /** "Using the API": API keys, examples, the routes a key can use; public (shown signed out too) */
  | { view: "api" }
  /** Settings: alerts (forms per company, webhooks) and the user's own market-data key */
  | { view: "settings" }
  /** Connect an app (Claude, ChatGPT): the consent screen for a sealed request the Query API's
   * /oauth/authorize sent the browser here with, or the problem it found (`error`, a short code) */
  | { view: "connect"; request?: string; error?: string }
  /** filing alerts: new 10-K/10-Q/8-K of watched and analysed companies, newest first */
  | { view: "alerts" }
  /** the user's holdings (no holdingId), one holding with its journal, or the Fidelity import */
  | { view: "portfolio"; holdingId?: string; import?: boolean }
  /** Your data (B2): the user's own datasets (CSV uploads) and metrics (saved formulas) */
  | { view: "data" }
  /** one saved workspace: its items and reports, "Write a report" */
  | { view: "workspace"; workspaceId: string }
  /** a report the assistant wrote (a workspace item of kind report) */
  | { view: "report"; itemId: string }
  /** a thesis the user writes (Markdown with embedded items; workspace item of kind thesis) */
  | { view: "thesis"; itemId: string }
  /** a revenue model: history + the user's assumptions → forecast (item of kind model) */
  | { view: "model"; itemId: string }
  /** a saved screen by id (alert links): opened as Companies with its filters, as the saved item */
  | { view: "screen"; itemId: string }
  /** the Analysis tab: your projects (no projectId), or one project at a guided step (a model
   * or thesis step may name which of the project's items) */
  | { view: "analysis"; projectId?: string; step?: ProjectStep; itemId?: string; start?: string };

/** Screens in the left nav: routes without parameters. */
export type NavView = "home" | "analysis" | "portfolio" | "companies" | "chart" | "compare" | "data" | "ask";

/** The left nav: label and the key after "g" that opens it. */
export const NAV: { view: NavView; label: string; key: string }[] = [
  { view: "home", label: "Home", key: "h" },
  // Portfolio is g p, Analysis g n (Riley, test 10, finding 70: "gp" beside Analysis read as Portfolio's)
  { view: "analysis", label: "Analysis", key: "n" },
  { view: "portfolio", label: "Portfolio", key: "p" },
  { view: "companies", label: "Companies", key: "c" },
  { view: "chart", label: "Chart builder", key: "b" },
  { view: "compare", label: "Compare", key: "x" },
  { view: "data", label: "Your data", key: "d" },
  { view: "ask", label: "Ask", key: "a" },
];

const CIK = /^\d{10}$/;
/** a ticker as an address may name it: NVDA, BRK-B, BRK.B */
const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;
/** saved-work ids (UUIDs from the Query API); anything else goes Home */
const ID = /^[A-Za-z0-9-]{1,64}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
/** Section keys as the Query API takes them: 10-K "1A", 10-Q "II-1A", 8-K "2.02", "EX-99.1". */
export const SECTION = /^[A-Z0-9.?-]{1,8}$/;
const MAX_Q = 200;
/** A sealed request to connect an app (query-api oauth.py SEALED): base64url payload and signature. */
export const SEALED_REQUEST = /^[A-Za-z0-9_-]{1,8000}\.[A-Za-z0-9_-]{43}$/;
const CONNECT_ERROR = /^[a-z_]{1,32}$/;
/** The first day point-in-time figures go back to (query-api point_in_time.EARLIEST). */
export const EARLIEST_AS_OF = "2009-01-01";

/** A past day as the address may name it: YYYY-MM-DD, a real date, from EARLIEST_AS_OF. */
export function asOfDay(value: string | null | undefined): string | undefined {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value < EARLIEST_AS_OF) return undefined;
  const d = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value ? undefined : value;
}
const SIMPLE = new Set(["chart", "ask", "about", "status", "api", "settings", "alerts", "data"]);

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
  if (view && SIMPLE.has(view)) return { view: view as "chart" | "ask" | "about" | "status" | "api" | "settings" | "alerts" | "data" };
  if (view === "explore") return { view: "companies" }; // links from before the v3 shell
  if (view === "connect") {
    // only a sealed request or a short error code: never a URL (the sign-in return path)
    const params = new URLSearchParams(query);
    const request = params.get("request");
    const error = params.get("error");
    if (request && SEALED_REQUEST.test(request)) return { view, request };
    if (error && CONNECT_ERROR.test(error)) return { view, error };
    return { view, error: "request" };
  }
  if (view === "portfolio") {
    if (a === "import") return { view: "portfolio", import: true };
    return a && ID.test(a) ? { view: "portfolio", holdingId: a } : { view: "portfolio" };
  }
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
  if ((view === "workspace" || view === "report" || view === "thesis" || view === "model" || view === "screen") && ID.test(a ?? "")) {
    return view === "workspace" ? { view, workspaceId: a! } : { view, itemId: a! };
  }
  if (view === "company" && CIK.test(a ?? "")) {
    const asOf = asOfDay(new URLSearchParams(query).get("asof"));
    if (!b || b === "overview" || !TABS.has(b)) return { view: "company", companyId: a!, ...(asOf ? { asOf } : {}) };
    const statements = b === "statements" ? parseStatementView(query) : {};
    const changes = b === "filings" ? new URLSearchParams(query).get("changes") : null;
    return {
      view: "company",
      companyId: a!,
      tab: b as CompanyTab,
      ...(Object.keys(statements).length ? { statements } : {}),
      ...(changes && ACCESSION.test(changes) ? { changes } : {}),
      ...(asOf ? { asOf } : {}),
    };
  }
  // a CIK without its leading zeros is the company's own address
  if (view === "company" && /^\d{1,9}$/.test(a ?? "") && Number(a) > 0) {
    return parse(`#/company/${a!.padStart(10, "0")}${b ? `/${b}` : ""}${query ? `?${query}` : ""}`);
  }
  if (view === "company" && TICKER.test(a ?? "")) {
    return { view: "ticker", ticker: a!.toUpperCase(), ...(b && b !== "overview" && TABS.has(b) ? { tab: b as CompanyTab } : {}) };
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
      ...(params.get("view") === "changes" ? { mode: "changes" as const } : {}),
    };
  }
  // a page name this app doesn't have: say so rather than show Home under a wrong address; the
  // sign-in fragment and anything malformed under a known page still lead home
  if (view && /^[a-z][a-z0-9-]{0,40}$/.test(view) && !KNOWN.has(view)) return { view: "notfound", path: view };
  return { view: "home" };
}

/** The page names parse knows (the rest are not found). */
const KNOWN = new Set([...SIMPLE, "companies", "compare", "search", "explore", "connect", "portfolio", "analysis", "workspace",
  "report", "thesis", "model", "screen", "company", "filing", "signed-in", "home"]);

export function href(route: Route): string {
  switch (route.view) {
    case "home":
      return "#/";
    case "notfound":
      return `#/${route.path}`;
    case "ticker":
      return `#/company/${route.ticker}${route.tab ? `/${route.tab}` : ""}`;
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
    case "portfolio":
      return route.import ? "#/portfolio/import" : route.holdingId ? `#/portfolio/${encodeURIComponent(route.holdingId)}` : "#/portfolio";
    case "analysis":
      if (!route.projectId) return route.start ? `#/analysis?start=${route.start}` : "#/analysis";
      return `#/analysis/${encodeURIComponent(route.projectId)}` +
        (route.step ? `/${route.step}` : "") +
        (route.step && route.itemId && ITEM_STEPS.has(route.step) ? `/${encodeURIComponent(route.itemId)}` : "");
    case "report":
    case "thesis":
    case "model":
    case "screen":
      return `#/${route.view}/${encodeURIComponent(route.itemId)}`;
    case "connect":
      return route.request ? `#/connect?request=${route.request}` : `#/connect?error=${route.error ?? "request"}`;
    case "company": {
      const tabQ = route.tab === "statements" && route.statements ? statementQuery(route.statements)
        : route.tab === "filings" && route.changes ? `changes=${route.changes}` : "";
      const q = [tabQ, route.asOf ? `asof=${route.asOf}` : ""].filter(Boolean).join("&");
      return `#/company/${route.companyId}` + (route.tab && route.tab !== "overview" ? `/${route.tab}` : "") + (q ? `?${q}` : "");
    }
    case "filing": {
      const params = new URLSearchParams();
      if (route.at !== undefined) params.set("at", String(route.at));
      if (route.q) params.set("q", route.q);
      if (route.mode === "changes") params.set("view", "changes");
      return `#/filing/${route.companyId}/${route.accessionNo}` +
        (route.section ? `/${encodeURIComponent(route.section)}` : "") +
        (params.size ? `?${params}` : "");
    }
    default:
      return `#/${route.view}`;
  }
}

/** Which page a route is, without its filters: a new page starts at the top (Riley 92: the chart builder
 * opened scrolled to the middle, where the page before had been), a filter or a sort keeps the place.
 * A company's tabs and an analysis' steps are pages; a filing's section is one too. */
export function pageOf(route: Route): string {
  switch (route.view) {
    case "company":
      return `company/${route.companyId}/${route.tab ?? "overview"}`;
    case "filing":
      return `filing/${route.companyId}/${route.accessionNo}/${route.section ?? ""}`;
    case "workspace":
      return `workspace/${route.workspaceId}`;
    case "portfolio":
      return `portfolio/${route.import ? "import" : (route.holdingId ?? "")}`;
    case "report":
    case "thesis":
    case "model":
    case "screen":
      return `${route.view}/${route.itemId}`;
    case "analysis":
      return `analysis/${route.projectId ?? ""}/${route.step ?? ""}/${route.itemId ?? ""}`;
    default:
      return route.view;
  }
}

export function go(route: Route): void {
  window.location.hash = href(route);
}

/** The address as a route. A change of page runs as a view transition (motion.ts): the update is
 * committed synchronously inside it, so the old page is captured before and the new one after. */
export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  const current = useRef(route);
  useEffect(() => {
    const onChange = () => {
      const next = parse(window.location.hash);
      const prev = current.current;
      current.current = next;
      transition(prev, next, () => flushSync(() => setRoute(next)));
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
