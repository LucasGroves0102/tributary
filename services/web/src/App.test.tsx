import { clearChartCache } from "./chart/cache";
import { loadScreens } from "./pages/lazy";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ACC, entry, feedFiling, NVDA, revenueRow, TESLA, TSLA } from "./fixtures";
import { forgetVisits } from "./home/lastVisit";

const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials"] }],
};
const EDGAR = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/";
const APPLE = "0000320193";
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

/** Home with every card on: a returning user's arrangement, so the cards' own tests see them (F6
 * turned the status strip, Continue and Expected filings off by default; `homeDefault` sees that) */
let homeDefault = false;
const EVERY_CARD = { cards: ["status", "recent", "watchlist", "filings", "expected", "notes"].map((id) => ({ id, hidden: false })) };

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/views") && (init?.method ?? "GET") === "GET") {
    return Response.json({ views: { home: homeDefault ? null : EVERY_CARD, screener: null, watchlist: null },
      options: { home_cards: [], columns: [], max_columns: { screener: 12, watchlist: 8 } } });
  }
  if (path.endsWith("/v1/demo/users")) return Response.json({ users: ["alice", "bob"] });
  if (path.endsWith("/v1/demo/token")) return Response.json({ token: "t", expires_in: 3600 });
  if (path.endsWith("/v1/me")) return Response.json(ME);
  if (/\/v1\/watchlist\/\d+$/.test(path)) return Response.json({ company_id: path.split("/").at(-1), removed: true, added: true });
  if (path.endsWith("/v1/watchlist")) {
    return Response.json({ as_of: "2026-10-21T12:00:00Z", columns: [], expected_method: "", companies: [entry(), TESLA] });
  }
  if (path.endsWith("/v1/status")) {
    return Response.json({ status: "ok", as_of: "", last_accepted_at: null, last_ingested_at: new Date().toISOString() });
  }
  if (path.endsWith("/api/agent/v1/allowance")) {
    return Response.json({ per_hour: 30, network_per_hour: 40, left_this_hour: 28, paused: false, per_day: 1000, left_today: 998, report_questions: 3, check_questions: 4 });
  }
  if (path.endsWith("/v1/filings/latest")) {
    return Response.json({ as_of: "", filings: [feedFiling({ ingested_at: new Date().toISOString() })] });
  }
  if (path.endsWith("/v1/filing-index")) {
    return Response.json({ as_of: "", forms: [], from: null, to: null, limit: 50, offset: 0, has_more: false, filings: [{
      accession_no: "0001318605-26-000099", company_id: TSLA, name: "Tesla", form: "8-K", filed: new Date().toISOString().slice(0, 10),
      accepted_at: null, period_of_report: null, filing_url: null, document_url: null, sections: null, sections_available: false }] });
  }
  if (path.endsWith("/v1/data-notes")) {
    return Response.json({ as_of: "", checked_at: null, companies: 1, notes: [{
      company_id: NVDA, kind: "restatement", severity: "info", metric: "revenue", period_end: "2025-04-27", date: "2026-02-26",
      detail: { original_filed: "2025-05-28", restated_filed: "2026-02-26", change: 0.0123 }, checked_at: null,
      filings: [{ role: "restated", accession_no: ACC, form: "10-K", filed: "2026-02-26", filing_url: EDGAR }] }] });
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
  if (path.endsWith("/v1/screener")) {
    // identity for any id asked (companies/directory.ts); Apple is on no list of this user's
    const ids = new URL(url, "http://x").searchParams.getAll("company_id");
    return Response.json({ as_of: "", total: ids.length, limit: 50, offset: 0, sort: "name", columns: [], rows: ids.map((id) => ({
      company_id: id, name: id === APPLE ? "Apple Inc." : "Nvidia", tickers: [id === APPLE ? "AAPL" : "NVDA"], exchanges: [], sic: null,
      industry: null, fiscal_year_end: null, filer_category: null, filer_categories: [], state_of_incorporation: null,
      values: {}, sources: {}, access: [] })) });
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

// the lazily loaded screens (pages/lazy.ts), ready before the first render
beforeAll(() => loadScreens());

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  // a returning user who keeps the assistant open (a first visit starts closed: its own test)
  window.localStorage.setItem("tributary.assistant", "open");
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  asked.length = 0;
  homeDefault = false;
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
    expect(strip).toContain("Newest filing listed");
    expect(strip).toContain("just now");
    expect(strip).toContain("newest filing text parsed just now");
    expect(strip).not.toContain("Live");
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

  it("lists recent data notes for companies with financials, linked to their Data notes tab", async () => {
    await render();
    const card = [...container.querySelectorAll("section.card")].find((c) => c.querySelector("h2")?.textContent === "Recent data notes for your companies")!;
    expect(card.querySelector(".note")?.textContent).toContain("Nvidia (NVDA) · Revenue restated · Feb 26, 2026");
    expect(card.querySelector(".note a")?.getAttribute("href")).toBe(`#/company/${NVDA}/notes`);
    // alice reads NVDA's financials, not its filings: the filing opens on EDGAR
    expect(card.querySelector(".note-f a")?.getAttribute("href")).toBe(EDGAR);
  });

  it("removes a company from the watchlist through the Query API", async () => {
    await render();
    const remove = container.querySelector<HTMLButtonElement>(`button[aria-label="Remove Tesla from watchlist"]`)!;
    await act(async () => remove.click());
    await settle();
    const calls = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "DELETE");
    expect(String(calls[0]?.[0])).toBe(`/api/query/v1/watchlist/${TSLA}`);
  });

  it("offers to undo a removal, which puts the company back", async () => {
    await render();
    await act(async () => container.querySelector<HTMLButtonElement>(`button[aria-label="Remove Tesla from watchlist"]`)!.click());
    await settle();
    const note = $(".undo-note")!;
    expect(note.textContent).toContain("Removed Tesla from your watchlist");
    await act(async () => note.querySelector("button")!.click());
    await settle();
    const puts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PUT").map(([u]) => String(u));
    expect(puts).toContain(`/api/query/v1/watchlist/${TSLA}`);
    expect($(".undo-note")).toBeNull();
  });

  it("doesn't say 0 new filings while they're still loading (L6)", async () => {
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request, init?: RequestInit) =>
      String(url).includes("/v1/filings/latest") ? new Promise<Response>(() => undefined) : respond(String(url), init));
    await render();
    const tile = [...container.querySelectorAll(".status-strip .k")].find((k) => k.textContent?.startsWith("New "))!;
    const v = tile.parentElement!.querySelector(".v")!;
    expect(v.textContent).toBe("…");
    expect(tile.parentElement!.textContent).not.toContain("0 filings");
  });

  it("says the same thing about new filings wherever it counts them", async () => {
    await render();
    const badge = $(".nav .cnt.new")!;
    const tile = [...container.querySelectorAll(".status-strip .k")].find((k) => k.textContent?.startsWith("New "))!;
    const label = tile.textContent!.replace(/^New /, "");
    expect(badge.getAttribute("title")).toContain(label);
    expect(label).toBe("in the last 7 days"); // a first visit in this browser, not "your last visit"
    expect(badge.getAttribute("title")).toContain(`${badge.textContent} new`);
    expect(tile.parentElement!.querySelector(".v")?.textContent).toContain(badge.textContent!);
  });

  it("F6 (#127): with nothing saved, Home starts with Start an analysis, then the watchlist, new filings and notes", async () => {
    homeDefault = true;
    await render();
    const start = $(".start-card")!;
    expect(start.querySelector("h2")?.textContent).toBe("Start an analysis");
    expect(start.querySelector('input[aria-label="Company to analyse"]')).not.toBeNull();
    expect([...container.querySelectorAll(".page > .card h2, .page > section.card h2")].map((h) => h.textContent?.split(" ")[0]))
      .toEqual(["Start", "Watchlist", "New", "Recent"]);
    expect($(".status-strip")).toBeNull();
    expect(container.textContent).not.toContain("Expected filings");
    // one primary way in: the other actions are quiet
    expect(container.querySelectorAll(".page-bar .btn.primary")).toHaveLength(0);
  });

  it("counts every readable company in the feed's heading with the catalog open", async () => {
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request, init?: RequestInit) =>
      String(url).endsWith("/v1/me")
        ? Response.json({ ...ME, companies: [], catalog: { public: true, tiers: ["financials", "filings"], companies: 6039 } })
        : respond(String(url), init));
    await render();
    expect(container.textContent).toContain("New filings for the 6,039 companies you can read");
  });
});

describe("new chart", () => {
  it("Home's New chart starts empty, and the draft it replaced can be restored", async () => {
    await render("#/chart");
    const box = () => container.querySelector<HTMLInputElement>('input[aria-label="Chart title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box(), "My draft");
      box().dispatchEvent(new Event("input", { bubbles: true }));
    });
    window.location.hash = "#/";
    await settle();
    await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "New chart")!.click());
    await settle();
    expect($("h1")?.textContent).toBe("Chart builder");
    expect(box().value).toBe("");
    const restore = [...container.querySelectorAll(".undo-note button")].find((b) => b.textContent === "Restore the previous draft")!;
    await act(async () => (restore as HTMLElement).click());
    await settle();
    expect(box().value).toBe("My draft");
    expect($(".undo-note")).toBeNull();
    // the builder's own New: the same thing, offered there too
    await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "New chart")!.click());
    await settle();
    expect(box().value).toBe("");
  });

  it("a template over your layers says so, and Restore brings them back", async () => {
    await render("#/chart");
    const box = () => container.querySelector<HTMLInputElement>('input[aria-label="Chart title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box(), "Mine");
      box().dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent === "Margins")!.click());
    await settle();
    expect(box().value).toBe("");
    expect($(".undo-note")?.textContent).toContain("Replaced your layers with the Margins template.");
    await act(async () => [...container.querySelectorAll<HTMLElement>(".undo-note button")].find((b) => b.textContent === "Restore the previous draft")!.click());
    await settle();
    expect(box().value).toBe("Mine");
    expect($(".undo-note")).toBeNull();
  });

  it("the nav's Chart builder still reopens the draft", async () => {
    await render("#/chart");
    const box = () => container.querySelector<HTMLInputElement>('input[aria-label="Chart title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box(), "Keep me");
      box().dispatchEvent(new Event("input", { bubbles: true }));
    });
    window.location.hash = "#/";
    await settle();
    window.location.hash = "#/chart";
    await settle();
    expect(box().value).toBe("Keep me");
  });
});

it("names Settings in the breadcrumb", async () => {
  await render("#/settings");
  expect($(".crumb [aria-current]")?.textContent).toBe("Settings");
});

it("#60: links Settings from the sidebar even with no data connections offered", async () => {
  await render("#/");
  const link = [...document.querySelectorAll<HTMLAnchorElement>(".side-links a")].find((a) => a.textContent === "Settings");
  expect(link?.getAttribute("href")).toBe("#/settings");
  expect(link?.title).toBe("Filing alerts");
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
    expect($(".crumb")?.textContent).toBe("Companies/Nvidia (NVDA)");
    expect($('.nav a[aria-current="page"]')?.textContent).toContain("Companies");
  });

  it("the browser tab says what's on screen", async () => {
    await render("#/companies");
    expect(document.title).toBe("Companies · Tributary");
    window.location.hash = `#/company/${NVDA}/statements`;
    await settle();
    expect(document.title).toBe("Nvidia (NVDA) · Statements · Tributary");
    window.location.hash = "#/status";
    await settle();
    expect(document.title).toBe("Data status · Tributary");
  });

  it("keeps the shell when a page fails to render", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (new URL(String(url), "http://x").pathname.endsWith("/v1/data-notes")) return Response.json({ notes: "not a list", checked_at: null, companies: 1 });
      return respond(String(url), init);
    });
    await render();
    expect($(".content [role=alert] h1")?.textContent).toBe("This page didn't load");
    expect($(".side")).not.toBeNull();
    window.location.hash = "#/about";
    await settle();
    expect($(".content h1")?.textContent).toBe("What Tributary covers");
    vi.restoreAllMocks();
  });

  it("shows no demo badge on a real account or a persona", async () => {
    await render();
    expect($(".brand .env")).toBeNull();
    expect($(".side .fresh")?.textContent).toContain("Signed in as alice");
  });

  it("says a filing's text is parsed about a minute after EDGAR, not hours (live fetch since 2026-11-09)", async () => {
    await render();
    const title = $(".side .fresh")?.getAttribute("title") ?? "";
    expect(title).toContain("about a minute later for a 10-K, 10-Q or material 8-K");
    expect(title).not.toContain("6 hours for the rest");
  });

  it("names a company it knows only by id: breadcrumb and assistant, never the CIK", async () => {
    await render(`#/company/${APPLE}`);
    // the watchlist and /v1/me don't hold Apple: the name comes from the shared directory
    await settle();
    expect($(".crumb")?.textContent).toBe("Companies/Apple (AAPL)");
    expect(chip()).toBe("Apple (AAPL) · Overview");
    expect($(".top")?.textContent).not.toContain(APPLE);
    expect($(".ai")?.textContent).not.toContain(APPLE);
    expect($(".ai-s")?.textContent).toContain("Apple");
  });

  it("? shows the keyboard shortcut sheet; Escape closes it and focus returns", async () => {
    await render();
    const before = document.activeElement;
    await key("?");
    const sheet = $('[role="dialog"]')!;
    expect(sheet.getAttribute("aria-modal")).toBe("true");
    expect(sheet.textContent).toContain("Keyboard shortcuts");
    expect(sheet.textContent).toContain("First or last column");
    expect(sheet.textContent).toContain("Go to Chart builder");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close keyboard shortcuts");
    await key("Escape", {}, document.activeElement!);
    expect($('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(before);
    // also from the sidebar footer
    const button = [...container.querySelectorAll(".side button")].find((b) => b.textContent?.startsWith("Keyboard shortcuts"))!;
    await act(async () => (button as HTMLElement).click());
    expect($('[role="dialog"]')).not.toBeNull();
    await act(async () => ($(".sheet-overlay") as HTMLElement).click());
    expect($('[role="dialog"]')).toBeNull();
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
    expect(window.localStorage.getItem("tributary.assistant")).toBe("closed");
    await key("/", {}, document.body);
    expect(drawer()).not.toBeNull();
    expect(document.activeElement).toBe($("aside.ai-drawer textarea"));
    expect(window.localStorage.getItem("tributary.assistant")).toBe("open");

    window.location.hash = "#/ask";
    await settle();
    expect(drawer()).toBeNull();
    expect($("aside.ai-page")).not.toBeNull();
  });

  it("starts closed on a first visit, whatever the width, with one way in that covers nothing", async () => {
    window.localStorage.clear();
    await render();
    expect(drawer()).toBeNull();
    // no floating button over the page: the top bar's Assistant is the way in
    expect($(".fab")).toBeNull();
    const open = [...container.querySelectorAll<HTMLButtonElement>(".top button")].find((b) => b.textContent?.includes("Assistant"))!;
    expect(open.getAttribute("aria-pressed")).toBe("false");
    act(() => open.click());
    await settle();
    expect(drawer()).not.toBeNull();
    expect(window.localStorage.getItem("tributary.assistant")).toBe("open");
  });

  it("shows the questions left before one is spent, and looks again after one is", async () => {
    await render();
    await settle();
    expect($(".ai-meter")?.textContent).toBe("28 of 30 questions left this hour · 998 of 1,000 today");
    expect($(".ai-meter")?.getAttribute("title")).toMatch(/30 questions an hour.*1,000 a day.*midnight UTC/);
    const asks = () => vi.mocked(fetch).mock.calls.filter(([u]) => String(u).endsWith("/v1/allowance")).length;
    const before = asks();
    await act(async () => container.querySelector<HTMLButtonElement>(".ai-s button")!.click());
    await settle();
    expect(asks()).toBe(before + 1);
  });

  it("a question a page wrote for you leaves when you do, unless you edited it", async () => {
    await render(`#/company/${NVDA}`);
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".co-head button")].find((b) => b.textContent === "Ask")!.click());
    await settle();
    const box = () => $("textarea") as HTMLTextAreaElement;
    expect(box().value).toContain("latest quarter");
    window.location.hash = "#/companies";
    await settle();
    expect(box().value).toBe("");
  });

  it("follows the page: context chip and suggestions", async () => {
    await render();
    expect(chip()).toBe("Home · watchlist");
    window.location.hash = `#/company/${NVDA}`;
    await settle();
    expect(chip()).toBe("Nvidia (NVDA) · Overview");
    const prompts = () => [...container.querySelectorAll(".ai-s button")].map((b) => b.textContent);
    expect(prompts()).toContain("Summarize Nvidia's latest quarter.");
    window.location.hash = `#/company/${NVDA}/ratios`;
    await settle();
    expect(chip()).toBe("Nvidia (NVDA) · Ratios");
    expect(prompts()).toContain("Is Nvidia's leverage rising or falling?");
  });

  it("sends a suggestion to the agent and shows the answer with its citation", async () => {
    await render();
    const suggestion = container.querySelector<HTMLButtonElement>(".ai-s button")!;
    await act(async () => suggestion.click());
    await settle();
    expect(asked[0]?.question).toBe("What did Nvidia and Tesla file recently?");
    const link = container.querySelector<HTMLAnchorElement>(".ai-m .answer a.accession");
    expect(link?.href).toBe(EDGAR);
    // two links that say which is which: the section here, the filing on EDGAR
    const edgar = $(".ai-m .sources a.edgar-link") as HTMLAnchorElement;
    expect(edgar.textContent).toBe("EDGAR ↗");
    expect(edgar.href).toBe(EDGAR);
    expect(edgar.title).toContain(ACC);
    // a section the tools returned opens the reader there; anything else is dropped
    const reader = [...container.querySelectorAll<HTMLAnchorElement>(".ai-m .sources a.reader-link")];
    expect(reader.map((a) => a.getAttribute("href"))).toEqual([`#/filing/${NVDA}/${ACC}/I-2`]);
    expect(reader[0]!.textContent).toBe("Read MD&A");
    // the answer's own footer is about the answer: no model name, tokens or cost until asked for
    const meta = $(".ai-m .meta") as HTMLElement;
    expect(meta.textContent).toContain("Newest filing cited: Aug 26, 2026");
    expect(meta.querySelector("details:not([open]) summary")?.textContent).toBe("Details");
    expect(meta.querySelector("details")?.textContent).toContain("claude-test");
  });
});

it("a named user keeps the full index: other companies' pages, search and unlabelled tests", async () => {
  await render(`#/company/${TSLA}`);
  // Tesla isn't readable for alice: its page still opens, with metadata only
  expect(container.querySelector(".not-in-demo")).toBeNull();
  expect(container.textContent).not.toContain("in this demo");
  window.location.hash = "#/ask";
  await settle();
  // no test strings among the prompts: the adversarial suite has them, the product doesn't
  const items = [...container.querySelectorAll(".ai-s > *")].map((e) => e.textContent).join(" ");
  expect(items).not.toMatch(/I'm bob|SYSTEM:|Try to break it/);
  await key("k", { ctrlKey: true });
  const input = $(".pal input") as HTMLInputElement;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, "TSLA");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => new Promise((r) => setTimeout(r, 250)));
  await settle();
  const search = vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"))
    .find((u) => u.pathname.endsWith("/v1/companies/search"))!;
  expect(search.searchParams.get("access")).toBeNull();
});

it("says what Tributary covers, from the sidebar footer and the assistant's empty state", async () => {
  await render();
  expect($(".side .cover-link")?.getAttribute("href")).toBe("#/about");
  expect([...container.querySelectorAll(".ai-m .empty a")].map((a) => a.getAttribute("href"))).toContain("#/about");
  expect($(".ai-m .empty")?.textContent).toContain("consensus estimates");
  window.location.hash = "#/about";
  await settle();
  expect($("h1")?.textContent).toBe("What Tributary covers");
  expect($(".crumb")?.textContent).toBe("What Tributary covers");
  const text = $(".about")!.textContent!;
  for (const limit of ["No stock prices or market data", "No consensus estimates", "No earnings-call transcripts",
    "Expected filing dates are estimates", "n/a (bank)", "5:30 pm ET",
    "A model gives market cap, P/E and EV multiples from a price you type"]) expect(text).toContain(limit);
  expect(text).not.toContain("So no market cap");
  expect($(".side .cover-link")?.getAttribute("aria-current")).toBe("page");
});

it("links the data status page from the sidebar footer and shows it in the shell", async () => {
  await render();
  const link = [...container.querySelectorAll(".side .cover-link")].find((a) => a.textContent?.startsWith("Data status"));
  expect(link?.getAttribute("href")).toBe("#/status");
  window.location.hash = "#/status";
  await settle();
  expect($("h1")?.textContent).toBe("Data status");
  expect($(".crumb")?.textContent).toBe("Data status");
  expect(link?.getAttribute("aria-current")).toBe("page");
  expect($(".status-home")).toBeNull();
});

it("switches theme and remembers it per user", async () => {
  await render();
  expect(document.documentElement.dataset.theme).toBe("dark");
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Light")!;
  await act(async () => button.click());
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(window.localStorage.getItem("tributary.theme.alice")).toBe("light");
});

describe("the sidebar", () => {
  it("keeps the logo and screens fixed, the lists scrolling and the footer whole", async () => {
    await render();
    const part = (s: string) => $(`.side > ${s}`)!;
    expect(part(".side-top").querySelector("nav.nav")).not.toBeNull();
    expect(part(".side-top").querySelector(".brand")).not.toBeNull();
    expect(part(".side-lists").querySelector(".wl")).not.toBeNull();
    expect(part(".side-foot").querySelector(".fresh")).not.toBeNull();
    expect(part(".side-foot").querySelector(".cover-link")).not.toBeNull();
    // nothing outside those three: nothing else scrolls with the lists
    expect($(".side")!.children.length).toBe(3);
  });

  it("opens at the top of its lists, wherever they were left", async () => {
    await render();
    const lists = $(".side-lists")!;
    lists.scrollTop = 400;
    expect(lists.scrollTop).toBe(400);
    await act(async () => $('button[aria-label="Menu"]')!.click());
    expect(lists.scrollTop).toBe(0);
  });
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
