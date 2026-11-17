import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SqlResult, SqlSchema } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { NotebookPage } from "./NotebookPage";

const SCHEMA: SqlSchema = {
  objects: [
    { name: "financials", kind: "view", description: "Tributary's figures as they stand today.", arguments: null,
      columns: [{ name: "company_id", type: "text", description: "The company" }, { name: "value", type: "numeric", description: "The figure as filed" }] },
    { name: "reading", kind: "function", description: "One company's latest figure with its reading.", arguments: "company_id text, metric text, span text DEFAULT 'ttm'::text",
      columns: [{ name: "value", type: "numeric", description: null }] },
  ],
  examples: [{ title: "A company's restatements", sql: "SELECT * FROM restatements('0000320193')" }],
  limits: { rows: 10000, seconds: 15, max_cost: 1500000, seconds_per_day: 600, sql_chars: 20000 },
};
const RESULT: SqlResult = {
  columns: [{ name: "period_end", type: "date" }, { name: "value", type: "number" }],
  rows: [["2026-01-25", "39331000000"], ["2026-04-26", "44062000000"]],
  row_count: 2, truncated: null, max_rows: 10000, seconds: 0.03, cost: 310, reads: ["financials"], sql: "SELECT period_end, value FROM financials",
  budget: { used_seconds: 0.1, limit_seconds: 600 },
};

let sent: string[] = [];
let schemaOn = true;
let refuse: Record<string, unknown> | null = null;

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  if (path === "/v1/sql/schema") return schemaOn ? Response.json(SCHEMA) : Response.json({ detail: "Not Found" }, { status: 404 });
  if (path === "/v1/sql") {
    sent.push(JSON.parse(String(init!.body)).sql);
    return refuse ? Response.json(refuse, { status: 422 }) : Response.json(RESULT);
  }
  if (path === "/v1/workspace-items/nb1/open") {
    return Response.json({ item_id: "nb1", workspace_id: "w1", kind: "notebook", name: "Margins", created_at: "", updated_at: "", opened_at: null,
      state: { cells: [{ id: "t1", kind: "text", text: "**Why** <img src=x onerror=alert(1)>" }, { id: "q1", kind: "sql", sql: "SELECT period_end, value FROM financials" },
        { id: "c1", kind: "chart", chart: { source: "q1", type: "line", x: "period_end", y: ["value"] } }] } });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 10; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

function fakeSaved() {
  return {
    list: { state: "ok", workspaces: [] },
    create: vi.fn(async (kind: string, name: string, state: object) => ({ item_id: "new1", workspace_id: "w9", kind, name, state, created_at: "", updated_at: "", opened_at: null })),
    update: vi.fn(async (id: string, patch: { state?: object }) => ({ item_id: id, workspace_id: "w1", kind: "notebook", name: "Margins", state: patch.state, created_at: "", updated_at: "", opened_at: null })),
  } as unknown as Saved & { create: ReturnType<typeof vi.fn>; update: ReturnType<typeof vi.fn> };
}

async function show(itemId: string | null = null, saved: Saved | null = fakeSaved()) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<NotebookPage token="t" itemId={itemId} saved={saved} />));
  await settle();
}

const button = (name: string, within: ParentNode = container) => [...within.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name);
const cells = () => [...container.querySelectorAll<HTMLElement>(".nb-cell")];
function type(el: HTMLTextAreaElement | HTMLInputElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  sent = [];
  schemaOn = true;
  refuse = null;
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("lists the views with their columns described, the functions and the examples", async () => {
  await show();
  const side = container.querySelector(".nb-schema")!;
  expect(side.textContent).toContain("financials");
  expect(side.textContent).toContain("The figure as filed");
  expect(side.textContent).toContain("reading(company_id text, metric text");
  expect(side.textContent).toContain("A company's restatements");
  expect(container.querySelector(".nb-status")!.textContent).toContain("up to 15 seconds and 10,000 rows each; 10 minutes of queries a day");
});

it("runs a query as typed, shows the rows and offers a CSV and a chart", async () => {
  await show();
  const query = cells().find((c) => c.querySelector("textarea.nb-sql"))!;
  type(query.querySelector("textarea")!, "select period_end, value from financials");
  await settle();
  await act(async () => button("Run", query)!.click());
  await settle();
  expect(sent).toEqual(["select period_end, value from financials"]);
  const table = query.querySelector("table.nb-table")!;
  expect([...table.querySelectorAll("th")].map((th) => [th.textContent, th.className])).toEqual([["period_end", ""], ["value", "n"]]);
  expect(table.querySelectorAll("tbody tr")).toHaveLength(2);
  expect(query.querySelector(".nb-line")!.textContent).toBe("2 rows in 0.03 s");
  await act(async () => button("Chart it", query)!.click());
  await settle();
  const chart = cells().find((c) => c.classList.contains("nb-chart"))!;
  expect(chart.querySelector("svg")).not.toBeNull();
  expect(chart.querySelector(".nb-line")!.textContent).toBe("Over Query 1");
});

it("shows a refusal in the API's words with where the error is", async () => {
  refuse = { detail: "syntax error at or near \"where\"", code: "refused", position: 12 };
  await show();
  const query = cells().find((c) => c.querySelector("textarea.nb-sql"))!;
  type(query.querySelector("textarea")!, "select\nfrom where");
  await settle();
  await act(async () => button("Run", query)!.click());
  await settle();
  const alert = query.querySelector(".nb-refusal")!;
  expect(alert.textContent).toBe("This query can't run here: syntax error at or near \"where\". Line 2, column 6.");
});

it("an example becomes a query and runs", async () => {
  await show();
  const before = cells().length;
  await act(async () => button("A company's restatements")!.click());
  await settle();
  expect(cells()).toHaveLength(before + 1);
  expect(sent).toEqual(["SELECT * FROM restatements('0000320193')"]);
});

it("saves a new notebook in Notebooks with what was written, never a result", async () => {
  const saved = fakeSaved();
  await show(null, saved);
  const query = cells().find((c) => c.querySelector("textarea.nb-sql"))!;
  await act(async () => button("Run", query)!.click());
  await settle();
  await act(async () => button("Save")!.click());
  await settle();
  const form = container.querySelector<HTMLFormElement>("form.nb-save")!;
  type(form.querySelector("input")!, "Nvidia margins");
  await settle();
  await act(async () => button("Save in Notebooks", form)!.click());
  await settle();
  expect(saved.create).toHaveBeenCalledTimes(1);
  const [kind, name, state, target] = saved.create.mock.calls[0]!;
  expect([kind, name, target]).toEqual(["notebook", "Nvidia margins", { newWorkspace: "Notebooks" }]);
  expect(JSON.stringify(state)).not.toContain("39331000000");
  expect((state as { cells: { kind: string }[] }).cells.map((c) => c.kind)).toEqual(["text", "sql"]);
  expect(container.querySelector("h1")!.textContent).toBe("Nvidia margins");
});

it("opens a saved notebook, renders its note safely and saves over it", async () => {
  const saved = fakeSaved();
  await show("nb1", saved);
  expect(container.querySelector("h1")!.textContent).toBe("Margins");
  const note = container.querySelector(".nb-note")!;
  expect(note.querySelector("strong")!.textContent).toBe("Why");
  expect(note.innerHTML).not.toContain("onerror");
  // the chart waits for its query: nothing was stored with it
  expect(cells()[2]!.textContent).toContain("Run Query 1 to draw it");
  expect(button("Save")!.disabled).toBe(true); // nothing changed yet
  type(container.querySelector<HTMLTextAreaElement>("textarea.nb-sql")!, "SELECT 2");
  await settle();
  expect(container.querySelector(".nb-status")!.textContent).toContain("Unsaved changes");
  await act(async () => button("Save")!.click());
  await settle();
  expect(saved.update).toHaveBeenCalledWith("nb1", { state: { cells: [
    { id: "t1", kind: "text", text: "**Why** <img src=x onerror=alert(1)>" },
    { id: "q1", kind: "sql", sql: "SELECT 2" },
    { id: "c1", kind: "chart", chart: { source: "q1", type: "line", x: "period_end", y: ["value"] } },
  ] } });
});

it("says so on a server without the SQL workspace", async () => {
  schemaOn = false;
  await show();
  expect(container.textContent).toContain("The SQL workspace isn't available on this server yet.");
  expect(button("Run all")!.disabled).toBe(true);
});
