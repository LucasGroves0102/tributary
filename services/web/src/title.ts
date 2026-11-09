/** The browser tab's title, per screen: with several tabs open each says what it is. */

import { UNNAMED } from "./displayNames";
import { COMPANY_TABS, NAV, type Route } from "./route";

const APP = "Tributary";

interface Known {
  /** "Nvidia (NVDA)" */
  company: (companyId: string) => string | null;
  /** what the reader has open: "10-K filed Feb 21, 2026" */
  filing?: string | null;
  /** the chart builder's title */
  chart?: string;
  /** a workspace's, project's or saved item's name, by id */
  saved?: (id: string) => string | null;
}

function what(route: Route, k: Known): string {
  const company = (id: string) => k.company(id) ?? UNNAMED;
  switch (route.view) {
    case "company": {
      const tab = route.tab ?? "overview";
      return tab === "overview" ? company(route.companyId) : `${company(route.companyId)} · ${COMPANY_TABS.find(([t]) => t === tab)![1]}`;
    }
    case "filing":
      return `${company(route.companyId)} · ${k.filing ?? "Filing"}`;
    case "search":
      return route.search?.q ? `Search: “${route.search.q}”` : "Search filings";
    case "chart":
      return k.chart?.trim() || "Chart builder";
    case "compare":
      return route.compare?.target ? `Comps: ${company(route.compare.target)}` : "Compare";
    case "about":
      return "What Tributary covers";
    case "status":
      return "Data status";
    case "settings":
      return "Settings";
    case "alerts":
      return "Alerts";
    case "workspace":
      return k.saved?.(route.workspaceId) ?? "Workspace";
    case "report":
      return k.saved?.(route.itemId) ?? "Report";
    case "thesis":
      return k.saved?.(route.itemId) ?? "Thesis";
    case "model":
      return k.saved?.(route.itemId) ?? "Model";
    case "analysis":
      return (route.projectId && k.saved?.(route.projectId)) || "Analysis";
    default:
      return NAV.find((n) => n.view === route.view)?.label ?? "Home";
  }
}

export function screenTitle(route: Route, known: Known): string {
  return `${what(route, known)} · ${APP}`;
}
