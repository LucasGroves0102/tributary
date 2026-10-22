/** A fake Query API and agent API for the end-to-end flows: every /api/* request the built app
 * makes is answered here (Playwright route interception), so no backend runs. Shapes follow
 * src/types.ts; state (watchlist, saved work) lives per test. */

import type { Page, Route } from "@playwright/test";

export const NVDA = "0001045810";
export const MSFT = "0000789019";
export const PEP = "0000077476";
export const KO = "0000021344";

interface Co {
  id: string;
  ticker: string;
  name: string;
  industry: string;
  sic: string;
  fye: string;
  revenue: number;
  growth: number;
  margin: number;
}

const COMPANIES: Co[] = [
  { id: NVDA, ticker: "NVDA", name: "NVIDIA CORP", industry: "Semiconductors & Related Devices", sic: "3674", fye: "0126", revenue: 165.2e9, growth: 0.69, margin: 0.62 },
  { id: MSFT, ticker: "MSFT", name: "MICROSOFT CORP", industry: "Services-Prepackaged Software", sic: "7372", fye: "0630", revenue: 281.7e9, growth: 0.15, margin: 0.45 },
  { id: PEP, ticker: "PEP", name: "PEPSICO INC", industry: "Beverages", sic: "2080", fye: "1231", revenue: 91.9e9, growth: 0.01, margin: 0.13 },
  { id: KO, ticker: "KO", name: "COCA COLA CO", industry: "Beverages", sic: "2080", fye: "1231", revenue: 47.1e9, growth: 0.03, margin: 0.29 },
];
const byId = new Map(COMPANIES.map((c) => [c.id, c]));
const byTicker = new Map(COMPANIES.map((c) => [c.ticker, c]));

const SRC = (c: Co) => ({ period_end: "2026-06-30", accession_no: `${c.id}-26-000040`, form: "10-Q", filed: "2026-07-30" });
const HOUR = 3_600_000;

function screenerRow(c: Co) {
  const values: Record<string, string | null> = {
    revenue_ttm: String(c.revenue),
    revenue_growth_ttm: String(c.growth),
    gross_margin_ttm: "0.55",
    operating_margin_ttm: String(c.margin),
    net_margin_ttm: String(c.margin * 0.8),
    free_cash_flow_ttm: String(c.revenue * 0.2),
    fcf_margin_ttm: "0.2",
    net_debt_q: String(-c.revenue * 0.1),
    debt_to_equity_q: "0.4",
    revenue_q: String(c.revenue / 4),
  };
  return {
    company_id: c.id, name: c.name, tickers: [c.ticker], exchanges: ["Nasdaq"], sic: c.sic, industry: c.industry,
    fiscal_year_end: c.fye, filer_category: "large_accelerated", filer_categories: null, state_of_incorporation: "DE",
    access: ["financials", "filings"], values,
    sources: Object.fromEntries(Object.keys(values).map((k) => [k, SRC(c)])),
  };
}

function watchEntry(c: Co) {
  const r = screenerRow(c);
  return {
    ...r, added_at: "2026-10-01T00:00:00Z",
    last_filing: { accession_no: `${c.id}-26-000040`, form: "10-Q", filing_date: "2026-07-30", period_end: "2026-06-30" },
    next_expected: null,
  };
}

/** Four calendar quarters of one value, each point citing its filing. */
function series(formula: string, company: string | undefined, basis: string, view: string) {
  const c = byId.get(company ?? "") ?? COMPANIES[0]!;
  const ratio = /margin|\//.test(formula);
  const quarters = [["CY2025 Q3", "2025-09-30"], ["CY2025 Q4", "2025-12-31"], ["CY2026 Q1", "2026-03-31"], ["CY2026 Q2", "2026-06-30"]];
  return {
    formula, unit: ratio ? "ratio" : "USD", basis, view, alignment: basis, as_of: "2026-10-21T12:00:00Z",
    inputs: [{ input: 0, ref: formula, ticker: null, company_id: c.id, metric: "revenue", span: "quarter", lag_quarters: 0, unit: "USD" }],
    points: quarters.map(([period, end], i) => {
      const value = ratio ? String(c.margin + i * 0.01) : String(Math.round((c.revenue / 4) * (1 + i * 0.05)));
      return {
        period, period_key: 8102 + i, period_end: end, value, source_accessions: [],
        inputs: [{
          input: 0, period_start: null, period_end: end, value, value_source: "reported",
          citation: { accession_no: `${c.id}-26-00003${i}`, form: "10-Q", filed: "2026-07-30", source_accessions: null, filing_url: null },
          freshness: { filed: "2026-07-30", ingested_at: "2026-07-30T12:00:00Z" },
        }],
      };
    }),
  };
}

export interface Fake {
  watched: Set<string>;
  /** every request path (with query) the app made, for assertions */
  calls: string[];
  workspaces: { workspace_id: string; name: string; created_at: string; updated_at: string }[];
  items: Map<string, Record<string, unknown>>;
  asked: string[];
  /** saved view preferences (/v1/views), by view */
  views: Record<string, unknown>;
  /** bodies of POST /api/agent/v1/reports */
  reports: Record<string, unknown>[];
}

/** The report the mocked agent writes: every saved item embedded, one figure it can't verify. */
function reportState(workspaceId: string, items: Record<string, unknown>[]) {
  const refs = items.map((i, n) => ({ ref: `item-${n + 1}`, item_id: i.item_id, kind: i.kind, name: i.name, state: i.state }));
  const acc = `${PEP}-26-000040`;
  const markdown = [
    "# Beverages: margins hold",
    "## Summary",
    `PepsiCo's revenue was $91.9B over the last twelve months (10-Q filed 2026-07-30, ${acc}, Revenues). Next year it may reach $99.9B.`,
    ...refs.flatMap((r) => [`## ${r.name}`, `[[embed:${r.ref}]]`, "What it shows, cited above."]),
    "Sources and freshness: latest filing 2026-07-30, as of 2026-10-21.",
  ].join("\n\n");
  return {
    workspace_id: workspaceId, markdown, items: refs,
    citations: [{ accession_no: acc, form: "10-Q", filed: "2026-07-30", company_id: PEP, url: "https://www.sec.gov/Archives/edgar/data/77476/x.htm", sections: [] }],
    verification: { ok: false, figures: [{ text: "$91.9B", status: "verified" }, { text: "$99.9B", status: "unverified" }], unverified_accessions: [], used_data: true, freshness: true },
    as_of: "2026-10-21T08:00:00+00:00", written_at: "2026-10-21T12:00:00+00:00", model: "mock", questions: 3, stop: "end_turn",
  };
}

export async function mockApi(page: Page): Promise<Fake> {
  const fake: Fake = { watched: new Set([NVDA, PEP]), calls: [], workspaces: [], items: new Map(), asked: [], views: {}, reports: [] };
  let seq = 0;
  const now = () => new Date(Date.UTC(2026, 9, 21, 12, 0, seq++)).toISOString();

  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const u = new URL(req.url());
    const method = req.method();
    const body = req.postData() ? JSON.parse(req.postData()!) : {};
    fake.calls.push(`${method} ${u.pathname}${u.search}`);
    const json = (data: unknown, status = 200) => route.fulfill({ status, json: data });

    if (u.pathname === "/api/agent/v1/ask") {
      fake.asked.push(body.question);
      const events = [
        { type: "text", round: 0, text: "PepsiCo's revenue was **$91.9B** over the last twelve months." },
        { type: "done", answer: {
          text: "PepsiCo's revenue was **$91.9B** over the last twelve months.", stop: "end_turn", model: "mock", rounds: 1,
          tool_calls: [], usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
          cost_usd: null, citations: [], verification: null, actions: [],
        } },
      ];
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""),
      });
    }

    if (u.pathname === "/api/agent/v1/reports") {
      fake.reports.push(body);
      const saved = [...fake.items.values()].filter((i) => i.workspace_id === body.workspace_id && i.kind !== "report");
      const id = body.report_id ?? `r${++seq}`;
      fake.items.set(id, {
        item_id: id, workspace_id: body.workspace_id, kind: "report", name: "Report: Beverages", created_at: now(), updated_at: now(),
        opened_at: null, state: reportState(body.workspace_id, saved),
      });
      const events = [
        { type: "tool", round: 0, name: "get_financials", input: { company_id: PEP }, is_error: false },
        { type: "text", round: 1, text: "# Beverages: margins hold" },
        { type: "report", item: { item_id: id, workspace_id: body.workspace_id, name: "Report: Beverages" }, verification: { ok: false, unverified: 1 } },
        { type: "done", answer: {
          text: "", stop: "end_turn", model: "mock", rounds: 2, tool_calls: [],
          usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
          cost_usd: null, citations: [], verification: null, actions: [],
        } },
      ];
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""),
      });
    }

    const path = u.pathname.replace(/^\/api\/query/, "");
    const q = u.searchParams;

    if (path === "/v1/views" && method === "GET") {
      return json({
        views: { home: null, screener: null, watchlist: null, ...fake.views },
        options: {
          home_cards: ["status", "recent", "watchlist", "filings", "expected", "notes"],
          columns: [
            { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" },
            { key: "revenue_growth_ttm", metric: "revenue_growth", span: "ttm", unit: "ratio" },
            { key: "gross_margin_ttm", metric: "gross_margin", span: "ttm", unit: "ratio" },
            { key: "operating_margin_ttm", metric: "operating_margin", span: "ttm", unit: "ratio" },
            { key: "net_margin_ttm", metric: "net_margin", span: "ttm", unit: "ratio" },
            { key: "free_cash_flow_ttm", metric: "free_cash_flow", span: "ttm", unit: "USD" },
            { key: "net_debt_q", metric: "net_debt", span: "quarter", unit: "USD" },
            { key: "debt_to_equity_q", metric: "debt_to_equity", span: "quarter", unit: "ratio" },
          ],
          max_columns: { screener: 12, watchlist: 8 },
        },
      });
    }
    const v = path.match(/^\/v1\/views\/(home|screener|watchlist)$/);
    if (v && method === "PUT") {
      fake.views[v[1]!] = body.state;
      return json({ view: v[1], state: body.state });
    }
    if (v && method === "DELETE") {
      delete fake.views[v[1]!];
      return json({ view: v[1], reset: true });
    }

    if (path === "/v1/demo/sandbox" && method === "POST") {
      return json({
        user_id: "demo-e2e", token: "sandbox-token", expires_in: 86400,
        expires_at: new Date(Date.now() + 24 * HOUR).toISOString(), plan: "pro",
        companies: COMPANIES.map((c) => ({ company_id: c.id, ticker: c.ticker })), watchlist: [NVDA, PEP],
      }, 201);
    }
    if (path === "/v1/demo/sandbox") {
      return json({ ttl_hours: 24, plan: "pro", tickers: COMPANIES.map((c) => c.ticker), agent: true, agent_questions_per_day: 10 });
    }
    if (path === "/v1/me") {
      return json({
        user_id: "demo-e2e",
        companies: COMPANIES.map((c) => ({ company_id: c.id, name: c.name, industry: c.industry, tiers: ["filings", "financials"] })),
        sandbox: { expires_at: new Date(Date.now() + 24 * HOUR).toISOString(), plan: "pro", companies: 4, agent: true, agent_questions_per_day: 10 },
      });
    }
    if (path === "/v1/watchlist" && method === "GET") {
      if (q.get("format") === "csv") return route.fulfill({ status: 200, contentType: "text/csv", body: "company_id\n" });
      return json({ as_of: "", columns: [], expected_method: "", companies: [...fake.watched].map((id) => watchEntry(byId.get(id)!)) });
    }
    let m = path.match(/^\/v1\/watchlist\/(\d{10})$/);
    if (m) {
      if (method === "PUT") fake.watched.add(m[1]!);
      else fake.watched.delete(m[1]!);
      return json(method === "PUT" ? { company_id: m[1], added: true } : { company_id: m[1], removed: true });
    }
    if (path === "/v1/filings/latest") return json({ as_of: "", filings: [] });
    if (path === "/v1/calendar/expected-filings") return json({ as_of: "", through: "", scope: q.get("scope"), method: "", filings: [] });
    if (path === "/v1/filing-index" || /^\/v1\/companies\/\d{10}\/filing-index$/.test(path)) {
      return json({ as_of: "", forms: [], from: null, to: null, limit: 25, offset: 0, has_more: false, filings: [] });
    }
    if (/^\/v1\/companies\/\d{10}\/financials$/.test(path)) return json({ as_of: "", rows: [] });
    m = path.match(/^\/v1\/companies\/(\d{10})\/data-notes$/);
    if (m || path === "/v1/data-notes") {
      const id = m?.[1] ?? PEP;
      return json({ as_of: "", checked_at: "2026-10-21T06:00:00Z", companies: COMPANIES.length, notes: [{
        company_id: id, kind: "restatement", severity: "info", metric: "revenue", period_end: "2025-06-14", date: "2026-02-03",
        detail: { original_filed: "2025-07-10", restated_filed: "2026-02-03", change: -0.012 }, checked_at: "2026-10-21T06:00:00Z",
        filings: [
          { role: "original", accession_no: `${id}-25-000020`, form: "10-Q", filed: "2025-07-10", filing_url: null },
          { role: "restated", accession_no: `${id}-26-000010`, form: "10-K", filed: "2026-02-03",
            filing_url: `https://www.sec.gov/Archives/edgar/data/${Number(id)}/${id}26000010/` },
        ],
      }] });
    }
    if (path === "/v1/companies/search") {
      const s = (q.get("q") ?? "").toUpperCase();
      return json({ results: COMPANIES.filter((c) => c.ticker.startsWith(s) || c.name.includes(s)).map((c) => ({
        company_id: c.id, name: c.name, matched_on: "ticker", matched_alias: c.ticker, is_current: true, access: ["financials", "filings"],
      })) });
    }
    if (path === "/v1/screener/columns") {
      return json({
        columns: [
          { key: "revenue_q", metric: "revenue", span: "quarter", unit: "USD" },
          { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" },
          { key: "operating_margin_ttm", metric: "operating_margin", span: "ttm", unit: "ratio" },
          { key: "net_income_ttm", metric: "net_income", span: "ttm", unit: "USD" },
          { key: "weighted_shares_diluted_q", metric: "weighted_shares_diluted", span: "quarter", unit: "shares" },
        ],
        filer_categories: [], bands: [],
      });
    }
    if (path === "/v1/screener") {
      if (q.get("format") === "csv") return route.fulfill({ status: 200, contentType: "text/csv", body: "company_id,name\n" });
      const ids = q.getAll("company_id");
      const text = (q.get("q") ?? "").toUpperCase();
      let rows = COMPANIES.filter((c) => (!ids.length || ids.includes(c.id)) && (!text || c.ticker === text || c.name.includes(text)));
      const sort = q.get("sort") ?? "-revenue_ttm";
      const key = sort.replace(/^-/, "");
      const val = (c: Co) => (key === "name" ? c.name : key === "revenue_growth_ttm" ? c.growth : key === "operating_margin_ttm" ? c.margin : c.revenue);
      rows = [...rows].sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * (sort.startsWith("-") ? -1 : 1));
      return json({ as_of: "", total: rows.length, limit: 50, offset: 0, sort, columns: [], rows: rows.map(screenerRow) });
    }
    m = path.match(/^\/v1\/companies\/(\d{10})\/peers$/);
    if (m) {
      const c = byId.get(m[1]!)!;
      return json({
        company_id: c.id, as_of: "", columns: [], method: "Same 4-digit SIC first, then revenue band",
        company: { ...screenerRow(c), revenue_band: ">=$100B" },
        peers: COMPANIES.filter((p) => p.id !== c.id).map((p) => ({
          ...screenerRow(p), match: { sic_level: p.sic === c.sic ? 4 : 2, same_filer_category: true, revenue_band: null, same_revenue_band: false },
        })),
      });
    }
    if (path === "/v1/formulas/validate") return json({ valid: true, unit: "ratio", alignment: body.basis, ast: {}, inputs: [] });
    if (path === "/v1/formulas/evaluate") {
      const tickers = [...String(body.formula).matchAll(/\b([A-Z]{1,5})\./g)].map((x) => byTicker.get(x[1]!)?.id);
      return json(series(body.formula, body.default_company ?? tickers[0], body.basis, body.view));
    }

    // saved work
    const summary = (i: Record<string, unknown>) => {
      const { state: _s, workspace_name: _w, ...rest } = i;
      return rest;
    };
    const listed = () => fake.workspaces.map((w) => ({
      ...w, items: [...fake.items.values()].filter((i) => i.workspace_id === w.workspace_id).map(summary),
    }));
    if (path === "/v1/workspaces" && method === "GET") return json({ workspaces: listed() });
    if (path === "/v1/workspaces" && method === "POST") {
      const w = { workspace_id: `w${++seq}`, name: body.name, created_at: now(), updated_at: now() };
      fake.workspaces.push(w);
      return json({ ...w, items: [] }, 201);
    }
    m = path.match(/^\/v1\/workspaces\/([^/]+)$/);
    if (m && method === "GET") {
      const w = fake.workspaces.find((x) => x.workspace_id === m![1]);
      if (!w) return json({ detail: "workspace not found" }, 404);
      const items = [...fake.items.values()].filter((i) => i.workspace_id === w.workspace_id)
        .map((i) => ({ ...summary(i), state: i.kind === "report" ? null : i.state }));
      return json({ ...w, items });
    }
    m = path.match(/^\/v1\/workspaces\/([^/]+)\/items$/);
    if (m && method === "POST") {
      const item = { item_id: `i${++seq}`, workspace_id: m[1], kind: body.kind, name: body.name, created_at: now(), updated_at: now(), opened_at: null, state: body.state };
      fake.items.set(item.item_id, item);
      return json(item, 201);
    }
    m = path.match(/^\/v1\/workspace-items\/([^/]+)(\/open)?$/);
    if (m) {
      const item = fake.items.get(m[1]!);
      if (!item) return json({ detail: "item not found" }, 404);
      if (m[2]) item.opened_at = now();
      if (method === "PUT") Object.assign(item, body, { updated_at: now() });
      const ws = fake.workspaces.find((w) => w.workspace_id === item.workspace_id);
      return json({ ...item, workspace_name: ws?.name });
    }
    if (path === "/v1/recent" && method === "GET") {
      const items = [...fake.items.values()].filter((i) => i.opened_at).map((i) => ({
        ...summary(i), workspace_name: fake.workspaces.find((w) => w.workspace_id === i.workspace_id)?.name ?? "",
      }));
      return json({ items, filings: [] });
    }
    if (path === "/v1/recent/filings") return json({ recorded: true });

    return json({ detail: "not found in the e2e mock" }, 404);
  });
  return fake;
}

/** "Try the demo" from the front door, up to Home with the sandbox banner. */
export async function startDemo(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Try the demo" }).click();
  await page.locator(".sandbox-banner").waitFor();
}
