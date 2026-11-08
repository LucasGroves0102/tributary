import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, fin, NVDA, nvdaRow } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";
import { clearNewest } from "../thesis/useRevisit";
import { draftKey } from "./bridge";
import { catalystLines, thesisTemplate } from "./project";

const AAPL = "0000320193";
const AMD = "0000002488";
const PID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const THESIS_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const MODEL_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ACC = "0001045810-26-000021";
const ME = {
  user_id: "alice",
  companies: [],
  catalog: { public: true, tiers: ["financials", "filings"], companies: 6000 },
};
const TEMPLATE = thesisTemplate({ company: "Nvidia (NVDA)", catalysts: [] });
const HISTORY = [2021, 2022, 2023, 2024, 2025].flatMap((fy, i) => {
  const revenue = 100e9 * 1.1 ** i;
  return [fin("revenue", `${fy}-01-26`, String(revenue), `FY${fy} Q4`), fin("operating_income", `${fy}-01-26`, String(revenue * 0.2), `FY${fy} Q4`)];
});
const EXPECTED = [{ form: "10-Q", period_end: "2026-10-26", expected_date: "2026-11-19", deadline: null, deadline_days: null, filer_category: null, basis: "history", typical_lag_days: 24, lag_samples: 8, status: "upcoming", estimate: true, last_period_end: null, last_filed: null }];
const CHANGES = {
  company_id: NVDA, accession_no: ACC, basis: "prior", as_of: "",
  sections: [{
    section_key: "1A", title: "Item 1A. Risk Factors", basis: "prior", stale: false,
    compared_to: { accession_no: "0001045810-26-000010", form: "10-Q", filed: "2026-05-28", period_of_report: null, section_key: "1A", document_url: null, filing_url: null },
    summary: { paragraphs: 10, prior_paragraphs: 9, unchanged: 9, minor_edits: 0, numbers_only: 0, new: 1, removed: 0, rewritten: 0 },
    changes: [{
      kind: "new", similarity: null, text: "Export controls could limit our sales of *data center* products.", prior_text: null, words: null,
      citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-27", period_of_report: null, section_key: "1A", document_url: null, char_start: 10, char_end: 80 },
      prior_citation: null,
    }],
  }],
};

type Item = { item_id: string; workspace_id: string; kind: string; name: string; state: Record<string, unknown>; created_at: string; updated_at: string; opened_at: string | null };
type Ws = { workspace_id: string; name: string; created_at: string; updated_at: string; project: Record<string, unknown> | null };
let workspaces: Map<string, Ws>;
let items: Map<string, Item>;
let writes: { method: string; path: string; body: Record<string, unknown> }[];
let n: number;

const item = (id: string, kind: string, name: string, state: Record<string, unknown>, ws = PID): Item => ({
  item_id: id, workspace_id: ws, kind, name, state, created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z", opened_at: null,
});
const summary = ({ state: _s, ...rest }: Item) => rest;

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
  if (p === "/v1/screener") {
    const ids = u.searchParams.getAll("company_id");
    const rows = [nvdaRow({ access: ["financials", "filings"] }), nvdaRow({ company_id: AAPL, name: "Apple", tickers: ["AAPL"] })].filter((r) => !ids.length || ids.includes(r.company_id));
    return Response.json({ as_of: "", total: rows.length, limit: 50, offset: 0, sort: "name", columns: [], rows });
  }
  if (p === "/v1/formulas/evaluate") return Response.json({ formula: "revenue", unit: "USD", basis: "fiscal", view: "ttm", alignment: "fiscal", as_of: "", inputs: [], points: [] });
  if (p === `/v1/companies/${NVDA}/financials`) return Response.json({ company_id: NVDA, view: "ttm", basis: "fiscal", as_of: "2026-11-02T00:00:00Z", rows: HISTORY });
  if (p === "/v1/companies/search") {
    expect(u.searchParams.get("access")).toBe("financials"); // readable companies only
    return Response.json({ results: [{ company_id: NVDA, name: "Nvidia", matched_on: "ticker", matched_alias: "NVDA", access: ["financials", "filings"] }] });
  }
  if (p === `/v1/companies/${NVDA}/peers`) {
    const peer = (id: string, ticker: string, access: string[]) => ({ ...nvdaRow({ company_id: id, name: ticker, tickers: [ticker], access: access as ["financials"] }), match: { sic_level: 4, same_filer_category: true, revenue_band: null, same_revenue_band: true } });
    return Response.json({ company_id: NVDA, as_of: "", columns: [], company: { ...nvdaRow(), revenue_band: null }, method: "", peers: [peer(AMD, "AMD", ["financials"]), peer(AAPL, "AAPL", ["financials"])] });
  }
  if (p === `/v1/companies/${NVDA}/expected-filings`) return Response.json({ company_id: NVDA, as_of: "", method: "", expected: EXPECTED });
  if (p === `/v1/companies/${NVDA}/sections/1`) {
    return Response.json({ sections: [{ section_key: "1", title: "Business", text: "Our Company\nNVIDIA pioneered accelerated computing to help solve the most challenging problems.\n\nWe sell to data centers and gamers around the world.", truncated: false, total_chars: 150, citation: { accession_no: "0001045810-26-000005", form: "10-K", filed: "2026-02-25", document_url: null } }] });
  }
  if (p === `/v1/companies/${NVDA}/filing-index`) {
    return Response.json({ company_id: NVDA, as_of: "", forms: ["10-K", "10-Q"], from: null, to: null, limit: 12, offset: 0, has_more: false, filings: [
      { accession_no: ACC, company_id: NVDA, name: "Nvidia", form: "10-Q", filed: "2026-08-27", accepted_at: null, period_of_report: "2026-07-27", filing_url: null, document_url: null, sections: 8, sections_available: true },
    ] });
  }
  if (p === `/v1/companies/${NVDA}/filings/${ACC}/changes`) return Response.json(CHANGES);
  if (p.endsWith("/segments")) return Response.json({ as_of: "", rows: [] });
  if (/^\/v1\/workspace-items\/[^/]+\/checks$/.test(p)) return Response.json({ item_id: "", checks: [] });
  if (p === "/v1/workspaces" && method === "GET") {
    return Response.json({ workspaces: [...workspaces.values()].map((w) => ({ ...w, items: [...items.values()].filter((i) => i.workspace_id === w.workspace_id).map(summary) })) });
  }
  if (p === "/v1/workspaces" && method === "POST") {
    const w: Ws = { workspace_id: `dddddddd-dddd-4ddd-8ddd-dddddddddd${String(++n).padStart(2, "0")}`, name: String(body.name), created_at: "", updated_at: "2026-11-02T00:00:00Z", project: (body.project as Record<string, unknown>) ?? null };
    workspaces.set(w.workspace_id, w);
    return Response.json({ ...w, items: [] }, { status: 201 });
  }
  const ws = p.match(/^\/v1\/workspaces\/([^/]+)$/);
  if (ws) {
    const w = workspaces.get(ws[1]!);
    if (!w) return Response.json({ detail: "workspace not found" }, { status: 404 });
    if (method === "PATCH") {
      if (body.project) w.project = body.project as Record<string, unknown>;
      return Response.json({ ...w, items: [] });
    }
    return Response.json({ ...w, items: [...items.values()].filter((i) => i.workspace_id === w.workspace_id).map((i) => ({ ...i, state: i.kind === "thesis" ? null : i.state })) });
  }
  const add = p.match(/^\/v1\/workspaces\/([^/]+)\/items$/);
  if (add && method === "POST") {
    const it = item(`eeeeeeee-eeee-4eee-8eee-eeeeeeeeee${String(++n).padStart(2, "0")}`, String(body.kind), String(body.name), body.state as Record<string, unknown>, add[1]);
    items.set(it.item_id, it);
    return Response.json(it, { status: 201 });
  }
  const one = p.match(/^\/v1\/workspace-items\/([^/]+)(\/open)?$/);
  if (one) {
    const it = items.get(one[1]!);
    if (!it) return Response.json({ detail: "item not found" }, { status: 404 });
    if (method === "PUT") {
      const next = { ...it, ...(body.name ? { name: String(body.name) } : {}), ...(body.state ? { state: body.state as Record<string, unknown> } : {}) };
      items.set(it.item_id, next);
      return Response.json(next);
    }
    return Response.json({ ...it, workspace_name: workspaces.get(it.workspace_id)?.name });
  }
  if (p === "/v1/recent") return Response.json({ items: [], filings: [] });
  if (p === "/v1/views") return Response.json({ detail: "preferences unavailable" }, { status: 503 });
  if (p === "/v1/connections") return Response.json({ detail: "Not Found" }, { status: 404 });
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

async function goTo(hash: string, ready: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  await waitFor(ready);
}

const visible = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)].find((e) => !e.closest("[hidden]"))!;
const button = (name: string, scope: ParentNode = container) =>
  [...scope.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name && !b.closest("[hidden]"))!;
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  await settle();
};
function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}
const thesisText = () => container.querySelector<HTMLTextAreaElement>("textarea.thesis-text")!;
const pinTo = async (pin: HTMLButtonElement, section: string) => {
  await click(pin);
  const menu = container.querySelector<HTMLElement>(".pin-menu")!;
  expect([...menu.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
    "Pillars · default", "Core thesis", "Variant perception", "Catalysts", "Risks and mitigants", "What would prove me wrong", "KPIs to watch",
  ]);
  expect(document.activeElement).toBe(menu.querySelector("button")); // Pillars, the default, has focus
  await click([...menu.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.startsWith(section))!);
};

beforeEach(() => {
  clearChartCache();
  clearNewest();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  writes = [];
  n = 0;
  workspaces = new Map([
    [PID, { workspace_id: PID, name: "Nvidia", created_at: "", updated_at: "2026-11-01T00:00:00Z", project: { companyId: NVDA, peers: [AAPL], notes: "", done: [] } }],
    ["w2", { workspace_id: "w2", name: "Semis", created_at: "", updated_at: "", project: null }],
  ]);
  items = new Map([
    [THESIS_ID, item(THESIS_ID, "thesis", "NVDA thesis", { companyId: NVDA, markdown: TEMPLATE })],
    [MODEL_ID, item(MODEL_ID, "model", "NVDA model", { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 10, margin: 20 }, { growth: 10, margin: 20 }] })],
  ]);
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("lists projects in the Analysis tab, and creates one from a readable company with suggested peers", async () => {
  await render("#/analysis", ".analysis-home h1");
  expect(container.querySelector('.nav a[aria-current="page"]')?.textContent).toContain("Analysis");
  // projects only: the plain workspace isn't one
  await waitFor(".project-row .tag, .project-status .faint");
  expect([...container.querySelectorAll(".project-row b")].map((b) => b.textContent)).toEqual(["Nvidia"]);
  expect(container.querySelector(".project-row")?.getAttribute("href")).toBe(`#/analysis/${PID}`);
  await click(button("New analysis"));
  const form = container.querySelector<HTMLFormElement>('form[aria-label="New analysis"]')!;
  expect(button("Start the analysis", form).disabled).toBe(true);
  await act(async () => type(form.querySelector<HTMLInputElement>('input[aria-label="Company to analyse"]')!, "NVDA"));
  await act(async () => new Promise((r) => setTimeout(r, 250)));
  await settle();
  await click(form.querySelector<HTMLButtonElement>('[aria-label="Company to analyse results"] button')!);
  // suggested peers from the peers route, the first ones ticked
  const chips = [...form.querySelectorAll<HTMLButtonElement>('[aria-label="Suggested peers"] .chip')];
  expect(chips.map((c) => [c.textContent, c.getAttribute("aria-pressed")])).toEqual([["✓ AMD", "true"], ["✓ AAPL", "true"]]);
  await click(chips[1]!); // untick Apple
  expect(form.querySelector<HTMLInputElement>("input[id$='-name']")!.value).toBe("Nvidia");
  await click(button("Start the analysis", form));
  const post = writes.find((w) => w.method === "POST" && w.path === "/v1/workspaces")!;
  expect(post.body).toEqual({ name: "Nvidia", project: { companyId: NVDA, peers: [AMD], notes: "", done: [] } });
  expect(window.location.hash).toMatch(/^#\/analysis\/dddddddd-[^/]+\/business$/);
});

it("switches steps from the rail and the header's tabs without losing an unsaved thesis", async () => {
  await render(`#/analysis/${PID}/thesis`, "textarea.thesis-text");
  const head = container.querySelector(".project-head")!;
  expect(head.querySelector("h1")?.textContent).toBe("Nvidia");
  expect([...head.querySelectorAll(".tabs a")].map((a) => a.textContent)).toEqual(["Company", "Charts", "Model", "Thesis", "Monitor"]);
  expect([...container.querySelectorAll(".step-rail a")].map((a) => a.textContent?.replace(/,.*$/, ""))).toEqual([
    "1Business", "2Numbers", "3What changed", "✓Peers", "✓Model", "6Thesis", "7Monitor"]); // a peer and a model exist
  await act(async () => type(thesisText(), `${TEMPLATE}\nMy own words.`));
  await settle();
  expect(head.querySelector(".tabs .dirty")).not.toBeNull();
  // Monitor: the check reads the saved text, so it waits for a save
  await click(head.querySelector<HTMLAnchorElement>('.tabs a[href$="/monitor"]')!);
  await goTo(`#/analysis/${PID}/monitor`, ".monitor-bar");
  expect(container.querySelector(".step-pane:not([hidden]) h2")?.textContent).toBe("Monitor");
  expect(button("Check my thesis").disabled).toBe(true);
  expect(button("Check my thesis").title).toContain("Save the thesis first");
  expect(container.querySelector(".coming-next .tag")?.textContent).toBe("coming next");
  // the rail: another step, then back; the thesis editor stayed mounted with the draft
  await goTo(`#/analysis/${PID}/business`, ".notes-text");
  await goTo(`#/analysis/${PID}/thesis`, "textarea.thesis-text");
  expect(thesisText().value).toBe(`${TEMPLATE}\nMy own words.`);
  expect(writes.filter((w) => w.method === "PUT")).toHaveLength(0);
  // the draft is also in the browser: leaving the project and coming back restores it
  await act(async () => new Promise((r) => setTimeout(r, 450)));
  expect(window.localStorage.getItem(draftKey("alice", THESIS_ID))).toContain("My own words.");
  act(() => root.unmount());
  await render(`#/analysis/${PID}/thesis`, ".draft-status");
  expect(container.querySelector(".draft-status")?.textContent).toContain("Restored your unsaved changes");
  expect(thesisText().value).toContain("My own words.");
});

it("pins a chart from Numbers into the chosen section of the thesis, saving it", async () => {
  await render(`#/analysis/${PID}/numbers`, ".numbers-chart");
  expect([...container.querySelectorAll(".numbers-chart")].map((c) => c.getAttribute("aria-label"))).toEqual([
    "NVDA: revenue and growth", "NVDA: gross and operating margin", "NVDA: free cash flow (operating cash flow − capex)",
    "NVDA: receivables vs revenue growth (YoY)", "NVDA: debt at year end"]);
  const fcf = container.querySelectorAll<HTMLElement>(".numbers-chart")[2]!;
  await pinTo(fcf.querySelector<HTMLButtonElement>(".pin-btn")!, "Risks and mitigants");
  const chart = writes.find((w) => w.method === "POST" && w.path === `/v1/workspaces/${PID}/items`)!;
  expect(chart.body).toMatchObject({ kind: "chart", name: "NVDA: free cash flow (operating cash flow − capex)" });
  expect((chart.body.state as { layers: { metric: string }[] }).layers.map((l) => l.metric)).toEqual(["operating_cash_flow", "capex", "free_cash_flow"]);
  const chartId = [...items.values()].find((i) => i.kind === "chart")!.item_id;
  const put = writes.find((w) => w.method === "PUT" && w.path === `/v1/workspace-items/${THESIS_ID}`)!;
  const md = String((put.body.state as { markdown: string }).markdown);
  const risks = md.indexOf("## Risks and mitigants");
  expect(md.indexOf(`[[embed:${chartId}]]`)).toBeGreaterThan(risks);
  expect(md.indexOf(`[[embed:${chartId}]]`)).toBeLessThan(md.indexOf("## What would prove me wrong"));
  expect(container.querySelector(".pin-toast")?.textContent).toContain("Pinned the chart under Risks and mitigants in your thesis.");
  // the Numbers step is done: a chart is saved in the project
  expect(container.querySelector('.step-rail a[href$="/numbers"]')?.className).toBe("done");
  // the thesis draws it: its preview resolves the new item
  await goTo(`#/analysis/${PID}/thesis`, ".thesis-doc");
  await waitFor('.thesis-doc .report-embed[aria-label^="NVDA: free cash flow"]');
  expect(container.querySelector('.thesis-doc .report-embed[aria-label^="NVDA: free cash flow"]')).not.toBeNull();
});

it("with no thesis yet, a pinned passage starts it from the template, cited with an EDGAR link", async () => {
  items.delete(THESIS_ID);
  await render(`#/analysis/${PID}/changes`, ".diff p.new");
  expect(container.querySelector(".diff p.new")?.textContent).toContain("Export controls could limit");
  await pinTo(container.querySelector<HTMLButtonElement>(".diff p.new .pin-btn")!, "Pillars");
  const post = writes.find((w) => w.method === "POST" && w.path === `/v1/workspaces/${PID}/items`)!;
  expect(post.body).toMatchObject({ kind: "thesis", name: "NVDA thesis", state: { companyId: NVDA } });
  const md = String((post.body.state as { markdown: string }).markdown);
  expect(md.startsWith(thesisTemplate({ company: "Nvidia (NVDA)", catalysts: catalystLines(EXPECTED as never) }).split("## Pillars")[0]!)).toBe(true);
  expect(md).toContain("- ~2026-11-19: 10-Q for the period ending 2026-10-26 (estimated date)"); // catalysts prefilled
  const quote = "> Export controls could limit our sales of \\*data center\\* products.\n>\n> — 10-Q filed 2026-08-27, Item 1A. Risk Factors (new in this filing) · " +
    `[${ACC}](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/${ACC}-index.htm)`;
  expect(md.indexOf(quote)).toBeGreaterThan(md.indexOf("### Pillar 1"));
  expect(md.indexOf(quote)).toBeLessThan(md.indexOf("## Catalysts"));
  expect(container.querySelector(".pin-toast")?.textContent).toContain("Started your thesis from the template");
});

it("adds bear and bull cases to the project's model, switches between them and saves all three", async () => {
  await render(`#/analysis/${PID}/model`, ".model-table");
  const page = visible(".model-page");
  expect(page.querySelector(".editor-bar a")).toBeNull(); // no "← workspace" inside the project
  const switchBtn = (s: string) => [...page.querySelectorAll<HTMLButtonElement>(".scenario-switch button")].find((b) => b.textContent?.startsWith(s))!;
  expect(switchBtn("Base").getAttribute("aria-pressed")).toBe("true");
  await click(switchBtn("Bear"));
  expect(page.querySelector(".scenario-empty")?.textContent).toContain("No bear case yet");
  // #156: before it's added, the bear tab's table shows the case it would start as, labelled so
  const tags = () => [...page.querySelectorAll(".model-table tr.forecast .tag")].map((t) => t.textContent);
  expect(tags()).toEqual(["Bear case (preview)", "Bear case (preview)"]);
  await click(button("Add bear and bull cases", page));
  const growth = page.querySelector<HTMLInputElement>('input[aria-label="FY2026E bear case revenue growth, percent"]')!;
  expect(growth.value).toBe("5"); // 10% less half of it, at least 5 points
  await act(async () => type(growth, "-2"));
  await settle();
  // the table shows the selected case; the chart all three, bear and bull lighter
  expect(tags()).toEqual(["Bear case", "Bear case"]);
  expect(page.querySelectorAll(".lchart-svg path[stroke-opacity]").length).toBe(4);
  // #98: a number that doesn't parse keeps the bear case on screen (last valid), not the base one
  await act(async () => type(growth, "-2x"));
  await settle();
  expect(tags()).toEqual(["Bear case", "Bear case"]);
  expect(page.querySelectorAll(".lchart-svg path[stroke-opacity]").length).toBe(4);
  expect(page.textContent).toContain("the chart and table show the last ones that did");
  await act(async () => type(growth, "-2"));
  await settle();
  await click(switchBtn("Bull"));
  expect(page.querySelector<HTMLInputElement>('input[aria-label="FY2026E bull case revenue growth, percent"]')!.value).toBe("15");
  await click(switchBtn("Base"));
  expect(tags()).toEqual(["Base case", "Base case"]);
  await click(button("Save", page));
  const put = writes.find((w) => w.method === "PUT" && w.path === `/v1/workspace-items/${MODEL_ID}`)!;
  expect(put.body.state).toEqual({
    companyId: NVDA, historyYears: 8,
    assumptions: [{ growth: 10, margin: 20 }, { growth: 10, margin: 20 }],
    scenarios: { bear: [{ growth: -2, margin: 16 }, { growth: 5, margin: 16 }], bull: [{ growth: 15, margin: 24 }, { growth: 15, margin: 24 }] },
  });
});

it("Reset to defaults sets exactly the default shown beside each field, in every case (#48)", async () => {
  await render(`#/analysis/${PID}/model`, ".model-table");
  const page = visible(".model-page");
  const switchBtn = (s: string) => [...page.querySelectorAll<HTMLButtonElement>(".scenario-switch button")].find((b) => b.textContent?.startsWith(s))!;
  await click(switchBtn("Bull"));
  await click(button("Add bear and bull cases", page));
  const inputs = () => [...page.querySelectorAll<HTMLInputElement>(".assumptions input[inputmode=decimal]")];
  const hints = () => [...page.querySelectorAll(".assumptions td .faint")].map((h) => h.textContent);
  await act(async () => type(inputs()[0]!, "99"));
  await settle();
  await click(button("Reset to defaults", page));
  const shown = hints().map((h) => h!.replace(/^default /, "").replace(/%$/, ""));
  expect(shown.every((h) => /^-?\d+\.\d$/.test(h))).toBe(true);
  expect(inputs().map((i) => Number(i.value).toFixed(1))).toEqual(shown);
  expect(page.querySelector(".model-rule")?.textContent).toContain("close half the gap each year to the long run");
});

it("opens a project's thesis or model inside the project, and plain ones as before", async () => {
  await render(`#/model/${MODEL_ID}`, ".analysis-project");
  await waitFor(".model-table");
  expect(window.location.hash).toBe(`#/analysis/${PID}/model/${MODEL_ID}`);
  expect(container.querySelector(".step-pane:not([hidden]) h2")?.textContent).toBe("Model");
  act(() => root.unmount());
  items.set("ffffffff-ffff-4fff-8fff-ffffffffffff", item("ffffffff-ffff-4fff-8fff-ffffffffffff", "thesis", "Loose", { markdown: "# Mine" }, "w2"));
  await render("#/thesis/ffffffff-ffff-4fff-8fff-ffffffffffff", ".thesis-doc h1");
  expect(window.location.hash).toBe("#/thesis/ffffffff-ffff-4fff-8fff-ffffffffffff");
  expect(container.querySelector(".analysis-project")).toBeNull();
});

it("saves the business notes and marks steps done by hand", async () => {
  await render(`#/analysis/${PID}/business`, ".item1 p");
  expect([...container.querySelectorAll(".item1 p")].map((p) => p.textContent?.trim())).toEqual([
    "Our Company — NVIDIA pioneered accelerated computing to help solve the most challenging problems.",
    "We sell to data centers and gamers around the world.",
  ]);
  await act(async () => type(container.querySelector<HTMLTextAreaElement>(".notes-text")!, "GPUs to clouds."));
  await act(async () => new Promise((r) => setTimeout(r, 850)));
  await settle();
  const patch = writes.find((w) => w.method === "PATCH")!;
  expect(patch.body).toEqual({ project: { companyId: NVDA, peers: [AAPL], notes: "GPUs to clouds.", done: [] } });
  expect(container.querySelector('.step-rail a[href$="/business"]')?.className).toBe("done");
  await goTo(`#/analysis/${PID}/changes`, ".diff");
  const mark = visible(".step-done input") as HTMLInputElement;
  await click(mark);
  expect(writes.filter((w) => w.method === "PATCH").at(-1)!.body).toMatchObject({ project: { done: ["changes"] } });
  expect(container.querySelector(".project-progress")?.getAttribute("aria-label")).toBe("4 of 7 steps done");
});
