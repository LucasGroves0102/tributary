import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, fin, NVDA, nvdaRow, TSLA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";
import { textHash } from "./check";
import { clearNewest } from "./useRevisit";

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
    fin("revenue", `${fy}-10-26`, String(revenue * 1.1 ** 0.75), `FY${fy + 1} Q3`), // a TTM at Q3: not a fiscal year (the run rate)
  ];
});

const ACC = "0001045810-26-000021";
const CHECK_ID = "66666666-6666-4666-8666-666666666666";
const CITE = { accession_no: ACC, form: "10-Q", filed: "2026-08-27", company_id: NVDA, url: "https://www.sec.gov/Archives/edgar/data/1045810/x.htm", section_key: "I-2", figure: null, figure_status: null, as_of: "2026-11-02T08:00:00+00:00", verified: true };
/** a check as agent-api stores it (thesis_check.assemble), for the thesis text `hash` */
function checkResult(hash: string) {
  return {
    version: 1,
    thesis: { item_id: THESIS_ID, title: "NVDA: the build-out", company_id: NVDA, text_hash: hash, characters: BODY.length, characters_checked: BODY.length, truncated: false },
    summary: "One claim holds; the **supply** claim <img src=x onerror=alert(2)> doesn't.",
    claims: [
      { quote: "Data centers keep buying.", quote_found: true, status: "supported", explanation: `Data Center revenue was $41.1B (10-Q, ${ACC}).`, citations: [CITE], figures: [{ text: "$41.1B", status: "verified" }] },
      { quote: "Margins never fall.", quote_found: false, status: "unverifiable", explanation: "Revenue was $99.9B per 0000000000-26-000001.", citations: [{ accession_no: "0000000000-26-000001", verified: false }], figures: [{ text: "$99.9B", status: "unverified" }], downgraded: true },
    ],
    assumptions: [{
      ref: "model-1", item_id: MODEL_ID, name: "NVDA model", company_id: NVDA, base_fy: 2025,
      stats: { cagr: 0.1, cagr_years: 4, avg_margin: 0.2, margin_years: 5, last_growth: 0.1, last_margin: 0.2 },
      margin_min: 0.2, margin_max: 0.2,
      years: [
        { fy: 2026, growth: 25, margin: 50, growth_vs_cagr: 15, growth_flag: "above", margin_flag: "above", commentary: "Well above history.", citations: [CITE], figures: [] },
        { fy: 2027, growth: 10, margin: 45, growth_vs_cagr: 0, growth_flag: null, margin_flag: "above", commentary: "", citations: [], figures: [] },
      ],
    }],
    not_mentioned: [{ point: "Supply of Blackwell stayed constrained (MD&A).", citations: [CITE], figures: [] }],
    notes: [],
    citations: [{ accession_no: ACC, form: "10-Q", filed: "2026-08-27", company_id: NVDA, url: null, sections: ["I-2"] }],
    verification: { ok: false, figures: [{ text: "$41.1B", status: "verified" }, { text: "$99.9B", status: "unverified" }], unverified_accessions: ["0000000000-26-000001"], used_data: true, freshness: true },
    as_of: "2026-11-02T08:00:00+00:00", checked_at: "2026-11-02T09:00:00+00:00", model: "claude-haiku-5-5", questions: 4, stop: "end_turn", rounds: 5, cost_usd: 0.0123,
  };
}
const summary = (hash: string, id = CHECK_ID) => ({
  check_id: id, item_id: THESIS_ID, text_hash: hash, model: "claude-haiku-5-5", cost_usd: 0.0123,
  counts: { supported: 1, contradicted: 0, unverifiable: 1, flagged_assumptions: 2, not_mentioned: 1, ok: false }, created_at: "2026-11-02T09:00:00Z",
  // what the list reads out of a result stored before Revisit (no filings)
  company_id: NVDA, checked_at: "2026-11-02T09:00:00+00:00", filings: [],
});
let stored: { check_id: string; text_hash: string }[];
/** what the next check returns (default: checkResult) */
let recheck: ((hash: string) => ReturnType<typeof checkResult> & { filings?: unknown[] }) | null;
/** the check's stream ends with this error and its questions given back (C1) */
let failCheck: string | null;
/** NVDA's newest 10-K/10-Q in the filing index */
let indexRows: Record<string, unknown>[];
const NEW_10Q = "0001045810-26-000030";
const INDEX_ROW = { accession_no: NEW_10Q, company_id: NVDA, name: "Nvidia", form: "10-Q", filed: "2026-11-20", accepted_at: null, period_of_report: "2026-10-25", filing_url: null, document_url: null, sections: null, sections_available: false };
let asked: Record<string, unknown>[];
const sse = (events: object[]) =>
  new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });

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
  if (p === "/api/agent/v1/thesis-checks" && method === "POST") {
    asked.push(body);
    if (failCheck) {
      return sse([{ type: "tool", round: 0, name: "get_financials", input: {}, is_error: false }, { type: "error", message: failCheck },
        { type: "refunded", questions: 4 }, { type: "done", answer: {} }]);
    }
    const hash = stored[0]?.text_hash ?? "0".repeat(64);
    const result: ReturnType<typeof checkResult> & { filings?: unknown[] } = recheck ? recheck(hash) : checkResult(hash);
    const fresh = { ...summary(hash, "77777777-7777-4777-8777-777777777777"), company_id: NVDA, checked_at: result.checked_at, filings: result.filings ?? [] };
    return sse([{ type: "tool", round: 0, name: "get_financials", input: {}, is_error: false }, { type: "check", check: fresh, result }, { type: "done", answer: {} }]);
  }
  if (p === `/v1/companies/${NVDA}/filing-index`) {
    expect(u.searchParams.getAll("form")).toEqual(["10-K", "10-Q"]);
    return Response.json({ company_id: NVDA, as_of: "", forms: ["10-K", "10-Q"], from: null, to: null, limit: 1, offset: 0, has_more: false, filings: indexRows });
  }
  if (p === `/v1/workspace-items/${THESIS_ID}/checks`) return Response.json({ item_id: THESIS_ID, checks: stored.map((c) => summary(c.text_hash, c.check_id)) });
  const one = p.match(/^\/v1\/workspace-items\/[^/]+\/checks\/([^/]+)$/);
  if (one) {
    const c = stored.find((x) => x.check_id === one[1]);
    return c ? Response.json({ ...summary(c.text_hash, c.check_id), result: checkResult(c.text_hash) }) : Response.json({ detail: "check not found" }, { status: 404 });
  }
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
  clearNewest();
  recheck = null;
  failCheck = null;
  indexRows = [];
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  writes = [];
  stored = [];
  asked = [];
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
  // each assumption sits next to its default; the rule above says the history (10% a year over
  // 4 years, 20% margins) and the run rate it starts from
  expect(container.querySelector(".model-rule")?.textContent).toContain("4-yr CAGR 10.0%");
  expect(container.querySelector(".model-rule")?.textContent).toContain("5-yr avg 20.0%");
  expect([...container.querySelectorAll(".assumptions td .faint")].map((h) => h.textContent)).toEqual([
    "default 10.0%", "default 20.0%", "default 7.5%", "default 20.0%"]);
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
    // the latest year's 10% and 20% (no newer TTM here), growth fading half the gap to 5% a year
    state: { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 10, margin: 20 }, { growth: 7.5, margin: 20 }, { growth: 6.3, margin: 20 }] },
  });
  expect(window.location.hash).toMatch(/^#\/model\/55555555-/);
});

it("checks the saved thesis and shows claims, assumptions, what's not mentioned and staleness", async () => {
  stored = [{ check_id: CHECK_ID, text_hash: await textHash(NVDA, BODY) }];
  await render(`#/thesis/${THESIS_ID}`, ".check-panel .claims");
  const panel = container.querySelector<HTMLElement>(".check-panel")!;
  // claims with status badges; model text sanitized and its figures checked
  expect([...panel.querySelectorAll(".claim .status-badge")].map((b) => b.textContent)).toEqual(["Supported", "Can't verify"]);
  expect(panel.querySelector("[onerror], script")).toBeNull(); // DOMPurify, as in chat answers
  expect(panel.querySelector(".claims .figure.unverified")?.textContent).toBe("$99.9B");
  // #57: the warning names the figure, and the name finds its mark
  const flag = panel.querySelector<HTMLButtonElement>(".figure-flags button")!;
  expect(panel.querySelector(".figure-flags")?.textContent).toBe("⚠ 1 figure not found in the data: $99.9B");
  Element.prototype.scrollIntoView = function (this: Element) {
    this.classList.add("seen");
  };
  await click(flag);
  expect(panel.querySelector(".claims .figure.unverified")?.classList.contains("seen")).toBe(true);
  expect(panel.querySelector(".claims .accession.unverified")?.textContent).toContain("0000000000-26-000001");
  // a cited section opens the filing reader there
  const reader = panel.querySelector<HTMLAnchorElement>(".check-cites a.reader-link")!;
  expect(reader.getAttribute("href")).toBe(`#/filing/${NVDA}/${ACC}/I-2`);
  // assumptions: flagged years, the numbers as computed
  const rows = [...panel.querySelectorAll(".check-assumptions tbody tr")];
  expect(rows.map((r) => r.className)).toEqual(["flagged", "flagged"]);
  expect(rows[0]!.textContent).toContain("+15.0 pts");
  expect(panel.textContent).toContain("4-yr revenue CAGR 10.0%");
  expect(panel.querySelector(".not-mentioned")?.textContent).toContain("Blackwell");
  expect(panel.textContent).not.toContain("Stale");
  // clicking a quote marks it in the preview
  await click(panel.querySelector<HTMLButtonElement>("button.claim-quote")!);
  expect(container.querySelector(".thesis-doc mark.claim-mark")?.textContent).toBe("Data centers keep buying.");
  // the text changes: the check is stale, and Check waits for Save
  await act(async () => type(textarea(), BODY + "\n\nMore."));
  await settle();
  expect(panel.textContent).toContain("Stale: the thesis changed since this check");
  expect(button("Check my thesis").disabled).toBe(true);
  await click(button("Save"));
  stored = [{ check_id: CHECK_ID, text_hash: await textHash(NVDA, BODY + "\n\nMore.") }];
  expect(button("Check my thesis").disabled).toBe(false);
  await click(button("Check my thesis"));
  await waitFor('.check-panel select option[value="77777777-7777-4777-8777-777777777777"]');
  expect(asked).toEqual([{ item_id: THESIS_ID }]);
  // the new check is shown first in the history, and isn't stale
  const history = container.querySelector<HTMLSelectElement>(".check-panel select")!;
  expect([...history.options].map((o) => o.value)).toEqual(["77777777-7777-4777-8777-777777777777", CHECK_ID]);
  expect(container.querySelector(".check-panel")?.textContent).not.toContain("Stale");
});

it("offers a re-check after a newer 10-Q, then shows what flipped", async () => {
  const hash = await textHash(NVDA, BODY);
  stored = [{ check_id: CHECK_ID, text_hash: hash }];
  indexRows = [INDEX_ROW]; // filed after the check (a check without `filings`: its date counts)
  recheck = (h) => {
    const r = checkResult(h);
    return {
      ...r,
      checked_at: "2026-11-21T09:00:00+00:00",
      filings: [{ company_id: NVDA, latest: { accession_no: NEW_10Q, form: "10-Q", filed: "2026-11-20" }, evidence: null }],
      claims: [
        { ...r.claims[0]!, quote: "Data centers  keep buying", status: "contradicted" }, // same quote, normalised
        { quote: "Supply is fine.", quote_found: false, status: "unverifiable", explanation: "Opinion.", citations: [], figures: [] },
      ],
      assumptions: [{ ...r.assumptions[0]!, years: [r.assumptions[0]!.years[0]!, { ...r.assumptions[0]!.years[1]!, margin_flag: null }] }],
    } as ReturnType<typeof checkResult> & { filings: unknown[] };
  };
  await render(`#/thesis/${THESIS_ID}`, ".revisit");
  const notice = container.querySelector<HTMLElement>(".revisit")!;
  expect(notice.textContent).toContain("New 10-Q filed");
  expect(notice.textContent).toContain("since your last check — re-check?");
  // only one check: nothing to compare with yet
  expect(container.querySelector(".check-changes")).toBeNull();
  await click(button("Re-check", notice));
  await waitFor(".check-changes");
  expect(asked).toEqual([{ item_id: THESIS_ID }]);
  // the new check knows the 10-Q: no more notice
  expect(container.querySelector(".revisit")).toBeNull();
  const changes = container.querySelector<HTMLElement>(".check-changes")!;
  expect(changes.querySelector("h3")?.textContent).toMatch(/^Changes since /);
  const flipped = changes.querySelector(".change.flipped")!;
  expect([...flipped.querySelectorAll(".status-badge")].map((b) => b.textContent)).toEqual(["Supported", "Contradicted"]);
  expect(changes.querySelector(".change.added")?.textContent).toContain("Supply is fine.");
  expect(changes.querySelector(".change.removed")?.textContent).toContain("Margins never fall.");
  expect(changes.querySelector(".change.flag")?.textContent).toContain("NVDA model FY2027E margin");
  expect(changes.querySelector(".change.flag")?.textContent).toContain("above the 5-year range → within the 5-year range");
  expect(changes.querySelector("[onerror], script, img")).toBeNull();
});

it("doesn't offer a re-check when the check already knew the newest 10-Q", async () => {
  stored = [{ check_id: CHECK_ID, text_hash: await textHash(NVDA, BODY) }];
  indexRows = [{ ...INDEX_ROW, filed: "2026-11-02" }]; // filed the day of the check
  await render(`#/thesis/${THESIS_ID}`, ".check-panel .claims");
  await settle();
  expect(container.querySelector(".revisit")).toBeNull();
});

it("lists theses with a newer 10-Q on the workspace page", async () => {
  stored = [{ check_id: CHECK_ID, text_hash: "0".repeat(64) }];
  indexRows = [INDEX_ROW];
  await render("#/workspace/w1", ".revisit");
  const link = container.querySelector<HTMLButtonElement>(".ws-list .revisit")!;
  expect(link.textContent).toContain("New 10-Q filed");
  // the summary carries the company: the listing's null thesis state isn't needed
  await click(link);
  expect(window.location.hash).toBe(`#/thesis/${THESIS_ID}`);
});

it("shows no check panel until a check exists", async () => {
  await render(`#/thesis/${THESIS_ID}`, "textarea.thesis-text");
  expect(container.querySelector(".check-panel")).toBeNull();
  expect(button("Check my thesis").disabled).toBe(false);
});

it("a check that couldn't finish says why, that it didn't count, and keeps the last saved check (C1)", async () => {
  const hash = await textHash(NVDA, BODY);
  stored = [{ check_id: CHECK_ID, text_hash: hash }];
  failCheck = "its result was longer than one reply can hold, even when asked to shorten it, so there was nothing to save";
  await render(`#/thesis/${THESIS_ID}`, ".check-panel .claims");
  await click(button("Check my thesis"));
  await waitFor(".check-panel .report-status.error");
  const panel = container.querySelector<HTMLElement>(".check-panel")!;
  expect(panel.querySelector(".report-status.error")?.textContent).toBe(
    "The check wasn't finished: its result was longer than one reply can hold, even when asked to shorten it, so there was " +
    "nothing to save. It didn't count: the 4 questions it took were given back. The last saved check is below.");
  expect([...panel.querySelectorAll(".claim .status-badge")].map((b) => b.textContent)).toEqual(["Supported", "Can't verify"]);
  expect([...panel.querySelectorAll<HTMLOptionElement>("select option")].map((o) => o.value)).toEqual([CHECK_ID]);
});
