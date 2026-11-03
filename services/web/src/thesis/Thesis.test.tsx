import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, fin, NVDA, nvdaRow, TSLA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";

const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials", "filings"] }],
};
const CHART_ID = "11111111-1111-4111-8111-111111111111";
const MODEL_ID = "22222222-2222-4222-8222-222222222222";
const THESIS_ID = "33333333-3333-4333-8333-333333333333";
const FOREIGN = "44444444-4444-4444-8444-444444444444";
const CHART = {
  title: "NVDA revenue", range: 3, align: "calendar",
  layers: [{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L", hidden: false }],
};
const MODEL = { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 25, margin: 50, note: "Blackwell" }, { growth: 10, margin: 45 }] };
const BODY = `# Why NVDA\n\nData centers keep buying. <img src=x onerror=alert(1)>\n\n[[embed:${CHART_ID}]]\n\n[[embed:${FOREIGN}]]\n\n## The model\n\n[[embed:${MODEL_ID}]]\n\nEnd.`;

// NVDA's fiscal years: revenue grows 10% a year, operating margin 20%
const HISTORY = [2021, 2022, 2023, 2024, 2025].flatMap((fy, i) => {
  const revenue = 100e9 * 1.1 ** i;
  const end = `${fy}-01-26`;
  return [
    fin("revenue", end, String(revenue), `FY${fy} Q4`, { citation: { accession_no: `0001045810-${String(fy).slice(2)}-000001`, form: "10-K", filed: `${fy}-02-26`, xbrl_tag: "Revenues", source_accessions: null, filing_url: null } }),
    fin("operating_income", end, String(revenue * 0.2), `FY${fy} Q4`),
    fin("revenue", `${fy}-10-26`, "1", `FY${fy + 1} Q3`), // a TTM at Q3: not a fiscal year
  ];
});

type Item = { item_id: string; workspace_id: string; kind: string; name: string; state: Record<string, unknown> | null; created_at: string; updated_at: string; opened_at: string | null };
let items: Map<string, Item>;
let writes: { method: string; path: string; body: Record<string, unknown> }[];

const item = (id: string, kind: string, name: string, state: Record<string, unknown>): Item => ({
  item_id: id, workspace_id: "w1", kind, name, state, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z", opened_at: null,
});

function respond(url: string, init?: RequestInit): Response {
  const u = new URL(url, "http://x");
  const method = init?.method ?? "GET";
  const p = u.pathname.replace(/^\/api\/query/, "");
  const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
  if (method !== "GET" && p.startsWith("/v1/workspace")) writes.push({ method, path: p, body });
  if (p === "/v1/demo/users") return Response.json({ users: ["alice"] });
  if (p === "/v1/demo/token") return Response.json({ token: "t" });
  if (p === "/v1/me") return Response.json(ME);
  if (p === "/v1/watchlist") return Response.json({ as_of: "", columns: [], expected_method: "", companies: [entry()] });
  if (p === "/v1/screener/columns") return Response.json({ columns: [{ key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" }], filer_categories: [], bands: [] });
  if (p === "/v1/screener") return Response.json({ as_of: "", total: 1, limit: 1, offset: 0, sort: "name", columns: [], rows: [nvdaRow()] });
  if (p === "/v1/formulas/evaluate") return Response.json({ formula: "revenue", unit: "USD", basis: "calendar", view: "ttm", alignment: "calendar", as_of: "", inputs: [], points: [] });
  if (p === `/v1/companies/${NVDA}/financials`) return Response.json({ company_id: NVDA, view: "ttm", basis: "fiscal", as_of: "2026-11-02T00:00:00Z", rows: HISTORY });
  if (p === "/v1/companies/search") return Response.json({ results: [{ company_id: TSLA, name: "Tesla", matched_on: "ticker", matched_alias: "TSLA", access: ["financials"] }] });
  const listed = () => [...items.values()].map(({ state: _s, ...rest }) => rest);
  if (p === "/v1/workspaces") return Response.json({ workspaces: [{ workspace_id: "w1", name: "Semis", created_at: "", updated_at: "", items: listed() }] });
  if (p === "/v1/workspaces/w1" && method === "GET") {
    return Response.json({ workspace_id: "w1", name: "Semis", created_at: "", updated_at: "", items: [...items.values()].map((i) => ({ ...i, state: i.kind === "thesis" ? null : i.state })) });
  }
  if (p === "/v1/workspaces/w1/items" && method === "POST") {
    const it = item(`55555555-5555-4555-8555-55555555555${items.size}`, String(body.kind), String(body.name), body.state as Record<string, unknown>);
    items.set(it.item_id, it);
    return Response.json(it, { status: 201 });
  }
  const m = p.match(/^\/v1\/workspace-items\/([^/]+)(\/open)?$/);
  if (m) {
    const it = items.get(m[1]!);
    if (!it) return Response.json({ detail: "item not found" }, { status: 404 });
    if (method === "PUT") {
      const next = { ...it, ...(body.name ? { name: String(body.name).trim() } : {}), ...(body.state ? { state: body.state as Record<string, unknown> } : {}) };
      items.set(it.item_id, next);
      return Response.json(next);
    }
    return Response.json({ ...it, workspace_name: "Semis" });
  }
  if (p === "/v1/recent") return Response.json({ items: [], filings: [] });
  if (p === "/v1/views") return Response.json({ detail: "preferences unavailable" }, { status: 503 });
  if (p.startsWith("/v1/companies/") && p.endsWith("/expected-filings")) return Response.json({ company_id: NVDA, as_of: "", method: "", expected: [] });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  await act(async () => { await vi.dynamicImportSettled(); });
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function waitFor(selector: string) {
  for (let i = 0; i < 40 && !container.querySelector(selector); i++) await settle();
}

async function render(hash: string, ready: string) {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  await waitFor(ready);
}

const button = (name: string, scope: ParentNode = container) =>
  [...scope.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name)!;
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  await settle();
};
function type(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : el instanceof HTMLSelectElement ? HTMLSelectElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}
const textarea = () => container.querySelector<HTMLTextAreaElement>("textarea.thesis-text")!;
async function blobText(b: Blob): Promise<string> {
  return new Promise((done) => {
    const r = new FileReader();
    r.onload = () => done(String(r.result));
    r.readAsText(b);
  });
}

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  writes = [];
  items = new Map([
    [CHART_ID, item(CHART_ID, "chart", "NVDA revenue", CHART)],
    [MODEL_ID, item(MODEL_ID, "model", "NVDA model", MODEL)],
    [THESIS_ID, item(THESIS_ID, "thesis", "NVDA: the build-out", { companyId: NVDA, markdown: BODY })],
  ]);
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("renders a thesis with this workspace's items embedded, sanitized, foreign embeds dropped", async () => {
  await render(`#/thesis/${THESIS_ID}`, ".thesis-doc h1");
  expect(container.querySelector<HTMLInputElement>("input.title-input")?.value).toBe("NVDA: the build-out");
  const doc = container.querySelector(".thesis-doc")!;
  expect(doc.querySelector("h1")?.textContent).toBe("Why NVDA");
  expect(doc.querySelector("img, [onerror]")).toBeNull();
  await waitFor(".thesis-doc .model-table");
  const embeds = [...doc.querySelectorAll<HTMLElement>(".report-embed")].map((e) => e.getAttribute("aria-label"));
  expect(embeds).toEqual(["NVDA revenue", "NVDA model"]);
  expect(doc.textContent).not.toContain("[[embed:");
  expect(doc.textContent).not.toContain(FOREIGN);
  // the model embed: its saved assumptions over the history read now, forecast rows labelled
  const rows = [...doc.querySelectorAll(".model-table tbody tr")].map((r) => r.querySelector("th")?.textContent);
  expect(rows).toEqual(["FY2021", "FY2022", "FY2023", "FY2024", "FY2025", "FY2026E", "FY2027E"]);
  expect(doc.querySelector(".model-table tr.forecast")?.textContent).toContain("25.0%");
  // the editor says a marker points at nothing in this workspace
  expect(container.textContent).toContain("1 embed points at an item no longer in this workspace");
});

it("inserts an embed at the cursor and saves name, company and text", async () => {
  items.set(THESIS_ID, item(THESIS_ID, "thesis", "Untitled thesis", { markdown: "Intro." }));
  await render(`#/thesis/${THESIS_ID}`, "textarea.thesis-text");
  await act(async () => {
    textarea().focus();
    textarea().setSelectionRange(6, 6);
  });
  const picker = container.querySelector<HTMLSelectElement>('select[aria-label="Embed a saved item"]')
    ?? container.querySelector<HTMLSelectElement>(".thesis-tools select")!;
  // the thesis itself isn't offered; the chart and the model are
  expect([...picker.options].map((o) => o.textContent)).toEqual(["Embed a saved item…", "Chart · NVDA revenue", "Model · NVDA model"]);
  await act(async () => type(picker, MODEL_ID));
  await settle();
  expect(textarea().value).toBe(`Intro.\n\n[[embed:${MODEL_ID}]]\n\n`);
  expect(container.textContent).toContain("● unsaved changes");
  await act(async () => type(container.querySelector<HTMLInputElement>("input.title-input")!, "  NVDA bull case "));
  await click(button("Save"));
  const put = writes.find((w) => w.method === "PUT")!;
  expect(put.path).toBe(`/v1/workspace-items/${THESIS_ID}`);
  expect(put.body).toEqual({ name: "NVDA bull case", state: { markdown: `Intro.\n\n[[embed:${MODEL_ID}]]\n\n` } });
  expect(container.textContent).not.toContain("● unsaved changes");
  // Ctrl+S saves too
  await act(async () => type(textarea(), "Changed."));
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "s", ctrlKey: true }));
  });
  await settle();
  expect(writes.filter((w) => w.method === "PUT")).toHaveLength(2);
});

it("exports Markdown with a header and readable embed lines", async () => {
  await render(`#/thesis/${THESIS_ID}`, "textarea.thesis-text");
  const created = vi.fn((_b: Blob) => "blob:thesis");
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: () => {} });
  await click(button("Export .md"));
  const text = await blobText(created.mock.calls[0]![0] as Blob);
  expect(text).toMatch(/^---\ntitle: "NVDA: the build-out"\ncompany: ".+"\ncik: "0001045810"\ndate: \d{4}-\d{2}-\d{2}\nsource: Tributary thesis\n---\n\n# Why NVDA/);
  expect(text).toContain("[Chart: NVDA revenue]");
  expect(text).toContain("[Model: NVDA model]");
  expect(text).not.toContain("[[embed:");
});

it("imports a .md file, refuses a binary one, and can undo the import", async () => {
  await render(`#/thesis/${THESIS_ID}`, "textarea.thesis-text");
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const pick = async (f: File) => {
    Object.defineProperty(input, "files", { value: [f], configurable: true });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    await settle();
  };
  await pick(new File(["---\ntitle: Old notes\n---\n# Imported\r\n\r\n<script>x()</script>\n"], "notes.md", { type: "text/markdown" }));
  expect(textarea().value).toBe("# Imported\n\n<script>x()</script>\n");
  expect(container.querySelector<HTMLInputElement>("input.title-input")?.value).toBe("NVDA: the build-out"); // kept: not "Untitled"
  expect(container.textContent).toContain("Imported notes.md. Save to keep it.");
  expect(container.querySelector(".thesis-doc script")).toBeNull();
  await click(button("Undo import"));
  expect(textarea().value).toBe(BODY);
  await pick(new File([new Uint8Array([0x50, 0x4b, 0, 0, 0xff])], "deck.pptx"));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("isn't plain text");
  expect(textarea().value).toBe(BODY);
  expect(writes.filter((w) => w.method === "PUT")).toHaveLength(0); // nothing saved until Save
});

it("another user's thesis or model reads as not found", async () => {
  await render(`#/thesis/${FOREIGN}`, ".page > p.empty");
  expect(container.textContent).toContain("This thesis doesn't exist, or isn't yours");
  act(() => root.unmount());
  await render(`#/model/${FOREIGN}`, ".page > p.empty");
  expect(container.textContent).toContain("This model doesn't exist, or isn't yours");
});

it("edits a model's assumptions next to their history and saves them", async () => {
  await render(`#/model/${MODEL_ID}`, ".model-table");
  const growth = container.querySelector<HTMLInputElement>('input[aria-label="FY2026E revenue growth, percent"]')!;
  expect(growth.value).toBe("25");
  // each assumption sits next to its history: 10% a year over 4 years, 20% margins
  expect(container.querySelector(".assumptions")?.textContent).toContain("4-yr CAGR 10.0%");
  expect(container.querySelector(".assumptions")?.textContent).toContain("5-yr avg 20.0%");
  const forecastRows = () => [...container.querySelectorAll(".model-page .model-table tr.forecast")].map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent));
  expect(forecastRows()[0]).toEqual(["Forecast", "$183.01B", "25.0%", "50.0%", "$91.51B"]);
  await act(async () => type(growth, "0"));
  await settle();
  expect(forecastRows()[0]![1]).toBe("$146.41B");
  // an impossible number is flagged and can't be saved
  await act(async () => type(growth, "abc"));
  await settle();
  expect(growth.getAttribute("aria-invalid")).toBe("true");
  expect(button("Save").disabled).toBe(true);
  await act(async () => type(growth, "12.5"));
  await act(async () => type(container.querySelector<HTMLSelectElement>('select[id$="-years"]')!, "3"));
  await settle();
  await click(button("Save"));
  const put = writes.find((w) => w.method === "PUT")!;
  expect(put.body).toEqual({
    name: "NVDA model",
    state: { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 12.5, margin: 50, note: "Blackwell" }, { growth: 10, margin: 45 }, { growth: 10, margin: 45 }] },
  });
});

it("creates a thesis and a model from the workspace page", async () => {
  await render("#/workspace/w1", "h1");
  expect([...container.querySelectorAll(".ws-list .tag")].map((t) => t.textContent)).toContain("Thesis");
  await click(button("New thesis"));
  const form = container.querySelector<HTMLFormElement>('form[aria-label="New thesis"]')!;
  expect(form.querySelector<HTMLInputElement>("input[id$='-name']")?.value).toBe("Untitled thesis");
  await click(button("Create thesis", form));
  const post = writes.find((w) => w.method === "POST" && w.path === "/v1/workspaces/w1/items")!;
  expect(post.body).toEqual({ kind: "thesis", name: "Untitled thesis", state: { markdown: "" } });
  expect(window.location.hash).toMatch(/^#\/thesis\/55555555-/);
});

it("creates a model from the company page, starting from the company's own history", async () => {
  await render(`#/company/${NVDA}`, ".co-head h1");
  await click(button("New model"));
  const form = container.querySelector<HTMLFormElement>('form[aria-label="New model"]')!;
  expect(form.querySelector("select")).not.toBeNull(); // which workspace
  await click(button("Create model", form));
  const post = writes.find((w) => w.method === "POST")!;
  expect(post.body).toMatchObject({
    kind: "model",
    state: { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 10, margin: 20 }, { growth: 10, margin: 20 }, { growth: 10, margin: 20 }] },
  });
  expect(window.location.hash).toMatch(/^#\/model\/55555555-/);
});
