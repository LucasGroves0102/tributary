import { clearChartCache } from "../chart/cache";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { entry, NVDA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";
import type { UiAction } from "../types";

const AAPL = "0000320193";
const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials"] }],
};
const answer = (actions: UiAction[]) => ({
  text: "Done.", stop: "end_turn", model: "claude-test", rounds: 2, tool_calls: [],
  usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
  cost_usd: null, citations: [], verification: null, actions,
});

const CHART: UiAction = {
  action: "set_chart_layers", title: "", label: "Chart: NVDA revenue",
  layers: [{ kind: "metric", company_id: NVDA, company_label: "NVDA", metric: "revenue", transform: "yoy", type: "line", axis: "L" }],
};
const WATCH: UiAction = { action: "add_watchlist", company_id: AAPL, company_label: "AAPL", label: "Added AAPL to watchlist" };
const SCREEN: UiAction = { action: "set_screen_filters", filters: { margin: 25 }, reset: false, label: "Screen: margin 25" };

let script: UiAction[] = [];
const asked: { question: string; context?: unknown }[] = [];

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/demo/users")) return Response.json({ users: ["alice"] });
  if (path.endsWith("/v1/demo/token")) return Response.json({ token: "t" });
  if (path.endsWith("/v1/me")) return Response.json(ME);
  if (path.endsWith("/v1/watchlist")) {
    return Response.json({ as_of: "", columns: [], expected_method: "", companies: [entry()] });
  }
  if (path.includes("/v1/watchlist/")) return Response.json({ company_id: path.slice(-10) });
  if (path.endsWith("/api/agent/v1/ask")) {
    asked.push(JSON.parse(String(init?.body)));
    const events = [
      ...script.map((action) => ({ type: "action", round: 0, action })),
      { type: "done", answer: answer(script) },
    ];
    return new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), {
      headers: { "Content-Type": "text/event-stream" },
    });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(hash: string) {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  await settle();
}

async function ask(question: string) {
  const textarea = container.querySelector<HTMLTextAreaElement>("aside.ai textarea")!;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    set.call(textarea, question);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => textarea.form!.requestSubmit());
  await settle();
}

const acts = () => [...container.querySelectorAll(".ai .act")].map((a) => a.textContent);
const undo = async (n: number) => {
  await act(async () => container.querySelectorAll<HTMLButtonElement>(".ai .act button")[n]!.click());
  await settle();
};
const methods = (m: string) =>
  vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === m).map(([u]) => String(u));

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  asked.length = 0;
  script = [];
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("sends the screen as context with each question", async () => {
  await render("#/companies?margin=20");
  await ask("only software");
  expect(asked[0]).toMatchObject({ question: "only software", context: { page: "companies", screen: { margin: 20 } } });
  expect(JSON.stringify(asked[0])).not.toMatch(/alice|user_id/);
});

it("applies a chart action, then undo restores the chart and the page", async () => {
  await render("#/");
  script = [CHART];
  await ask("chart nvidia revenue growth");
  expect(window.location.hash).toBe("#/chart");
  expect(container.querySelector("h1")?.textContent).toBe("Chart builder");
  expect(container.querySelector<HTMLSelectElement>('select[aria-label="Transform"]')?.value).toBe("yoy");
  expect(acts()).toEqual(["✓ Applied: Chart: NVDA revenueUndo"]);
  await undo(0);
  expect(window.location.hash).toBe("#/");
  expect(acts()).toEqual(["↺ Undone: Chart: NVDA revenue"]);
  window.location.hash = "#/chart";
  await settle();
  expect(container.querySelector('select[aria-label="Transform"]')).toBeNull();
});

it("watches through the API, and undoing an earlier action reverts the later ones too", async () => {
  await render("#/");
  script = [WATCH];
  await ask("watch apple");
  expect(methods("PUT")).toEqual([`/api/query/v1/watchlist/${AAPL}`]);
  script = [SCREEN];
  await ask("margin above 25%");
  expect(window.location.hash).toBe("#/companies?margin=25");
  await undo(0); // the watch, from the first answer
  expect(methods("DELETE")).toEqual([`/api/query/v1/watchlist/${AAPL}`]);
  expect(window.location.hash).toBe("#/");
  expect(acts()).toEqual(["↺ Undone: Added AAPL to watchlist", "↺ Undone: Screen: margin 25"]);
});

it("doesn't touch the watchlist for a company already on it, so undo can't remove it", async () => {
  await render("#/");
  script = [{ ...WATCH, company_id: NVDA, company_label: "NVDA", label: "Added NVDA to watchlist" }];
  await ask("watch nvidia");
  await undo(0);
  expect(methods("PUT")).toEqual([]);
  expect(methods("DELETE")).toEqual([]);
});

it("sets up comps on Compare, keeping its columns, and undo goes back", async () => {
  await render(`#/compare?target=${AAPL}&cols=roe_ttm`);
  script = [{
    action: "set_compare", label: "Comps: NVDA vs AAPL", columns: null,
    target: { company_id: NVDA, company_label: "NVDA" }, peers: [{ company_id: AAPL, company_label: "AAPL" }],
  }];
  await ask("compare nvidia with apple");
  expect(asked[0]).toMatchObject({ context: { page: "compare", compare: { target: AAPL, peers: [], columns: ["roe_ttm"] } } });
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${AAPL}&cols=roe_ttm`);
  expect(container.querySelector("h1")?.textContent).toBe("Compare");
  expect(acts()).toEqual(["✓ Applied: Comps: NVDA vs AAPLUndo"]);
  await undo(0);
  expect(window.location.hash).toBe(`#/compare?target=${AAPL}&cols=roe_ttm`);
});

it("merges screen filters into the current screen", async () => {
  await render("#/companies?band=1b-10b");
  script = [SCREEN];
  await ask("margin above 25");
  expect(window.location.hash).toBe("#/companies?band=1b-10b&margin=25");
  await undo(0);
  expect(window.location.hash).toBe("#/companies?band=1b-10b");
});
