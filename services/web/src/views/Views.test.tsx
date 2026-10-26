import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, NVDA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";

const ME = { user_id: "alice", companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials"] }] };
const COLUMNS = [
  { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" },
  { key: "revenue_growth_ttm", metric: "revenue_growth", span: "ttm", unit: "ratio" },
  { key: "operating_margin_ttm", metric: "operating_margin", span: "ttm", unit: "ratio" },
  { key: "free_cash_flow_ttm", metric: "free_cash_flow", span: "ttm", unit: "USD" },
  { key: "net_debt_q", metric: "net_debt", span: "quarter", unit: "USD" },
  { key: "net_income_ttm", metric: "net_income", span: "ttm", unit: "USD" },
];

let saved: Record<string, unknown>;
let available: boolean;

function respond(url: string, init?: RequestInit): Response {
  const u = new URL(url, "http://x");
  const path = u.pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  if (path === "/v1/demo/users") return Response.json({ users: ["alice"] });
  if (path === "/v1/demo/token") return Response.json({ token: "t" });
  if (path === "/v1/me") return Response.json(ME);
  if (path === "/v1/views" && method === "GET") {
    if (!available) return Response.json({ detail: "preferences unavailable" }, { status: 503 });
    return Response.json({
      views: { home: null, screener: null, watchlist: null, ...saved },
      options: { home_cards: [], columns: COLUMNS, max_columns: { screener: 12, watchlist: 8 } },
    });
  }
  const m = path.match(/^\/v1\/views\/(home|screener|watchlist)$/);
  if (m && method === "PUT") {
    saved[m[1]!] = JSON.parse(String(init!.body)).state;
    return Response.json({ view: m[1], state: saved[m[1]!] });
  }
  if (m && method === "DELETE") {
    delete saved[m[1]!];
    return Response.json({ view: m[1], reset: true });
  }
  if (path === "/v1/watchlist") {
    const e = entry();
    return Response.json({ as_of: "", columns: [], expected_method: "", companies: [{ ...e, values: { ...e.values, net_income_ttm: "72880000000" } }] });
  }
  if (path === "/v1/screener") return Response.json({ total: 1, columns: [], rows: [{ ...entry(), filer_categories: null, state_of_incorporation: null, sources: {}, values: { net_income_ttm: "72880000000" } }] });
  if (path === "/v1/workspaces") return Response.json({ workspaces: [] });
  if (path === "/v1/recent") return Response.json({ items: [], filings: [] });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  // lazy pages (pages/lazy.ts): a slow runner can still be loading the chunk after the ticks
  await act(async () => { await vi.dynamicImportSettled(); });
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(hash: string, ready = "h1") {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  for (let i = 0; i < 40 && !container.querySelector(ready); i++) await settle();
  await settle();
}

const byLabel = (label: string) => container.querySelector<HTMLElement>(`[aria-label="${label}"]`)!;
const button = (name: string) =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name)!;
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  await settle();
};
const choose = async (select: HTMLSelectElement, value: string) => {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
};
const headings = () => [...container.querySelectorAll(".page h2")].map((h) => h.textContent?.trim().split(" ")[0]);
const requests = (path: string) =>
  vi.mocked(fetch).mock.calls.filter(([u]) => String(u).includes(path)).map(([u, init]) => ({ url: new URL(String(u), "http://x"), init }));

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  saved = {};
  available = true;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("Home: hide and reorder cards, saved for the user, and reset", async () => {
  await render("#/", "table.watchlist");
  expect(headings()).toEqual(["Continue", "Watchlist", "New", "Expected", "Recent"]);
  expect(container.querySelector(".status-strip")).not.toBeNull();
  await click(byLabel("Customize Home"));
  await click(container.querySelector<HTMLInputElement>('.view-editor input[type="checkbox"]')!); // the status strip
  await click(byLabel("Move Watchlist up"));
  await click(byLabel("Move Watchlist up"));
  expect(container.querySelector(".status-strip")).toBeNull();
  expect(headings().slice(0, 3)).toEqual(["Customize", "Watchlist", "Continue"]);
  expect(saved.home).toEqual({ cards: [
    { id: "watchlist", hidden: false }, { id: "status", hidden: true }, { id: "recent", hidden: false },
    { id: "filings", hidden: false }, { id: "expected", hidden: false }, { id: "notes", hidden: false },
  ] });
  // it comes back on the next visit
  act(() => root.unmount());
  await render("#/", "table.watchlist");
  expect(headings().slice(0, 2)).toEqual(["Watchlist", "Continue"]);
  await click(byLabel("Customize Home"));
  await click(button("Reset to default"));
  expect(saved.home).toBeUndefined();
  expect(container.querySelector(".status-strip")).not.toBeNull();
});

it("watchlist and screener columns: choose, order, saved, read from the API", async () => {
  await render("#/", "table.watchlist");
  const picker = container.querySelector<HTMLButtonElement>(".card .col-picker button")!;
  await click(picker);
  await choose(byLabel("Add a watchlist column") as HTMLSelectElement, "net_income_ttm");
  expect(saved.watchlist).toEqual({ columns: ["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q", "net_income_ttm"] });
  await click(byLabel("Move Net income (TTM) left"));
  await click(byLabel("Remove Free cash flow (TTM)"));
  expect(saved.watchlist).toEqual({ columns: ["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm", "net_income_ttm", "net_debt_q"] });
  const last = requests("/v1/watchlist").at(-1)!.url;
  expect(last.searchParams.getAll("columns")).toEqual(["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm", "net_income_ttm", "net_debt_q"]);
  const head = [...container.querySelectorAll("table.watchlist th")].map((t) => t.textContent);
  expect(head).toContain("Net income TTM");
  expect(container.querySelector("table.watchlist")?.textContent).toContain("$72.88B");

  act(() => root.unmount());
  saved.screener = { columns: ["net_income_ttm", "revenue_ttm"] };
  await render("#/companies", "table.screen");
  const params = requests("/v1/screener").at(-1)!.url.searchParams;
  expect(params.getAll("columns")).toEqual(["net_income_ttm", "revenue_ttm"]);
  const th = [...container.querySelectorAll("table.screen th")].map((t) => t.textContent);
  expect(th.slice(2, 4)).toEqual(["Net income TTM", "Revenue TTM ↓"]); // sorted by revenue
  await click(container.querySelector<HTMLButtonElement>(".col-picker button")!);
  await click(button("Reset"));
  expect(saved.screener).toBeUndefined();
  expect(requests("/v1/screener").at(-1)!.url.searchParams.getAll("columns")).toHaveLength(7);
});

it("without the preferences service, the defaults show and nothing is editable", async () => {
  available = false;
  await render("#/", "table.watchlist");
  expect(container.querySelector('[aria-label="Customize Home"]')).toBeNull();
  expect(container.querySelector(".col-picker")).toBeNull();
  expect(headings()).toEqual(["Continue", "Watchlist", "New", "Expected", "Recent"]);
});
