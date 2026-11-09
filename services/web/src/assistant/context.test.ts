import { describe, expect, it } from "vitest";

import type { Route } from "../route";
import { pageContext } from "./context";

const NVDA = "0001045810";
const TSLA = "0001318605";
const VRT = "0001674101";
const name = (id: string) => (id === NVDA ? "Nvidia" : id === TSLA ? "Tesla" : id === VRT ? "Vertiv" : null);
const title = (id: string) => (id === NVDA ? "Nvidia (NVDA)" : id === TSLA ? "Tesla (TSLA)" : null);

const PAGES: Route[] = [
  { view: "home" },
  { view: "companies" },
  { view: "chart" },
  { view: "compare" },
  { view: "ask" },
  { view: "about" },
  { view: "status" },
  { view: "settings" },
  { view: "search" },
  { view: "workspace", workspaceId: "w" },
  { view: "report", itemId: "r" },
  { view: "thesis", itemId: "t" },
  { view: "model", itemId: "m" },
  { view: "analysis" },
];

const all = (route: Route, watchlist: string[] = [], extra = {}) =>
  pageContext(route, name, () => true, { watchlist, title, ...extra });

describe("pageContext", () => {
  it("suggests prompts about the company on its page, and titles the chip with its ticker", () => {
    const c = all({ view: "company", companyId: TSLA, tab: "statements" });
    expect(c.label).toBe("Tesla (TSLA) · Statements");
    expect(c.suggestions.some((p) => p.includes("Tesla"))).toBe(true);
  });

  it("never suggests prompts about a company the UI doesn't list (a demo sandbox's others)", () => {
    const listed = (id: string) => id === NVDA;
    for (const route of [
      { view: "company", companyId: TSLA } as const,
      { view: "filing", companyId: TSLA, accessionNo: "0001318605-26-000099", section: null } as const,
      { view: "compare", compare: { target: TSLA } } as const,
    ]) {
      const c = pageContext(route, name, listed, { watchlist: [NVDA, TSLA] });
      expect(c.label).toBe("Not in this demo");
      expect(c.suggestions.join(" ")).not.toContain("Tesla");
    }
    expect(pageContext({ view: "company", companyId: NVDA }, name, listed).label).toBe("Nvidia · Overview");
  });

  it("offers no test strings and no other company, anywhere", () => {
    for (const route of PAGES) {
      for (const watchlist of [[], [TSLA, VRT]]) {
        const text = all(route, watchlist).suggestions.join(" ");
        expect(text).not.toMatch(/I'm bob|SYSTEM:|override/i);
        expect(text).not.toMatch(/Nvidia|Microsoft/);
      }
    }
  });

  it("only names a company the watchlist holds, and doesn't claim a watchlist that isn't there", () => {
    const home = all({ view: "home" }, [TSLA, VRT]);
    expect(home.suggestions.join(" ")).toContain("Tesla and Vertiv");
    expect(home.label).toBe("Home · watchlist");
    const empty = all({ view: "home" });
    expect(empty.label).toBe("Home");
    expect(empty.suggestions.join(" ")).not.toMatch(/my |watchlist|Tesla/i);
    // "Tesla is on my watchlist" was the kind of thing this used to say
    for (const route of PAGES) expect(all(route).suggestions.join(" ")).not.toMatch(/\bmy\b/i);
  });

  it("the chart's prompts follow the chart", () => {
    expect(all({ view: "chart" }).suggestions).toEqual([]);
    expect(all({ view: "chart" }, [TSLA]).suggestions).toEqual(["Chart Tesla's revenue and operating margin"]);
    const amazonLike = all({ view: "chart" }, [TSLA], { chartHasLayers: true, charted: [VRT], company: VRT }).suggestions;
    expect(amazonLike).toEqual(["Show it as YoY growth over 5 years", "Add Vertiv's operating margin", "Add Tesla's revenue"]);
    // a company already on it isn't offered again
    const both = all({ view: "chart" }, [TSLA], { chartHasLayers: true, charted: [VRT, TSLA], company: VRT }).suggestions;
    expect(both).toEqual(["Show it as YoY growth over 5 years", "Add Vertiv's operating margin"]);
  });

  it("a filing is described in words, with no accession number or section key in the prompts", () => {
    const route: Route = { view: "filing", companyId: NVDA, accessionNo: "0001045810-26-000075", section: "I-2" };
    const c = all(route, [], { filing: "10-Q filed Aug 26, 2026" });
    expect(c.label).toBe("Nvidia (NVDA) · 10-Q filed Aug 26, 2026 · MD&A");
    const text = c.suggestions.join(" ");
    expect(text).toContain("MD&A");
    expect(text).not.toMatch(/\d{10}-\d{2}-\d{6}|I-2/);
    const whole = all({ ...route, section: null }).label;
    expect(whole).toBe("Nvidia (NVDA) · Filing");
  });

  it("an analysis is about its company", () => {
    const c = all({ view: "analysis", projectId: "p" }, [TSLA], { company: VRT });
    expect(c.suggestions.join(" ")).toContain("Vertiv");
    expect(c.suggestions.join(" ")).not.toContain("Tesla");
  });

  it("shows a company by name while waiting for it, never its id", () => {
    const c = all({ view: "company", companyId: "0000000001" });
    expect(c.label).toBe("This company · Overview");
    expect(JSON.stringify(c)).not.toContain("0000000001");
    expect(all({ view: "compare", compare: { target: "0000000001" } }).label).toBe("Comps · This company");
  });
});
