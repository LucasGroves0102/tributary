import { clearChartCache } from "../chart/cache";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { entry, NVDA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";
import type { SavedItemFull, SavedWorkspace, UiAction } from "../types";

const AAPL = "0000320193";
const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials"] }],
};
const COLUMNS = {
  columns: [{ key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" }],
  filer_categories: [],
  bands: [],
};
const NVDA_CHART = {
  title: "NVDA revenue", range: 3, align: "calendar",
  layers: [{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L", hidden: false }],
};
// saved while alice could still read Apple; she can't now
const AAPL_CHART = { ...NVDA_CHART, title: "AAPL revenue", layers: [{ ...NVDA_CHART.layers[0], companyId: AAPL }] };

/** The saved-work API in memory, as the Query API answers it (for alice). */
let db: { workspaces: SavedWorkspace[]; items: Map<string, SavedItemFull>; recentFilings: object[] };
let seq = 0;
const now = () => new Date(Date.UTC(2026, 9, 21, 12, 0, seq++)).toISOString();

function seed() {
  db = { workspaces: [], items: new Map(), recentFilings: [] };
  const ws: SavedWorkspace = { workspace_id: "w1", name: "Semis", created_at: now(), updated_at: now(), items: [] };
  db.workspaces.push(ws);
  for (const [id, name, state] of [["i1", "NVDA revenue", NVDA_CHART], ["i2", "Lost access", AAPL_CHART]] as const) {
    const item: SavedItemFull = {
      item_id: id, workspace_id: "w1", kind: "chart", name, created_at: now(), updated_at: now(), opened_at: null,
      state: state as Record<string, unknown>,
    };
    db.items.set(id, item);
  }
  const comps: SavedItemFull = {
    item_id: "i3", workspace_id: "w1", kind: "comps", name: "NVDA comps", created_at: now(), updated_at: now(),
    opened_at: null, state: { target: NVDA, peers: [AAPL] },
  };
  db.items.set("i3", comps);
}

const summary = ({ state: _s, workspace_name: _w, ...rest }: SavedItemFull) => rest;
const listed = () =>
  db.workspaces.map((w) => ({ ...w, items: [...db.items.values()].filter((i) => i.workspace_id === w.workspace_id).map(summary) }));
const nameOf = (id: string) => db.workspaces.find((w) => w.workspace_id === id)?.name ?? "";
const notFound = () => Response.json({ detail: "item not found" }, { status: 404 });

let script: UiAction[] = [];

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  if (path === "/v1/demo/users") return Response.json({ users: ["alice"] });
  if (path === "/v1/demo/token") return Response.json({ token: "t" });
  if (path === "/v1/me") return Response.json(ME);
  if (path === "/v1/watchlist") return Response.json({ as_of: "", columns: [], expected_method: "", companies: [entry()] });
  if (path === "/v1/screener/columns") return Response.json(COLUMNS);
  if (path === "/v1/screener") {
    return Response.json({ total: 1, columns: [], rows: [{ ...entry(), filer_categories: null, state_of_incorporation: null, sources: {}, values: {} }] });
  }
  if (path === "/v1/formulas/evaluate") {
    if (body.default_company === AAPL) {
      return Response.json({ detail: { errors: [{ code: "company", message: "AAPL isn't one of your companies", pos: 0, length: 4, hint: null }] } }, { status: 403 });
    }
    return Response.json({ formula: body.formula, unit: "USD", basis: body.basis, view: body.view, alignment: body.basis, as_of: "", inputs: [], points: [] });
  }
  if (path === "/v1/workspaces" && method === "GET") return Response.json({ workspaces: listed() });
  if (path === "/v1/workspaces" && method === "POST") {
    const w = { workspace_id: `w${++seq}`, name: body.name, created_at: now(), updated_at: now(), items: [] };
    db.workspaces.push(w);
    return Response.json(w, { status: 201 });
  }
  let m = path.match(/^\/v1\/workspaces\/([^/]+)(\/items|\/duplicate)?$/);
  if (m) {
    const w = db.workspaces.find((x) => x.workspace_id === m![1]);
    if (!w) return Response.json({ detail: "workspace not found" }, { status: 404 });
    if (m[2] === "/items") {
      const item: SavedItemFull = {
        item_id: `i${++seq}`, workspace_id: w.workspace_id, kind: body.kind, name: body.name, created_at: now(),
        updated_at: now(), opened_at: null, state: body.state,
      };
      db.items.set(item.item_id, item);
      return Response.json(item, { status: 201 });
    }
    if (method === "PATCH") {
      w.name = body.name;
      return Response.json(w);
    }
    if (method === "DELETE") {
      db.workspaces = db.workspaces.filter((x) => x !== w);
      for (const [k, i] of db.items) if (i.workspace_id === w.workspace_id) db.items.delete(k);
      return Response.json({ deleted: true });
    }
  }
  m = path.match(/^\/v1\/workspace-items\/([^/]+)(\/open|\/duplicate)?$/);
  if (m) {
    const item = db.items.get(m[1]!);
    if (!item) return notFound();
    if (m[2] === "/open") {
      item.opened_at = now();
      return Response.json({ ...item, workspace_name: nameOf(item.workspace_id) });
    }
    if (m[2] === "/duplicate") {
      const copy = { ...item, item_id: `i${++seq}`, name: `${item.name} (copy)`, opened_at: null };
      db.items.set(copy.item_id, copy);
      return Response.json(copy, { status: 201 });
    }
    if (method === "PUT") {
      if (body.name) item.name = body.name;
      if (body.state) item.state = body.state;
      return Response.json(item);
    }
    if (method === "DELETE") {
      db.items.delete(item.item_id);
      return Response.json({ deleted: true });
    }
  }
  if (path === "/v1/recent") {
    const items = [...db.items.values()]
      .filter((i) => i.opened_at)
      .sort((a, b) => b.opened_at!.localeCompare(a.opened_at!))
      .map((i) => ({ ...summary(i), workspace_name: nameOf(i.workspace_id) }));
    return Response.json({ items, filings: db.recentFilings });
  }
  if (path.endsWith("/api/agent/v1/ask")) {
    const events = [
      ...script.map((action) => ({ type: "action", round: 0, action })),
      { type: "done", answer: { text: "Done.", stop: "end_turn", model: "m", rounds: 1, tool_calls: [], usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, cost_usd: null, citations: [], verification: null, actions: script } },
    ];
    return new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  // lazy pages (pages/lazy.ts): a slow runner can still be loading the chunk after the ticks
  await act(async () => { await vi.dynamicImportSettled(); });
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(hash: string) {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  await settle();
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const buttons = (text: string | RegExp) =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].filter((b) =>
    typeof text === "string" ? b.textContent === text : text.test(b.textContent ?? "") || text.test(b.getAttribute("aria-label") ?? ""),
  );
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  await settle();
};
const sidebarItem = (name: string) =>
  [...container.querySelectorAll<HTMLButtonElement>(".side .ws-row.it .ws-open")].find((b) => b.textContent?.includes(name))!;
const type = async (input: HTMLInputElement, value: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};
const calls = (method: string) =>
  vi.mocked(fetch).mock.calls.filter(([, init]) => (init?.method ?? "GET") === method).map(([u, init]) => [String(u), init?.body ? JSON.parse(String(init.body)) : undefined]);

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  seq = 0;
  script = [];
  seed();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("lists workspaces as sidebar folders and opens an item with its state", async () => {
  await render("#/");
  expect($(".side .ws-row.fd")?.textContent).toContain("Semis");
  expect([...container.querySelectorAll(".side .ws-row.it")].map((r) => r.querySelector(".ws-name")?.textContent)).toEqual([
    "NVDA revenue", "Lost access", "NVDA comps",
  ]);
  await click(sidebarItem("NVDA revenue"));
  expect(calls("POST").map(([u]) => u)).toContain("/api/query/v1/workspace-items/i1/open");
  expect(window.location.hash).toBe("#/chart");
  expect($("h1")?.textContent).toBe("Chart builder");
  expect($(".savebar .saved-as")?.textContent).toBe("Semis / NVDA revenue · saved");
  // the comps open on Compare with their target and peers in the URL
  await click(sidebarItem("NVDA comps"));
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${AAPL}`);
});

it("marks unsaved changes and saves over the open item", async () => {
  await render("#/");
  await click(sidebarItem("NVDA revenue"));
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="Transform"]')!;
  await act(async () => {
    select.value = "yoy";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
  expect($(".savebar .dirty")?.textContent).toContain("unsaved changes");
  await click(buttons("Save")[0]!);
  const [url, body] = calls("PUT").at(-1)!;
  expect(url).toBe("/api/query/v1/workspace-items/i1");
  expect(body.state.layers[0]).toMatchObject({ companyId: NVDA, transform: "yoy" });
  expect(body.state.layers[0]).not.toHaveProperty("id");
  expect($(".savebar .dirty")).toBeNull();
});

it("saves comps into a new workspace with Save as", async () => {
  await render(`#/compare?target=${NVDA}&peers=${AAPL}`);
  await click(buttons("Save…")[0]!);
  const form = $(".save-form")!;
  const [name] = form.querySelectorAll<HTMLInputElement>("input");
  expect(name!.value).toBe("Nvidia comps");
  await type(name!, "Chips comps");
  const select = form.querySelector<HTMLSelectElement>("select")!;
  await act(async () => {
    select.value = "__new__";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await type(form.querySelectorAll<HTMLInputElement>("input")[1]!, "Chips");
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle();
  const posts = calls("POST").map(([u, b]) => [u, b]);
  expect(posts).toContainEqual(["/api/query/v1/workspaces", { name: "Chips" }]);
  const item = posts.find(([u]) => String(u).endsWith("/items"))!;
  expect(item[1]).toEqual({ kind: "comps", name: "Chips comps", state: { target: NVDA, peers: [AAPL] } });
  expect($(".savebar .saved-as")?.textContent).toBe("Chips / Chips comps · saved");
  expect($(".side")?.textContent).toContain("Chips comps");
});

it("refuses a bad name before calling the API", async () => {
  await render(`#/compare?target=${NVDA}`);
  await click(buttons("Save…")[0]!);
  const form = $(".save-form")!;
  await type(form.querySelector("input")!, "   ");
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle();
  expect(form.querySelector('[role="alert"]')?.textContent).toBe("Give it a name.");
  expect(calls("POST").filter(([u]) => String(u).includes("/items"))).toEqual([]);
});

it("a saved chart for a company you lost shows the same no-access state", async () => {
  await render("#/");
  await click(sidebarItem("Lost access"));
  expect(window.location.hash).toBe("#/chart");
  expect($(".layer .err")?.textContent).toContain("AAPL isn't one of your companies");
});

it("renames, duplicates and deletes from the sidebar", async () => {
  await render("#/");
  await click(buttons(/Rename NVDA revenue/)[0]!);
  const input = container.querySelector<HTMLInputElement>('.side input[aria-label="Item name"]')!;
  await type(input, "Revenue");
  await act(async () => input.form!.requestSubmit());
  await settle();
  expect(calls("PUT").at(-1)).toEqual(["/api/query/v1/workspace-items/i1", { name: "Revenue" }]);
  expect(sidebarItem("Revenue")).toBeTruthy();
  await click(buttons(/Duplicate Revenue/)[0]!);
  expect(calls("POST").at(-1)?.[0]).toBe("/api/query/v1/workspace-items/i1/duplicate");
  expect(sidebarItem("Revenue (copy)")).toBeTruthy();
  await click(buttons(/Delete Lost access/)[0]!);
  await click(container.querySelector<HTMLButtonElement>(".ws-confirm button")!);
  expect(calls("DELETE").map(([u]) => u)).toEqual(["/api/query/v1/workspace-items/i2"]);
  expect(sidebarItem("Lost access")).toBeUndefined();
  await click(buttons(/Delete workspace Semis/)[0]!);
  expect($(".ws-confirm")?.textContent).toContain("Delete “Semis” and its 3 items?");
  await click(container.querySelector<HTMLButtonElement>(".ws-confirm button")!);
  expect($(".side .ws")?.textContent).toContain("Save a chart, comps table or screen");
});

it("Home continues where you left off, newest first", async () => {
  await render("#/");
  await click(sidebarItem("NVDA comps"));
  await click(sidebarItem("NVDA revenue"));
  db.recentFilings = [{
    company_id: NVDA, accession_no: "0001045810-26-000075", section_key: "II-1A", read_at: "2026-10-21T11:00:00Z",
    name: "Nvidia", form: "10-Q", filed: "2026-08-26", period_end: "2026-07-26",
  }];
  window.location.hash = "#/";
  await settle();
  // the list refreshes after each open; reopen Home to fetch the filing too
  await click(sidebarItem("NVDA comps"));
  window.location.hash = "#/";
  await settle();
  const rows = [...container.querySelectorAll(".t.recent tr")].map((r) => r.textContent);
  expect(rows[0]).toContain("NVDA comps");
  expect(rows[1]).toContain("NVDA revenue");
  expect(rows[2]).toContain("Nvidia 10-Q");
  expect(container.querySelector<HTMLAnchorElement>(".t.recent a")?.getAttribute("href")).toBe(
    `#/filing/${NVDA}/0001045810-26-000075/II-1A`,
  );
});

it("the assistant saves the chart it built, and Undo deletes it", async () => {
  await render("#/");
  script = [
    {
      action: "set_chart_layers", title: "", label: "Chart: NVDA revenue",
      layers: [{ kind: "metric", company_id: NVDA, company_label: "NVDA", metric: "revenue", transform: "yoy", type: "line", axis: "L" }],
    },
    { action: "save_to_workspace", item: "chart", name: "NVDA growth", workspace: "semis", label: "Saved chart “NVDA growth” to “semis”" },
  ];
  const textarea = container.querySelector<HTMLTextAreaElement>("aside.ai textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "chart and save it");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => textarea.form!.requestSubmit());
  await settle();
  const saved = calls("POST").find(([u]) => String(u).endsWith("/items"))!;
  // into the existing "Semis" (names match ignoring case), with the chart the first action built
  expect(saved[0]).toBe("/api/query/v1/workspaces/w1/items");
  expect(saved[1].state.layers).toEqual([{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "yoy", type: "line", axis: "L" }]);
  expect(calls("POST").filter(([u]) => u === "/api/query/v1/workspaces")).toEqual([]);
  const newId = [...db.items.values()].find((i) => i.name === "NVDA growth")!.item_id;
  const undoSave = container.querySelectorAll<HTMLButtonElement>(".ai .act button")[1]!;
  await click(undoSave);
  expect(calls("DELETE").map(([u]) => u)).toEqual([`/api/query/v1/workspace-items/${newId}`]);
  expect(db.items.has(newId)).toBe(false);
});

it("the assistant can't save comps when none are on screen", async () => {
  await render("#/");
  script = [{ action: "save_to_workspace", item: "comps", name: "x", workspace: "w", label: "Saved comps table “x” to “w”" }];
  const textarea = container.querySelector<HTMLTextAreaElement>("aside.ai textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "save comps");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => textarea.form!.requestSubmit());
  await settle();
  expect(calls("POST").filter(([u]) => String(u).includes("/items"))).toEqual([]);
  expect($('[role="alert"]')?.textContent).toContain("Nothing to save as “x”");
});
