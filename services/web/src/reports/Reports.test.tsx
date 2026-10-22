import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, NVDA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";

const AAPL = "0000320193";
const ACC = "0001045810-26-000021";
const ME = {
  user_id: "alice",
  companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials", "filings"] }],
};
const CHART = {
  title: "NVDA revenue", range: 3, align: "calendar",
  layers: [{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L", hidden: false }],
};
const COMPS = { target: NVDA, peers: [AAPL] };

function reportState(markdown: string, figures = [{ text: "$46.7B", status: "verified" }]) {
  return {
    workspace_id: "w1", markdown,
    items: [
      { ref: "item-1", item_id: "i1", kind: "chart", name: "NVDA revenue", state: CHART },
      { ref: "item-2", item_id: "i2", kind: "comps", name: "<b>Comps</b>", state: COMPS },
    ],
    citations: [{ accession_no: ACC, form: "10-Q", filed: "2026-08-27", company_id: NVDA, url: "https://www.sec.gov/Archives/x.htm", sections: [] }],
    verification: { ok: figures.every((f) => f.status !== "unverified"), figures, unverified_accessions: [], used_data: true, freshness: true },
    as_of: "2026-10-21T08:00:00+00:00", written_at: "2026-10-21T09:00:00+00:00", model: "claude-haiku-5-5", questions: 3, stop: "end_turn",
  };
}

const NOTE =
  `# Semis note\n\n## Summary\n\nNVIDIA's revenue was $46.7B (10-Q filed 2026-08-27, ${ACC}). Next year $61.2B.\n\n` +
  "<img src=x onerror=alert(1)>\n\n## Revenue\n\n[[embed:item-1]]\n\n## Comps\n\n[[embed:item-2]]\n\n[[embed:item-9]]\n\nAs of 2026-10-21.";

type Item = { item_id: string; workspace_id: string; kind: string; name: string; state: Record<string, unknown> | null; created_at: string; updated_at: string; opened_at: string | null };
let items: Map<string, Item>;
let reportReply: () => Response;
let reportBodies: unknown[];

const item = (id: string, kind: string, name: string, state: Record<string, unknown>): Item => ({
  item_id: id, workspace_id: "w1", kind, name, state, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z", opened_at: null,
});

const sse = (events: object[]) =>
  new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  const method = init?.method ?? "GET";
  if (path === "/api/agent/v1/reports") {
    reportBodies.push(JSON.parse(String(init?.body)));
    return reportReply();
  }
  const p = path.replace(/^\/api\/query/, "");
  if (p === "/v1/demo/users") return Response.json({ users: ["alice"] });
  if (p === "/v1/demo/token") return Response.json({ token: "t" });
  if (p === "/v1/me") return Response.json(ME);
  if (p === "/v1/watchlist") return Response.json({ as_of: "", columns: [], expected_method: "", companies: [entry()] });
  if (p === "/v1/screener/columns") return Response.json({ columns: [{ key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" }], filer_categories: [], bands: [] });
  if (p === "/v1/formulas/evaluate") return Response.json({ formula: "revenue", unit: "USD", basis: "calendar", view: "ttm", alignment: "calendar", as_of: "", inputs: [], points: [] });
  const listed = () => [...items.values()].map(({ state: _s, ...rest }) => rest);
  if (p === "/v1/workspaces") return Response.json({ workspaces: [{ workspace_id: "w1", name: "Semis", created_at: "", updated_at: "", items: listed() }] });
  if (p === "/v1/workspaces/w1") {
    return Response.json({ workspace_id: "w1", name: "Semis", created_at: "", updated_at: "", items: [...items.values()].map((i) => ({ ...i, state: i.kind === "report" ? null : i.state })) });
  }
  const m = p.match(/^\/v1\/workspace-items\/([^/]+)(\/open)?$/);
  if (m && (method === "POST" || method === "GET")) {
    const it = items.get(m[1]!);
    return it ? Response.json({ ...it, workspace_name: "Semis" }) : Response.json({ detail: "item not found" }, { status: 404 });
  }
  if (/^\/v1\/workspaces\/[^/]+$/.test(p)) return Response.json({ detail: "workspace not found" }, { status: 404 });
  if (p === "/v1/recent") return Response.json({ items: [], filings: [] });
  if (p === "/v1/views") return Response.json({ detail: "preferences unavailable" }, { status: 503 });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(hash: string, ready = "h1") {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  await waitFor(ready);
}

/** Lazy screens load their chunk first: settle until `selector` is there. */
async function waitFor(selector: string) {
  for (let i = 0; i < 40 && !container.querySelector(selector); i++) await settle();
}

const button = (name: string) =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.includes(name))!;
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  await settle();
};

beforeEach(() => {
  clearChartCache();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
  items = new Map([["i1", item("i1", "chart", "NVDA revenue", CHART)], ["i2", item("i2", "comps", "<b>Comps</b>", COMPS)]]);
  reportBodies = [];
  reportReply = () => {
    items.set("r1", item("r1", "report", "Report: Semis", reportState(NOTE, [{ text: "$46.7B", status: "verified" }, { text: "$61.2B", status: "unverified" }])));
    return sse([
      { type: "tool", round: 0, name: "get_financials", input: { company_id: NVDA }, is_error: false },
      { type: "text", round: 1, text: "# Semis note" },
      { type: "report", item: { item_id: "r1", workspace_id: "w1", name: "Report: Semis" }, verification: { ok: false, unverified: 1 } },
      { type: "done", answer: { text: NOTE, stop: "end_turn", model: "m", rounds: 2, tool_calls: [], usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }, cost_usd: null, citations: [], verification: null, actions: [] } },
    ]);
  };
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("writes a report from the workspace page and opens it", async () => {
  await render("#/workspace/w1");
  expect(container.querySelector("h1")?.textContent).toBe("Semis");
  expect(container.textContent).toContain("counts as 3 assistant questions");
  await click(button("Write a report"));
  expect(reportBodies).toEqual([{ workspace_id: "w1" }]); // the workspace only: never a user
  expect(window.location.hash).toBe("#/report/r1");
  await waitFor(".report-doc h1");
  const report = container.querySelector(".report-doc")!;
  expect(report.querySelector("h1")?.textContent).toBe("Semis note");
  // every saved item drawn in place from its saved state; the unknown embed is dropped
  const embeds = [...report.querySelectorAll<HTMLElement>(".report-embed")].map((e) => e.getAttribute("aria-label"));
  expect(embeds).toEqual(["NVDA revenue", "<b>Comps</b>"]);
  expect(report.textContent).not.toContain("[[embed:");
  const evaluate = vi.mocked(fetch).mock.calls.find(([u]) => String(u).includes("/v1/formulas/evaluate"))!;
  expect(JSON.parse(String(evaluate[1]!.body))).toMatchObject({ formula: "revenue", default_company: NVDA, view: "ttm" });
  // the comps' ungranted peer isn't read: no access, as anywhere else
  expect(report.textContent).toContain("No access to 0000320193");
  // the unverified figure is flagged; the cited filing links to EDGAR
  expect([...report.querySelectorAll(".figure.unverified")].map((e) => e.textContent)).toEqual(["$61.2B"]);
  expect(container.querySelector(".report-meta")?.textContent).toContain("1 of 2 figures unverified");
  expect(report.querySelector<HTMLAnchorElement>(`a.accession[href="https://www.sec.gov/Archives/x.htm"]`)).not.toBeNull();
  // model output is sanitized; a saved name is text
  expect(report.querySelector("[onerror]")).toBeNull();
  expect(report.querySelector(".report-embed b b")).toBeNull();
});

it("regenerates, downloads Markdown and prints", async () => {
  items.set("r1", item("r1", "report", "Report: Semis", reportState(NOTE)));
  await render("#/report/r1", ".report-doc");
  const created = vi.fn((_b: Blob) => "blob:report");
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: () => {} });
  const print = vi.fn();
  vi.stubGlobal("print", print);
  window.print = print;
  await click(button("Download Markdown"));
  const blob = created.mock.calls[0]![0] as Blob;
  const text = await new Promise<string>((done) => {
    const r = new FileReader();
    r.onload = () => done(String(r.result));
    r.readAsText(blob);
  });
  expect(text).toContain("# Semis note");
  expect(text).toContain("> **NVDA revenue**: Chart");
  expect(text).toContain(`${ACC}: https://www.sec.gov/Archives/x.htm`);
  await click(button("Print / PDF"));
  expect(print).toHaveBeenCalled();
  await click(button("Write again"));
  expect(reportBodies).toEqual([{ workspace_id: "w1", report_id: "r1" }]);
  expect(window.location.hash).toBe("#/report/r1");
});

it("says clearly when the report is over the limit, and spends nothing visible", async () => {
  reportReply = () => Response.json({ detail: "A report counts as 3 questions. You have 2 of 30 left this hour." }, { status: 429 });
  await render("#/workspace/w1");
  await click(button("Write a report"));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("A report counts as 3 questions");
  expect(window.location.hash).toBe("#/workspace/w1");
});

it("another user's report or workspace reads as not found", async () => {
  await render("#/report/nope", ".empty");
  expect(container.textContent).toContain("This report doesn't exist, or isn't yours");
  act(() => root.unmount());
  await render("#/workspace/w2", ".empty");
  expect(container.textContent).toContain("This workspace doesn't exist, or isn't yours");
});

it("opening a report from the sidebar goes to its page", async () => {
  items.set("r1", item("r1", "report", "Report: Semis", reportState(NOTE)));
  await render("#/");
  const open = [...container.querySelectorAll<HTMLButtonElement>(".side .ws-row.it .ws-open")].find((b) => b.textContent?.includes("Report: Semis"))!;
  await click(open);
  expect(window.location.hash).toBe("#/report/r1");
  expect(container.querySelector<HTMLAnchorElement>('a[aria-label^="Open workspace Semis"]')?.getAttribute("href")).toBe("#/workspace/w1");
});
