import { sectionName } from "../reader/sections";
import { COMPANY_TABS, type CompanyTab, type Route } from "../route";

/** What to ask on each tab of the company page (n: the company's name). */
const COMPANY_SUGGESTIONS: Record<CompanyTab, (n: string) => string[]> = {
  overview: (n) => [
    `Summarize ${n}'s latest quarter.`,
    `What drove ${n}'s revenue change versus a year ago?`,
    `What changed in ${n}'s risk factors in its latest 10-K?`,
  ],
  earnings: (n) => [
    `What guidance did ${n} give in its latest earnings release?`,
    `What adjustments does ${n} make to reach its adjusted figures?`,
    `What has ${n} filed since its last 10-Q?`,
  ],
  statements: (n) => [
    `How has ${n}'s operating margin moved over the last 8 quarters?`,
    `Chart ${n}'s revenue and operating income`,
    `Which of ${n}'s expenses grew fastest over the last year?`,
  ],
  revenue: (n) => [
    `Which of ${n}'s segments drove its revenue growth over the last year?`,
    `Are ${n}'s receivables growing faster than its revenue?`,
  ],
  segments: (n) => [
    `Which of ${n}'s segments grew fastest over the last year?`,
    `How much of ${n}'s revenue comes from its largest segment?`,
  ],
  ratios: (n) => [
    `How has ${n}'s free cash flow margin changed over two years?`,
    `Chart ${n}'s return on equity`,
    `Is ${n}'s leverage rising or falling?`,
  ],
  filings: (n) => [
    `What changed in ${n}'s latest 10-Q versus the one before?`,
    `Summarize ${n}'s most recent 8-K.`,
  ],
  notes: (n) => [
    `What is ${n}'s latest 10-Q or 10-K, and when was it filed?`,
    `How is ${n}'s fiscal Q4 derived when the 10-K reports only the year?`,
  ],
  peers: (n) => [
    `Compare ${n} with its peers`,
    `How does ${n}'s operating margin compare with its peers?`,
  ],
  // the assistant can't read the user's prices: ask about the filings behind the multiples
  price: (n) => [
    `How has ${n}'s TTM net income changed over the last year?`,
    `What is ${n}'s net debt in its latest 10-Q?`,
  ],
};

export interface PageContext {
  /** the chip in the assistant's header */
  label: string;
  /** prompts for this page (the agent also gets the screen as context) */
  suggestions: string[];
}

/** What else the prompts can draw on, beyond the address: all of it this user's own, so a prompt
 * is never about a company that has nothing to do with what's on screen. */
export interface Situation {
  /** the watchlist's company ids, in order */
  watchlist?: string[];
  /** the company a chart on screen, or an analysis, is about */
  company?: string | null;
  /** the companies on the chart builder (with `company` the first of them) */
  charted?: string[];
  /** something is on the chart, even with no company (a formula) */
  chartHasLayers?: boolean;
  /** what the reader has open: "10-K filed Feb 21, 2026" */
  filing?: string | null;
  /** "Nvidia (NVDA)" for the header chip; the prompts say the plain name */
  title?: (companyId: string) => string | null;
}

/** "A", "A and B", "A, B and C". */
function list(names: string[]): string {
  return names.length < 2 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

/** Prompts for a page about no company in particular: about the user's own watchlist, and if there
 * is none, ones any reader can use. Each names its companies, since the agent can't see a watchlist. */
function general(watched: string[]): string[] {
  if (!watched.length) {
    return ["Which companies mention tariffs in their latest 10-Q?", "Which recent 8-Ks report a change of auditor?"];
  }
  const some = watched.slice(0, 3);
  return [
    `What did ${list(some)} file recently?`,
    ...(some.length > 1 ? [`Which grew revenue fastest over the last year: ${list(some)}?`] : [`How has ${some[0]}'s revenue changed over the last 4 quarters?`]),
    `Summarize the top risk factors in ${some[0]}'s latest 10-K.`,
  ];
}

/** What the assistant shows for the page the user is on. `name` resolves a company id to the
 * name the user knows it by (null if unknown). `listed` says whether the UI lists a company
 * (scope.ts: a demo sandbox lists only its own); a page about one it doesn't gets general
 * prompts, not ones about that company. The agent also gets the screen itself (actions.ts
 * `uiContext`), so prompts on the chart, the screener and a filing can say "it" or "this". */
export function pageContext(
  route: Route,
  name: (companyId: string) => string | null,
  listed: (companyId: string) => boolean = () => true,
  situation: Situation = {},
): PageContext {
  const watched = (situation.watchlist ?? []).filter(listed).flatMap((id) => {
    const n = name(id);
    return n ? [n] : [];
  });
  const about =
    route.view === "company" || route.view === "filing"
      ? route.companyId
      : route.view === "compare"
        ? route.compare?.target
        : undefined;
  if (about !== undefined && !listed(about)) {
    return { label: "Not in this demo", suggestions: general(watched).slice(0, 2) };
  }
  // the company page's own words while its name is on its way: never an id
  const title = (id: string) => situation.title?.(id) ?? name(id) ?? "This company";
  const called = (id: string) => name(id) ?? "this company";
  const subject = situation.company && listed(situation.company) ? name(situation.company) : null;
  // a saved thesis, model or analysis: about its company when that's known
  const research = subject
    ? [`What changed in ${subject}'s latest 10-Q risk factors?`, `What does ${subject}'s latest 10-K say about revenue growth drivers?`]
    : general(watched).slice(0, 2);
  switch (route.view) {
    case "home":
      return { label: watched.length ? "Home · watchlist" : "Home", suggestions: general(watched) };
    case "companies":
      return {
        label: "Companies",
        suggestions: [
          "Only companies with operating margin above 25%",
          "Semiconductor companies growing faster than 20%",
          "Which of these has the highest operating margin?",
        ],
      };
    case "company": {
      const tab = route.tab ?? "overview";
      return {
        label: `${title(route.companyId)} · ${COMPANY_TABS.find(([t]) => t === tab)![1]}`,
        suggestions: COMPANY_SUGGESTIONS[tab](called(route.companyId)),
      };
    }
    case "filing": {
      const n = called(route.companyId);
      const filing = situation.filing ?? "Filing";
      if (route.section) {
        const section = sectionName(route.section);
        return {
          label: `${title(route.companyId)} · ${filing} · ${section}`,
          suggestions: [
            `Summarize ${section} in this filing.`,
            `What changed in ${section} versus ${n}'s previous filing?`,
            `What are the key risks or numbers in ${section}?`,
          ],
        };
      }
      return {
        label: `${title(route.companyId)} · ${filing}`,
        suggestions: ["Summarize this filing.", "What changed in this filing versus the one before?"],
      };
    }
    case "chart": {
      if (situation.chartHasLayers) {
        const onChart = new Set((situation.charted ?? []).map(name));
        const other = watched.find((w) => !onChart.has(w));
        return {
          label: "Chart builder",
          suggestions: [
            "Show it as YoY growth over 5 years",
            ...(subject ? [`Add ${subject}'s operating margin`] : []),
            ...(other ? [`Add ${other}'s revenue`] : []),
          ],
        };
      }
      return {
        label: "Chart builder",
        suggestions: watched.length ? [`Chart ${watched[0]}'s revenue and operating margin`] : [],
      };
    }
    case "compare": {
      const target = route.compare?.target;
      if (!target) {
        return {
          label: "Compare",
          suggestions: [
            ...(watched[0] ? [`Compare ${watched[0]} with its peers`] : []),
            ...(watched[1] ? [`Compare ${watched[0]}'s and ${watched[1]}'s operating income last quarter.`] : []),
          ],
        };
      }
      return {
        label: `Comps · ${title(target)}`,
        suggestions: [
          `Which of these companies has the highest free cash flow margin?`,
          `How does ${called(target)}'s operating margin compare with the peer median?`,
          "Chart these companies' gross margin",
        ],
      };
    }
    case "search": {
      const q = route.search?.q;
      return {
        label: q ? `Search · “${q}”` : "Search filings",
        suggestions: q
          ? [`Which companies discuss “${q}” most in their filings?`, `What do the latest filings say about ${q}?`]
          : ["Which companies mention export controls in their risk factors?"],
      };
    }
    case "ask":
      return { label: "Ask", suggestions: general(watched) };
    case "about":
      return { label: "What Tributary covers", suggestions: ["What can you answer from SEC filings, and what can't you?"] };
    case "status":
      return { label: "Data status", suggestions: [] };
    case "settings":
      return { label: "Settings", suggestions: [] };
    case "alerts":
      return { label: "Alerts", suggestions: general(watched).slice(0, 2) };
    case "workspace":
      return { label: "Workspace", suggestions: general(watched).slice(0, 2) };
    case "report":
      return { label: "Report", suggestions: research };
    case "thesis":
      return { label: "Thesis", suggestions: research };
    case "model":
      return { label: "Model", suggestions: research };
    case "analysis":
      return { label: subject ? `Analysis · ${title(situation.company!)}` : "Analysis", suggestions: research };
  }
}
