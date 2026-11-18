import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Dataset, Datasets } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { DataPage, METRICS_WORKSPACE } from "./DataPage";

const LIMITS = { file_bytes: 20 * 2 ** 20, rows: 200000, columns: 50, datasets: 50, user_bytes: 200 * 2 ** 20, requests_per_hour: 30, uploads_per_hour: 10 };
const STORES: Dataset = {
  dataset_id: "11111111-1111-4111-8111-111111111111", name: "stores", title: "Store counts", currency: "USD", known_from: "upload",
  fields: [{ name: "stores", unit: "number" }, { name: "sales", unit: "money" }], rows: 3, values: 6, companies: 2, bytes: 2048,
  first_day: "2026-03-31", last_day: "2026-06-30", created_at: "2026-11-16T15:00:00Z", uploaded_at: "2026-11-16T15:00:00Z",
};
const CSV = "Ticker,Date,Published,Stores,Sales\nNVDA,2026-06-30,2026-07-30,10,1000\nNOPE,2026-06-30,2026-07-30,1,1\n";

let list: Datasets;
let posts: { path: string; form: FormData }[] = [];
let refuse = false;

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  if (path === "/v1/datasets" && method === "GET") return Response.json(list);
  if (path === "/v1/datasets" && method === "POST") {
    const form = init!.body as FormData;
    posts.push({ path, form });
    if (refuse) {
      return Response.json({ detail: "The file can't be read as a dataset", code: "file", count: 2, hint: null,
        errors: [{ code: "number", message: "Not a number", row: 2, column: 4 }, { code: "date", message: "Not a date as YYYY-MM-DD between 1900 and 2099", row: 3, column: 2 }] },
      { status: 422 });
    }
    const answer = { columns: { company: "ticker", date: "date", published: form.get("published_column") ? "published" : null },
      suggested: { company_column: "Ticker", date_column: "Date", published_column: "Published" }, delimiter: "comma", decimal: ".",
      left_out: { rows: [3], count: 1, companies: 1 }, blank_rows: 0, limits: LIMITS };
    if (form.get("dry_run") === "true") {
      return Response.json({ ...answer, preview: true, name: form.get("name"), title: form.get("title"),
        fields: [{ name: "stores", unit: "number" }, { name: "sales", unit: "money" }], rows: 1, companies: 1,
        first_day: "2026-06-30", last_day: "2026-06-30", known_from: form.get("published_column") ? "column" : "upload",
        usage: { datasets: 0, bytes: 0, requests: 1, uploads: 0 } });
    }
    const stored = { ...STORES, dataset_id: "22222222-2222-4222-8222-222222222222", name: String(form.get("name")), title: String(form.get("title")) };
    list = { ...list, datasets: [...list.datasets, stored] };
    return Response.json({ ...answer, preview: false, dataset: stored, stored: 1, unchanged: 0 });
  }
  if (path.startsWith("/v1/formulas/validate")) {
    const body = JSON.parse(String(init?.body ?? "{}"));
    return /shares_diluted/.test(body.formula ?? "")
      ? Response.json({ valid: false, errors: [{ message: "Unknown metric shares_diluted", pos: 10, length: 14, hint: "Start typing to see the metric names" }] })
      : Response.json({ valid: true, unit: "USD", alignment: "fiscal", inputs: [] });
  }
  if (path === "/v1/screener/columns") {
    return Response.json({ columns: [
      { key: "weighted_shares_diluted_q", metric: "weighted_shares_diluted", span: "quarter", unit: "shares" },
      { key: "weighted_shares_basic_q", metric: "weighted_shares_basic", span: "quarter", unit: "shares" },
      { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" }] });
  }
  if (path.startsWith("/v1/workspace-items/")) return Response.json({ item_id: "m1", kind: "metric", name: "Per store", state: { formula: "revenue / mine.stores.stores", view: "ttm" } });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 10; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function show(saved: Saved | null = null) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<DataPage token="t" saved={saved} />));
  await settle();
}

const button = (name: string) => [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name);
function type(el: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}
function choose(select: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}
async function pick(text = CSV) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { value: [new File([text], "Store counts.csv", { type: "text/csv" })], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await settle();
}

beforeEach(() => {
  list = { datasets: [STORES], limits: LIMITS, uploads_allowed: true };
  posts = [];
  refuse = false;
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("lists the user's datasets with their formula names, fields and how rows are dated", async () => {
  await show();
  const row = container.querySelector(".data-table tbody tr")!;
  expect(row.textContent).toContain("Store counts");
  expect(row.textContent).toContain("mine.stores");
  expect([...row.querySelectorAll(".data-field")].map((f) => f.getAttribute("title"))).toEqual([
    "mine.stores.stores: Number", "mine.stores.sales: Money (USD)"]);
  expect(row.textContent).toContain("Rows are known from the day they were uploaded");
  expect(container.querySelector(".page-bar")!.textContent).toContain("1 of 50 datasets");
});

it("reads the header in the browser, checks the file, then stores it, listing rows left out by number", async () => {
  await show();
  await pick();
  const form = container.querySelector<HTMLFormElement>("form.data-form")!;
  const selects = [...form.querySelectorAll<HTMLSelectElement>("select.field")];
  // company and date found by name; the publication date only suggested
  expect(selects[0]!.value).toBe("Ticker");
  expect(selects[1]!.value).toBe("Date");
  expect(selects[2]!.value).toBe("");
  expect(form.textContent).toContain("“Published” looks like a publication date");
  expect(form.querySelector<HTMLInputElement>("input.field")!.value).toBe("store_counts");
  choose(selects[2]!, "Published");
  await settle();
  // the value columns, each with its unit
  const units = [...form.querySelectorAll(".data-unit")].map((u) => u.textContent);
  expect(units[0]).toContain("Stores (stores)");
  choose(form.querySelectorAll<HTMLSelectElement>(".data-unit select")[1]!, "money");
  await settle();
  await act(async () => button("Check it")!.click());
  await settle();
  expect(posts).toHaveLength(1);
  const sent = posts[0]!.form;
  expect(sent.get("dry_run")).toBe("true");
  expect(sent.get("name")).toBe("store_counts");
  expect(sent.get("company_column")).toBe("Ticker");
  expect(sent.get("published_column")).toBe("Published");
  expect(sent.get("units")).toBe("sales:money");
  expect((sent.get("file") as File).size).toBe(CSV.length);
  const check = container.querySelector(".data-check")!.textContent!;
  expect(check).toContain("1 row for 1 company");
  expect(check).toContain("each row is known from its own publication date");
  expect(check).toContain("(row 3)");
  expect(check).not.toContain("NOPE");
  await act(async () => button("Store 1 row")!.click());
  await settle();
  expect(posts[1]!.form.get("dry_run")).toBeNull();
  expect(container.querySelectorAll(".data-table tbody tr")).toHaveLength(2);
  expect(container.querySelector("form.data-form")).toBeNull();
});

it("shows a refused file's problems by row and column, never its cells", async () => {
  refuse = true;
  await show();
  await pick("Ticker,Date,X\nNVDA,2026-06-30,=cmd|' /C calc'!A0\n");
  await act(async () => button("Check it")!.click());
  await settle();
  const panel = container.querySelector(".errpanel")!.textContent!;
  expect(panel).toContain("2 problems; nothing was stored");
  expect(panel).toContain("Row 2, column 4: Not a number.");
  expect(panel).toContain("Row 3, column 2: Not a date");
  expect(panel).not.toContain("cmd");
});

it("a demo sandbox is told uploads need a permanent account", async () => {
  list = { datasets: [], limits: LIMITS, uploads_allowed: false };
  await show();
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(container.textContent).toContain("Your own data needs a permanent account");
});

it("saves a metric, checked first, into Your metrics, and lists metrics with a screen link", async () => {
  const create = vi.fn(async () => ({}));
  const saved = {
    list: { state: "ok", workspaces: [{ workspace_id: "w1", name: METRICS_WORKSPACE, created_at: "", updated_at: "", project: null,
      items: [{ item_id: "6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f", workspace_id: "w1", kind: "metric", name: "Per store", created_at: "", updated_at: "", opened_at: null }] }] },
    create, deleteWithUndo: vi.fn(),
  } as unknown as Saved;
  await show(saved);
  const metricRow = [...container.querySelectorAll(".data-table tbody tr")].at(-1)!;
  expect(metricRow.textContent).toContain("revenue / mine.stores.stores");
  expect(metricRow.querySelector("a")!.getAttribute("href")).toBe("#/companies?metric=6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f");
  const form = container.querySelector<HTMLFormElement>("form.data-metric-form")!;
  const [name, formula] = [...form.querySelectorAll<HTMLInputElement>("input.field")];
  expect(formula!.placeholder).toBe("revenue / mine.stores.stores");
  type(name!, "Sales per store");
  type(formula!, "mine.stores.sales / mine.stores.stores");
  await settle();
  await act(async () => button("Save metric")!.click());
  await settle();
  expect(create).toHaveBeenCalledWith("metric", "Sales per store", { formula: "mine.stores.sales / mine.stores.stores", view: "ttm" },
    { newWorkspace: METRICS_WORKSPACE });
});

it("the metric's formula box offers the chart builder's names and the user's own fields, and a problem clears on edit (Riley, test 10, finding 9)", async () => {
  const saved = { list: { state: "ok", workspaces: [] }, create: vi.fn(async () => ({})), deleteWithUndo: vi.fn() } as unknown as Saved;
  await show(saved);
  const form = container.querySelector<HTMLFormElement>("form.data-metric-form")!;
  const [name, formula] = [...form.querySelectorAll<HTMLInputElement>("input.field")];
  expect(formula!.getAttribute("role")).toBe("combobox");
  formula!.setSelectionRange = () => undefined;
  await act(async () => type(formula!, "revenue / weighted_sh"));
  await settle();
  const options = () => [...form.querySelectorAll("[role=option] b")].map((b) => b.textContent);
  expect(options()).toEqual(["weighted_shares_basic", "weighted_shares_diluted"]);
  await act(async () => type(formula!, "revenue / mine.st"));
  await settle();
  expect(options()).toEqual(["mine.stores.stores", "mine.stores.sales"]);
  // a refused formula says why; editing clears it
  await act(async () => type(name!, "Per share"));
  await act(async () => type(formula!, "revenue / shares_diluted"));
  await settle();
  await act(async () => form.requestSubmit());
  await settle();
  expect(form.querySelector(".err")?.textContent).toContain("Unknown metric shares_diluted");
  await act(async () => type(formula!, "revenue / weighted_shares_diluted"));
  await settle();
  expect(form.querySelector(".err")).toBeNull();
});
