import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ACC, entry, feedFiling, NVDA, revenueRow, TESLA, TSLA } from "./fixtures";
import { forgetVisits } from "./home/lastVisit";

const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials"] }],
};
const EDGAR = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/";
const ANSWER = {
  text: `Revenue was $46.74B in the quarter (${ACC}).`,
  stop: "end_turn",
  model: "claude-test",
  rounds: 2,
  tool_calls: [],
  usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
  cost_usd: null,
  citations: [{ accession_no: ACC, form: "10-Q", filed: "2026-08-26", company_id: NVDA, url: EDGAR, sections: ["I-2", "../x"] }],
  verification: null,
};

const asked: { question: string }[] = [];

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/demo/users")) return Response.json({ users: ["alice", "bob"] });
  if (path.endsWith("/v1/demo/token")) return Response.json({ token: "t", expires_in: 3600 });
  if (path.endsWith("/v1/me")) return Response.json(ME);
  if (path.endsWith("/v1/watchlist")) {
    return Response.json({ as_of: "2026-10-21T12:00:00Z", columns: [], expected_method: "", companies: [entry(), TESLA] });
  }
  if (path.endsWith("/v1/filings/latest")) {
    return Response.json({ as_of: "", filings: [feedFiling({ ingested_at: new Date().toISOString() })] });
  }
  if (path.endsWith("/v1/filing-index")) {
    return Response.json({ as_of: "", forms: [], from: null, to: null, limit: 50, offset: 0, has_more: false, filings: [{
      accession_no: "0001318605-26-000099", company_id: TSLA, name: "Tesla", form: "8-K", filed: new Date().toISOString().slice(0, 10),
      accepted_at: null, period_of_report: null, filing_url: null, document_url: null, sections: null, sections_available: false }] });
  }
  if (path.endsWith("/v1/calendar/expected-filings")) {
    return Response.json({ filings: [{ ...entry().next_expected, company_id: NVDA, name: "Nvidia" }] });
  }
  if (path.endsWith("/financials")) {
    return Response.json({ as_of: "", rows: [revenueRow("2026-04-26", "44062000000", "FY2027 Q1"), revenueRow("2026-07-26", "46743000000", "FY2027 Q2")] });
  }
  if (path.endsWith("/v1/companies/search")) {
    const q = new URL(url, "http://x").searchParams.get("q");
    const ids: Record<string, string> = { NVDA, TSLA };
    return Response.json({ results: q && ids[q] ? [{ company_id: ids[q], name: q, matched_on: "ticker", matched_alias: q, is_current: true, access: [] }] : [] });
  }
  if (path.endsWith("/api/agent/v1/ask")) {
    asked.push(JSON.parse(String(init?.body)));
    return new Response(`data: ${JSON.stringify({ type: "done", answer: ANSWER })}\n\n`, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function render(hash = "", picker = true) {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker={picker} />));
  await settle();
}

/** Lets fetches resolve and hashchange (a queued task in jsdom) fire. */
async function settle() {
  for (let i = 0; i < 3; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function key(k: string, opts: KeyboardEventInit = {}, target: EventTarget = window) {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...opts }));
  });
  await settle();
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const drawer = () => $("aside.ai-drawer");
const chip = () => $(".ai .chip")?.textContent;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  // wide enough for the drawer to open beside the page by default
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  asked.length = 0;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

describe("Home", () => {
  it("shows the watchlist with units, periods and sources", async () => {
    await render();
    expect($(".crumb")?.textContent).toBe("Home");
    const rows = [...container.querySelectorAll("table.watchlist tbody tr")];
    expect(rows).toHaveLength(2);
    const nvda = rows[0]!;
    expect(nvda.textContent).toContain("Nvidia");
    expect(nvda.textContent).toContain("FY2027 Q2");
    const revenue = [...nvda.querySelectorAll("td.n")].find((td) => td.textContent === "$130.50B");
    expect(revenue?.getAttribute("title")).toContain(`10-Q filed Aug 26, 2026, ${ACC}`);
    expect(container.querySelector("table.watchlist thead")?.textContent).toContain("Revenue TTM");
    // watching Tesla grants nothing: no values, and the reason on hover
    const tsla = rows[1]!;
    expect(tsla.querySelector("td.n")?.getAttribute("title")).toContain("grants nothing");
    expect(tsla.textContent).not.toMatch(/\$\d/);
  });

  it("fills the status strip, feed, expected filings and the sidebar from real data", async () => {
    await render();
    const strip = $(".status-strip")!.textContent!;
    expect(strip).toContain("2 companies");
    expect(strip).toContain("NVDA 10-Q ~Nov 19");
    expect(strip).toContain("+69.0%");
    expect(strip).toContain("Live");
    expect($(".feed")?.textContent).toContain("Nvidia");
    const feed = [...container.querySelectorAll(".feed li")];
    // Tesla's 8-K from the public index: listed, opened on its Filings tab (no grant to read it)
    expect(feed[0]!.textContent).toContain("Tesla");
    expect(feed[0]!.querySelector("a")?.getAttribute("href")).toBe(`#/company/${TSLA}/filings`);
    expect(feed[1]!.querySelector("a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${ACC}`);
    const index = vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"))
      .find((u) => u.pathname === "/api/query/v1/filing-index")!;
    expect(index.searchParams.getAll("company_id")).toEqual([NVDA, TSLA]);
    expect(index.searchParams.getAll("form")).toContain("8-K");
    expect(container.textContent).toContain("Expected filings");
    const side = [...container.querySelectorAll(".side .wl a")];
    expect(side.map((a) => a.querySelector("b")?.textContent)).toEqual(["NVDA", "TSLA"]);
    expect(side[0]!.querySelector("svg.spark")).not.toBeNull();
    expect(side[1]!.querySelector("svg.spark")).toBeNull();
  });

  it("removes a company from the watchlist through the Query API", async () => {
    await render();
    const remove = container.querySelector<HTMLButtonElement>(`button[aria-label="Remove Tesla from watchlist"]`)!;
    await act(async () => remove.click());
    await settle();
    const calls = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(String(calls[0]?.[0])).toBe(`/api/query/v1/watchlist/${TSLA}`);
  });
});

describe("navigation", () => {
  it("routes to every screen", async () => {
    await render("#/chart");
    expect($("h1")?.textContent).toBe("Chart builder");
    expect($('.nav a[aria-current="page"]')?.textContent).toContain("Chart builder");
    window.location.hash = "#/compare";
    await settle();
    expect($("h1")?.textContent).toBe("Compare");
    expect($('.nav a[aria-current="page"]')?.textContent).toContain("Compare");
    expect(container.textContent).toContain("Pick a target company");
    expect(container.textContent).not.toMatch(/TODO|Coming next/i);
    window.location.hash = "#/companies?margin=20";
    await settle();
    expect($("h1")?.textContent).toBe("Companies");
    expect($('.nav a[aria-current="page"]')?.textContent).toContain("Companies");
    window.location.hash = `#/company/${NVDA}`;
    await settle();
    expect($(".crumb")?.textContent).toBe("Companies/Nvidia");
    expect($('.nav a[aria-current="page"]')?.textContent).toContain("Companies");
  });

  it("switches screens with g then a letter", async () => {
    await render();
    await key("g");
    await key("c");
    expect(window.location.hash).toBe("#/companies");
    await key("g");
    await key("b");
    expect(window.location.hash).toBe("#/chart");
    await key("g");
    await key("h");
    expect(window.location.hash).toBe("#/");
  });

  it("opens the palette with Ctrl K, filters it and runs the chosen item", async () => {
    await render();
    await key("k", { ctrlKey: true });
    const input = $('.pal input') as HTMLInputElement;
    expect(document.activeElement).toBe(input);
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(input, "nvidia");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const items = [...container.querySelectorAll(".pal .it")].map((i) => i.textContent);
    expect(items[0]).toContain("Nvidia");
    expect(items.at(-1)).toContain("Ask the assistant: “nvidia”");
    await key("Enter", {}, input);
    expect($(".pal")).toBeNull();
    expect(window.location.hash).toBe(`#/company/${NVDA}`);

    await key("k", { metaKey: true });
    expect($(".pal")).not.toBeNull();
    await key("Escape", {}, $('.pal input')!);
    expect($(".pal")).toBeNull();
  });

  it("runs typed commands: compare tickers, chart a ticker's metric, and says when a ticker is unknown", async () => {
    await render();
    const type = async (text: string) => {
      await key("k", { ctrlKey: true });
      const input = $('.pal input') as HTMLInputElement;
      await act(async () => {
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
        set.call(input, text);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      return input;
    };
    let input = await type("compare nvda tsla");
    expect(container.querySelector(".pal .it")?.textContent).toContain("Compare NVDA, TSLA");
    await key("Enter", {}, input);
    await settle();
    expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${TSLA}`);

    input = await type("chart NVDA operating margin");
    expect(container.querySelector(".pal .it")?.textContent).toContain("Chart NVDA operating margin");
    await key("Enter", {}, input);
    await settle();
    expect(window.location.hash).toBe("#/chart");
    const saved = JSON.parse(window.localStorage.getItem("tributary.chart.alice") ?? "{}");
    expect(saved.layers?.[0]).toMatchObject({ companyId: NVDA, metric: "operating_margin", transform: "ttm" });

    input = await type("compare NVDA ZZZZ");
    await key("Enter", {}, input);
    await settle();
    expect(container.textContent).toContain("No company with the ticker ZZZZ.");
  });

  it("searches filings from the palette: Enter when nothing else matches", async () => {
    await render();
    await act(async () => container.querySelector<HTMLButtonElement>(".search-box")!.click());
    const input = $('.pal input') as HTMLInputElement;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(input, "export controls");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const items = [...container.querySelectorAll(".pal .it")].map((i) => i.textContent);
    expect(items[0]).toContain("Search filings for “export controls”");
    expect(items[1]).toContain("Ask the assistant");
    await key("Enter", {}, input);
    await settle();
    expect(window.location.hash).toBe("#/search?q=export+controls");
    expect($(".crumb")?.textContent).toBe("Search filings: “export controls”");
    // alice has no filings access anywhere: the page says so instead of searching
    expect(container.textContent).toContain("Searching filing text needs filings access");
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).includes("/v1/search"))).toBe(false);
  });
});

describe("assistant drawer", () => {
  it("toggles with Ctrl J, focuses with /, and stays the full page on Ask", async () => {
    await render();
    expect(drawer()).not.toBeNull();
    await key("j", { ctrlKey: true });
    expect(drawer()).toBeNull();
    expect($(".fab")).not.toBeNull();
    await key("/", {}, document.body);
    expect(drawer()).not.toBeNull();
    expect(document.activeElement).toBe($("aside.ai-drawer textarea"));
    expect(window.localStorage.getItem("tributary.assistant")).toBe("open");

    window.location.hash = "#/ask";
    await settle();
    expect(drawer()).toBeNull();
    expect($("aside.ai-page")).not.toBeNull();
  });

  it("follows the page: context chip and suggestions", async () => {
    await render();
    expect(chip()).toBe("Home · watchlist");
    window.location.hash = `#/company/${NVDA}`;
    await settle();
    expect(chip()).toBe("Nvidia · Overview");
    const prompts = () => [...container.querySelectorAll(".ai-s button")].map((b) => b.textContent);
    expect(prompts()).toContain("Summarize Nvidia's latest quarter.");
    window.location.hash = `#/company/${NVDA}/ratios`;
    await settle();
    expect(chip()).toBe("Nvidia · Ratios");
    expect(prompts()).toContain("Is Nvidia's leverage rising or falling?");
  });

  it("sends a suggestion to the agent and shows the answer with its citation", async () => {
    await render();
    const suggestion = container.querySelector<HTMLButtonElement>(".ai-s button")!;
    await act(async () => suggestion.click());
    await settle();
    expect(asked[0]?.question).toBe("What did my companies file recently?");
    const link = container.querySelector<HTMLAnchorElement>(".ai-m .answer a.accession");
    expect(link?.href).toBe(EDGAR);
    expect($(".ai-m .sources")?.textContent).toContain(ACC);
    // a section the tools returned opens the reader there; anything else is dropped
    const reader = [...container.querySelectorAll<HTMLAnchorElement>(".ai-m .sources a.reader-link")];
    expect(reader.map((a) => a.getAttribute("href"))).toEqual([`#/filing/${NVDA}/${ACC}/I-2`]);
  });
});

it("switches theme and remembers it per user", async () => {
  await render();
  expect(document.documentElement.dataset.theme).toBe("dark");
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Light")!;
  await act(async () => button.click());
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(window.localStorage.getItem("tributary.theme.alice")).toBe("light");
});

describe("narrow screens", () => {
  it("open the sidebar as a drawer from Menu; Escape, the scrim or a link close it", async () => {
    await render();
    const menu = $('button[aria-label="Menu"]')!;
    expect(menu.getAttribute("aria-controls")).toBe("side-nav");
    expect($("#side-nav")?.className).toBe("side");
    await act(async () => menu.click());
    expect($("#side-nav")?.className).toBe("side open");
    expect(menu.getAttribute("aria-expanded")).toBe("true");
    await key("Escape");
    expect($("#side-nav")?.className).toBe("side");
    await act(async () => menu.click());
    await act(async () => $(".scrim")!.click());
    expect($(".scrim")).toBeNull();
    await act(async () => menu.click());
    window.location.hash = "#/companies";
    await settle();
    expect($("#side-nav")?.className).toBe("side");
  });

  it("keys through the watchlist rows and opens one with Enter", async () => {
    await render();
    const rows = [...container.querySelectorAll<HTMLTableRowElement>("table.watchlist tbody tr")];
    rows[0]!.focus();
    await key("ArrowDown", {}, rows[0]!);
    expect(document.activeElement).toBe(rows[1]);
    await key("Enter", {}, rows[1]!);
    expect($(".crumb")?.textContent).toContain("Tesla");
  });
});
