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
export const byId = new Map(COMPANIES.map((c) => [c.id, c]));
const byTicker = new Map(COMPANIES.map((c) => [c.ticker, c]));

const SRC = (c: Co) => ({ period_end: "2026-06-30", accession_no: `${c.id}-26-000040`, form: "10-Q", filed: "2026-07-30" });
const HOUR = 3_600_000;

export function screenerRow(c: Co) {
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
  /** what the next thesis check returns (agent-api's result shape); stored checks by thesis */
  checkResult: ((itemId: string) => Record<string, unknown>) | null;
  checks: Map<string, Record<string, unknown>[]>;
  /** the account's API keys (/v1/api-keys), as the Query API lists them */
  apiKeys: Record<string, unknown>[];
  /** the portfolio (query_api/portfolio.py): holdings with their lots, and journal entries */
  holdings: PortfolioHolding[];
  journal: Record<string, unknown>[];
}

interface PortfolioHolding {
  id: string;
  company_id: string | null;
  symbol: string;
  name: string | null;
  lots: { id: string; shares: string; cost_total: string; bought_on: string | null; account_label: string | null; source: string }[];
  price: { value: string; on: string; source: string } | null;
}

/** A portfolio as GET /v1/portfolio answers it: average cost, values only on a price given. */
function portfolioAnswer(holdings: PortfolioHolding[]) {
  const rows = holdings.map((h) => {
    const shares = h.lots.reduce((s, l) => s + Number(l.shares), 0);
    const cost = h.lots.reduce((s, l) => s + Number(l.cost_total), 0);
    const value = h.price ? shares * Number(h.price.value) : null;
    return {
      id: h.id, company_id: h.company_id, covered: h.company_id !== null, symbol: h.symbol, name: h.name,
      added_at: "2026-10-21T12:00:00Z", open: shares > 0, shares: String(shares), cost_basis: cost.toFixed(2),
      average_cost: shares ? String(cost / shares) : null, realised: "0.00", first_bought: h.lots.find((l) => l.bought_on)?.bought_on ?? null,
      price: h.price ? { ...h.price, provider: null, stale: false } : null,
      value: value === null ? null : value.toFixed(2), unrealised: value === null ? null : (value - cost).toFixed(2),
      unrealised_pct: value === null || !cost ? null : ((value - cost) / cost).toFixed(4),
      next_report: h.company_id ? { form: "10-Q", period_end: "2027-01-25", expected_date: "2027-02-25" } : null,
      weight: null as string | null, link: null, candidates: [], review_due: null,
      lots: h.lots.map((l) => ({ ...l, cost_per_share: String(Number(l.cost_total) / Number(l.shares)), created_at: "2026-10-21T12:00:00Z" })),
      sells: [],
    };
  });
  const total = rows.reduce((s, r) => s + Number(r.cost_basis), 0);
  for (const r of rows) r.weight = total ? (Number(r.cost_basis) / total).toFixed(4) : null;
  const priced = rows.filter((r) => r.value !== null);
  return {
    holdings: rows, currency: "USD", method: "average_cost", weight_basis: "cost", stale_after_days: 7, today: "2026-10-21",
    totals: { open: rows.length, priced: priced.length, cost_basis: total.toFixed(2),
      value: priced.length ? priced.reduce((s, r) => s + Number(r.value), 0).toFixed(2) : null,
      unrealised: priced.length ? priced.reduce((s, r) => s + Number(r.unrealised), 0).toFixed(2) : null, realised: "0.00" },
    reviews_due: 0, prices_connected: false, refresh: false,
  };
}

/** What the Query API reads from Fidelity's positions export (portfolio_import.py): account numbers
 * already dropped. */
const FIDELITY_PREVIEW = {
  positions: [
    { row: 0, symbol: "NVDA", description: "NVIDIA CORPORATION COM", shares: "1.25", cost_total: "200.00", last_price: "186.26", account_label: "Individual", company_id: NVDA, name: "NVIDIA CORP", covered: true, held: null },
    { row: 1, symbol: "MSFT", description: "MICROSOFT CORP", shares: "0.2", cost_total: null, last_price: "505.00", account_label: "ROTH IRA", company_id: MSFT, name: "MICROSOFT CORP", covered: true, held: null },
    { row: 2, symbol: "VOO", description: "VANGUARD S&P 500 ETF", shares: "0.05", cost_total: "25.00", last_price: "560.00", account_label: "Individual", company_id: null, name: null, covered: false, held: null },
  ],
  skipped: [{ symbol: "SPAXX", description: "HELD IN MONEY MARKET", reason: "cash" }, { symbol: null, description: "Pending activity", reason: "pending" }],
  downloaded: "2026-10-21", source: "fidelity",
};

/** A GitHub account's session (stored in localStorage as the app keeps one): API keys need a
 * permanent account, so Settings offers them only to it, never to the demo sandbox. */
export const GITHUB_TOKEN = "github-e2e-token";
export const GITHUB_SESSION = {
  kind: "github", token: GITHUB_TOKEN, userId: "gh-e2e", login: "e2e-analyst", companies: [],
  expiresAt: "2099-01-01T00:00:00.000Z",
};
/** The key the mocked API makes: an obvious fake. */
export const NEW_API_KEY = "trb_" + "e2e".repeat(14) + "x";

/** A thesis check as agent-api stores it: one supported claim and one of the user's own model. */
export function thesisCheckResult(itemId: string, modelId = "m1"): Record<string, unknown> {
  const acc = `${PEP}-26-000040`;
  const cite = { accession_no: acc, form: "10-Q", filed: "2026-07-30", company_id: PEP, url: "https://www.sec.gov/Archives/edgar/data/77476/x.htm", section_key: "I-2", figure: "$91.9B", figure_status: "verified", as_of: "2026-10-21T08:00:00+00:00", verified: true };
  return {
    version: 1,
    thesis: { item_id: itemId, title: "PepsiCo thesis", company_id: PEP, text_hash: "0".repeat(64), characters: 100, characters_checked: 100, truncated: false },
    summary: "Revenue checks out; the FY2027 figures are your model's.",
    claims: [
      { quote: "Revenue was $91.9B over the last twelve months.", quote_found: true, status: "supported", explanation: "The 10-Q's quarters sum to $91.9B.", citations: [cite], figures: [{ text: "$91.9B", status: "verified" }] },
      { quote: "My base case puts FY2027 revenue at $99.9B.", quote_found: true, status: "model",
        explanation: "Matches your model (PEP model): FY2027E revenue $99.9B. A forecast of yours, so the filings can't support or contradict it.",
        citations: [], figures: [{ text: "$99.9B", status: "model" }],
        model: { ref: "model-1", item_id: modelId, name: "PEP model", currency: "USD", matches: [{ figure: "$99.9B", line: "revenue", fy: 2027, case: "base", kind: "money", value: 99.9e9 }] } },
    ],
    assumptions: [], not_mentioned: [], notes: [],
    citations: [{ accession_no: acc, form: "10-Q", filed: "2026-07-30", company_id: PEP, url: null, sections: ["I-2"] }],
    verification: { ok: true, figures: [{ text: "$91.9B", status: "verified" }, { text: "$99.9B", status: "model" }], unverified_accessions: [], used_data: true, freshness: true },
    as_of: "2026-10-21T08:00:00+00:00", checked_at: "2026-10-21T12:00:00+00:00", model: "mock", questions: 4, stop: "end_turn", rounds: 3, cost_usd: null,
    filings: [],
  };
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
  const fake: Fake = { watched: new Set([NVDA, PEP]), calls: [], workspaces: [], items: new Map(), asked: [], views: {}, reports: [], checkResult: null, checks: new Map(), apiKeys: [], holdings: [], journal: [] };
  let seq = 0;
  const now = () => new Date(Date.UTC(2026, 9, 21, 12, 0, seq++)).toISOString();

  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const u = new URL(req.url());
    const method = req.method();
    // JSON bodies; the portfolio import's preview posts a CSV's text
    const body = req.postData() && req.headers()["content-type"]?.includes("application/json") ? JSON.parse(req.postData()!) : {};
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

    if (u.pathname === "/api/agent/v1/thesis-checks" && method === "POST") {
      // agent-api's check: a lookup, the saved check, done (thesis_check.py)
      const result = (fake.checkResult ?? thesisCheckResult)(body.item_id);
      const claims = result.claims as { status: string }[];
      const count = (s: string) => claims.filter((c) => c.status === s).length;
      const check = {
        check_id: `c${++seq}`, item_id: body.item_id, text_hash: (result.thesis as { text_hash: string }).text_hash, model: "mock", cost_usd: null, created_at: now(),
        counts: { supported: count("supported"), contradicted: count("contradicted"), unverifiable: count("unverifiable"), model: count("model"), flagged_assumptions: 0, not_mentioned: 0, ok: true },
        company_id: PEP, checked_at: result.checked_at, filings: [],
      };
      fake.checks.set(body.item_id, [{ ...check, result }, ...(fake.checks.get(body.item_id) ?? [])]);
      const events = [
        { type: "tool", round: 0, name: "get_financials", input: { company_id: PEP }, is_error: false },
        { type: "check", check, result },
        { type: "done", answer: {} },
      ];
      return route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""),
      });
    }

    const path = u.pathname.replace(/^\/api\/query/, "");
    const q = u.searchParams;

    let c = path.match(/^\/v1\/workspace-items\/([^/]+)\/checks$/);
    if (c) return json({ item_id: c[1], checks: (fake.checks.get(c[1]!) ?? []).map(({ result: _r, ...s }) => s) });
    c = path.match(/^\/v1\/workspace-items\/([^/]+)\/checks\/([^/]+)$/);
    if (c) {
      const one = (fake.checks.get(c[1]!) ?? []).find((x) => x.check_id === c![2]);
      return one ? json(one) : json({ detail: "check not found" }, 404);
    }

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
    const github = req.headers()["authorization"] === `Bearer ${GITHUB_TOKEN}`;
    if (path === "/v1/me" && github) {
      return json({
        user_id: GITHUB_SESSION.userId, catalog: { public: true, tiers: ["filings", "financials"], companies: COMPANIES.length }, companies: [],
        github: { login: GITHUB_SESSION.login, plan: "pro", company_ids: COMPANIES.map((c) => c.id), agent: true, agent_questions_per_day: 10 },
      });
    }
    // API keys (Settings): a GitHub account has them; the sandbox is told it can't (allowed false)
    if (path === "/v1/api-keys" && method === "GET") {
      return json({ allowed: github, max_active: 5, expiry_days: [30, 90, 365], default_expiry_days: 90, scopes: ["read", "write"],
        limits: { per_minute: 60, per_day: 10000 }, keys: github ? fake.apiKeys : [] });
    }
    if (path === "/v1/api-keys" && method === "POST") {
      const made = { key_id: `0b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c${String(10 + fake.apiKeys.length)}`, name: body.name,
        prefix: NEW_API_KEY.slice(0, 8), scopes: body.scopes, status: "active", created_at: now(),
        expires_at: new Date(Date.UTC(2026, 9, 21) + body.expires_in_days * 86_400_000).toISOString(),
        last_used_at: null, revoked_at: null, today: { requests: 0, limited: 0 } };
      fake.apiKeys = [made, ...fake.apiKeys];
      return json({ key: NEW_API_KEY, api_key: made }, 201);
    }
    const revoked = path.match(/^\/v1\/api-keys\/([^/]+)$/);
    if (revoked && method === "DELETE") {
      fake.apiKeys = fake.apiKeys.map((x) => (x.key_id === revoked[1] ? { ...x, status: "revoked", revoked_at: now() } : x));
      return json({ key_id: revoked[1], revoked: true });
    }
    // "Using the API": the Query API's generated OpenAPI of the key routes (a few of them here)
    if (path === "/v1/openapi-keys.json") {
      return json({
        openapi: "3.1.0", info: { title: "Tributary API (API keys)", version: "1", description: "",
          "x-tributary-limits": { per_minute: 60, per_day: 10000 } },
        paths: {
          "/v1/me": { get: { summary: "Who you are", description: "Your user id and what you can read.\n\nScope: read (any API key).", "x-tributary-scope": "read" } },
          "/v1/companies/search": { get: { summary: "Find companies", description: "Companies matching a ticker, a CIK or any part of a name, best match first. `q` is required; `limit` 1-25.\n\nScope: read (any API key).",
            "x-tributary-scope": "read", parameters: [{ name: "q", in: "query", required: true }, { name: "limit", in: "query" }] } },
          "/v1/companies/{company_id}/financials": { get: { summary: "Financial figures", description: "A company's figures, each with the filing it came from. `metrics`, `quarters`, `view`, `basis`, `format`.\n\nScope: read (any API key).",
            "x-tributary-scope": "read", parameters: [{ name: "company_id", in: "path" }, { name: "metrics", in: "query" }, { name: "quarters", in: "query" },
              { name: "view", in: "query" }, { name: "basis", in: "query" }, { name: "format", in: "query" }] } },
          "/v1/companies/{company_id}/sections/{section_key}": { get: { summary: "A section's text", description: "One section's text with its citation. `form`, `accession_no`, `max_chars`.\n\nScope: read (any API key).",
            "x-tributary-scope": "read", parameters: [{ name: "form", in: "query" }, { name: "accession_no", in: "query" }, { name: "max_chars", in: "query" }] } },
          "/v1/workspaces": {
            get: { summary: "Your workspaces", description: "Your workspaces, oldest first, each with its items.\n\nScope: read (any API key).", "x-tributary-scope": "read" },
            post: { summary: "Create a workspace", description: "A new workspace named `name`.\n\nScope: write (a key with read and write).", "x-tributary-scope": "write" },
          },
          "/v1/watchlist/{company_id}": {
            put: { summary: "Watch a company", description: "Adds a company to your watchlist.\n\nScope: write (a key with read and write).", "x-tributary-scope": "write" },
            delete: { summary: "Stop watching a company", description: "Removes a company from your watchlist.\n\nScope: write (a key with read and write).", "x-tributary-scope": "write" },
          },
          "/v1/metrics": { get: { summary: "Metric names", description: "Every metric name the financials route accepts.\n\nPublic: no key needed.", "x-tributary-scope": "public" } },
        },
        components: { schemas: {}, securitySchemes: { apiKey: { type: "http", scheme: "bearer" } } },
      });
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
    // F1: the Overview asks for the 8-Ks since the last report; the Earnings tab for its releases
    m = path.match(/^\/v1\/companies\/(\d{10})\/events$/);
    if (m) {
      return json({ company_id: m[1], as_of: "", since: "2026-06-30", window: "since_report", events: [], more: false, stale: [], method: "",
        latest_report: { accession_no: `${m[1]}-26-000040`, form: "10-Q", filed: "2026-07-30", period_end: "2026-06-30" } });
    }
    m = path.match(/^\/v1\/companies\/(\d{10})\/earnings$/);
    if (m && m[1] === KO) return json(koEarnings(q.get("tables") === "true"));
    if (m) return json({ company_id: m[1], as_of: "", quarters: [], method: "" });
    // F8b: the Overview's and Numbers' backlog and orders from the filings' text
    m = path.match(/^\/v1\/companies\/(\d{10})\/text-kpis$/);
    if (m) {
      const none = { points: [], conflicts: [] };
      return json({ company_id: m[1], as_of: "", since: "2016-11-09", method: "", read: { sections: 0, filings: 0, from: null },
        metrics: { backlog: { label: "Backlog", unit: "USD", span: "instant", ...none },
          orders: { label: "Orders in the quarter", unit: "USD", span: "quarter", ...none },
          orders_growth: { label: "Order growth in the quarter, year on year", unit: "percent", span: "quarter", ...none } } });
    }
    // F7b: the Filings tab's insider filings (Insider and All)
    m = path.match(/^\/v1\/companies\/(\d{10})\/insiders$/);
    if (m) return json({ company_id: m[1], filings: [], has_more: false });
    // Riley (test 6): the Statements tab's EPS and shares on one share basis
    m = path.match(/^\/v1\/companies\/(\d{10})\/per-share$/);
    if (m) return json({ company_id: m[1], view: q.get("view") ?? "quarter", basis: q.get("basis") ?? "fiscal",
      shares: q.get("shares") ?? "adjusted", as_of: "", splits: [], rows: [] });
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
        company_id: c.id, name: c.name, matched_on: "ticker", matched_alias: c.ticker, is_current: true, tickers: [c.ticker], access: ["financials", "filings"],
      })) });
    }
    if (path === "/v1/sectors") {
      return json({ note: "Tributary's own grouping of the SEC's SIC codes (not GICS)", sectors: [
        { name: "Technology", companies: 2, industries: [{ name: "Semiconductors", sic: ["3674"], companies: 1 }] }] });
    }
    if (/^\/v1\/companies\/\d{10}\/peers\/check\/\d{10}$/.test(path)) {
      return json({ company_id: path.split("/")[3], candidate_id: path.split("/").at(-1), ok: true, reason: null, annual_form: "10-K", annual_filed: "2026-02-25" });
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
    // Connect your data off: the API offers no providers (not a 404)
    if (path === "/v1/connections" && method === "GET") return json({ providers: [], connections: [] });
    // filing alerts (F6): the bell asks on every load (a count, so the phone layout test sees the
    // widest bell); Settings and Monitor list them
    if (path === "/v1/alerts/unread") return json({ unread: 12, latest: now(), last_check: { checked_at: now(), filings_through: "2026-10-21" } });
    if (path === "/v1/alerts/notifications") return json({ notifications: [], has_more: false, lookback_days: 3, pending_days: 3 });
    if (path === "/v1/alerts/settings") {
      return json({ forms: ["10-K", "10-Q", "8-K"], companies: [...fake.watched].map((c) => ({ company_id: c, sources: ["watchlist"], since: "2026-10-01", forms: ["10-K", "10-Q", "8-K"] })),
        webhooks: [], formats: ["json", "discord", "slack"], max_webhooks: 3, email: { available: false }, last_check: null,
        schedule: "every 10 minutes", lookback_days: 3, pending_days: 3 });
    }
    if (path === "/v1/alerts/deliveries") return json({ deliveries: [], retries: [300, 1800, 7200, 21600] });
    if (path === "/v1/alerts/red-lines") return json({ red_lines: [], max_per_company: 10, evaluation: [], evaluated_at: now() });
    // the portfolio and journal (query_api/portfolio.py, journal.py)
    if (path === "/v1/portfolio") return json(portfolioAnswer(fake.holdings));
    m = path.match(/^\/v1\/portfolio\/position\/(\d{10})$/);
    if (m) {
      const h = fake.holdings.find((x) => x.company_id === m![1]);
      const shares = h ? h.lots.reduce((s, l) => s + Number(l.shares), 0) : 0;
      return json({ position: h ? { holding_id: h.id, shares: String(shares), open: shares > 0, first_bought: h.lots.find((l) => l.bought_on)?.bought_on ?? null, added_at: "2026-10-21T12:00:00Z" } : null });
    }
    if (path === "/v1/portfolio/holdings" && method === "POST") {
      const c = body.company_id ? byId.get(body.company_id) : byTicker.get(body.symbol);
      const key = c ? c.id : null;
      let h = fake.holdings.find((x) => (key ? x.company_id === key : !x.company_id && x.symbol === body.symbol));
      const created = !h;
      if (!h) {
        h = { id: `0000000${++seq}-0000-4000-8000-000000000000`.slice(-36), company_id: key, symbol: c?.ticker ?? body.symbol, name: c ? null : (body.name ?? null), lots: [], price: null };
        fake.holdings.push(h);
      }
      const cost = body.lot.cost_total ?? String(Number(body.lot.cost_per_share) * Number(body.lot.shares));
      h.lots.push({ id: `l${++seq}`, shares: body.lot.shares, cost_total: Number(cost).toFixed(2), bought_on: body.lot.bought_on ?? null, account_label: body.lot.account_label ?? null, source: "typed" });
      return json({ holding_id: h.id, created, covered: !!key, company_id: key });
    }
    if (path === "/v1/portfolio/import/preview" && method === "POST") return json(FIDELITY_PREVIEW);
    if (path === "/v1/portfolio/import" && method === "POST") {
      let made = 0;
      for (const p of body.positions as { symbol: string; description: string | null; shares: string; cost_total: string; last_price: string | null; account_label: string | null }[]) {
        const c = byTicker.get(p.symbol) ?? null;
        let h = fake.holdings.find((x) => (c ? x.company_id === c.id : !x.company_id && x.symbol === p.symbol));
        if (!h) {
          h = { id: `0000000${++seq}-0000-4000-8000-000000000000`.slice(-36), company_id: c?.id ?? null, symbol: p.symbol, name: c ? null : p.description, lots: [], price: null };
          fake.holdings.push(h);
          made++;
        }
        h.lots.push({ id: `l${++seq}`, shares: p.shares, cost_total: Number(p.cost_total).toFixed(2), bought_on: null, account_label: p.account_label, source: "fidelity" });
        if (c && p.last_price && body.price_on) h.price = { value: p.last_price, on: body.price_on, source: "fidelity" };
      }
      return json({ holdings_added: made, lots_added: body.positions.length, prices_saved: 0 });
    }
    if (path === "/v1/portfolio/journal" && method === "GET") {
      const company = u.searchParams.get("company_id");
      const symbol = u.searchParams.get("symbol");
      const entries = fake.journal.filter((e) => (company ? e.company_id === company : symbol ? e.symbol === symbol : true));
      return json({ entries: [...entries].reverse(), has_more: false, edit_hours: 24, kinds: ["bought", "added", "trimmed", "sold", "review", "note"] });
    }
    if (path === "/v1/portfolio/journal" && method === "POST") {
      const at = now();
      const e = { id: `e${++seq}`, company_id: body.company_id ?? null, symbol: body.symbol ?? null, kind: body.kind, decided_on: body.decided_on ?? "2026-10-21",
        why: body.why, sell_rules: body.sell_rules ?? null, red_lines: [], confidence: body.confidence ?? null, corrects: body.corrects ?? null,
        created_at: at, edited_at: null, editable_until: "2026-10-22T12:00:00Z", kept: false };
      fake.journal.push(e);
      return json({ entry: e });
    }
    if (path === "/v1/status") return json({ last_accepted_at: now(), last_ingested_at: now() });
    // the public status page's data: Data status, the front door's figures, and Home's "checks
    // last ran" (#24: the run's time, from the same source as Data status)
    if (path === "/v1/status/data") {
      const recorded = { recorded_at: "2026-10-18T20:51:00Z", models: ["claude-haiku-5-5"], commit: "02ca3b2", committed: "2026-10-18" };
      return json({
        as_of: now(), max_age_seconds: 60,
        freshness: { last_accepted_at: now(), last_ingested_at: now() },
        ingest: { window_days: 7, window_start: "2026-10-14T15:00:00Z", filings: 40000, via_feed: 39000, live_pct: 97.5, p50_seconds: 43, p95_seconds: 108 },
        completeness: { window_days: 30, window_start: "2026-09-21", days_checked: 21, indexed: 43012, missing: 1, republished: 1, days_with_missing: 1, last_checked_at: now() },
        coverage: { companies_with_financials: 6597, documents: 632923, indexed_filings: 632164, sections: 3555257, last_indexed_at: now() },
        quality: {
          companies_checked: 7070, last_checked_at: now(), figures: 13_790_000,
          checks: [
            { check: "impossible_value", severity: "error", tier: "error", findings: 1410, companies: 517, per_1000: 0.102 },
            { check: "tag_disagreement", severity: "warning", tier: "warning", findings: 2900, companies: 1000, per_1000: 0.21 },
            { check: "mistagged_fact", severity: "info", tier: "kept_out", findings: 634, companies: 262, per_1000: 0.046 },
            { check: "series_gap", severity: "info", tier: "coverage", findings: 19534, companies: 3865, per_1000: 1.417 },
            { check: "restatement", severity: "info", tier: "info", findings: 191772, companies: 5288, per_1000: 13.91 },
          ],
        },
        parser: {
          documents_checked: 580000, last_checked_at: now(),
          checks: [{ check: "invalid_key", severity: "error", documents: 6300, per_1000: 10.862 }],
        },
        recorded: {
          adversarial: { ...recorded, attacks: 59, categories: 10, leaks: 0, controls: 4, controls_answered: 4, report: "tests/adversarial/report.md" },
          citations: { ...recorded, questions: 32, verified_answers: 32, data_answers: 30, data_answers_citing: 30, figures: 128, figures_traced: 128, untraced_accessions: 0, report: "tests/citations/report.md" },
          golden: { figures: 149, companies: 5, filings: 5, checks: { absent: 2, annual: 129, annual_silver: 18 }, hand_checked: 21, readme: "tests/golden/README.md", commit: "9d9f318", committed: "2026-10-26" },
        },
      });
    }
    m = path.match(/^\/v1\/companies\/(\d{10})\/expected-filings$/);
    if (m) return json({ company_id: m[1], as_of: "", method: "", expected: [] });
    if (u.pathname === "/api/agent/v1/allowance") {
      return json({ per_hour: 30, left_this_hour: 30, per_day: 100, left_today: 100, network_per_hour: 60, report_questions: 3, check_questions: 4, check_seconds: 120, paused: false });
    }

    return json({ detail: "not found in the e2e mock" }, 404);
  });
  return fake;
}

/** "Try the demo" from the front door, up to Home with the sandbox banner. */
/** Coca-Cola's Q3 2026 release as /earnings answers it: its guidance block, with the table it quotes
 * as a grid when asked with tables=true (parser v7; Query API release_tables.py, the shape of its
 * test on the same release). */
function koEarnings(tables: boolean) {
  const acc = "0001628280-26-067403";
  const text = "Full Year 2026 Guidance\nCurrent Prior\nOrganic revenues (non-GAAP) At least 5% growth Approx. 5% growth\nUnderlying effective tax rate 19.7% 19.9%";
  const cite = { accession_no: acc, form: "8-K", filed: "2026-10-27", filing_url: null, section_key: "EX-99.1", char_start: 1200, char_end: 1200 + text.length };
  const cell = (t: string, col: number) => ({ text: t, col, span: 1 });
  const outlook = {
    kind: "outlook", heading: "Full Year 2026 Guidance", text, char_start: 1200, char_end: 1200 + text.length, truncated: false, citation: cite,
    ...(tables ? { parts: [
      { lines: ["Full Year 2026 Guidance"] },
      { table: { columns: 3, char_start: 1224, char_end: 1200 + text.length,
        head: [[cell("Current", 1), cell("Prior", 2)]],
        body: [[cell("Organic revenues (non-GAAP)", 0), cell("At least 5% growth", 1), cell("Approx. 5% growth", 2)],
          [cell("Underlying effective tax rate", 0), cell("19.7%", 1), cell("19.9%", 2)]] } },
    ] } : {}),
  };
  return { company_id: KO, as_of: "", method: "", quarters: [{
    period_end: "2026-09-26", estimated: false, report: { accession_no: `${KO}-26-000050`, form: "10-Q", filed: "2026-10-28" },
    release: { accession_no: acc, form: "8-K", filed: "2026-10-27", accepted_at: null, items: [{ item: "2.02", title: "Results of Operations and Financial Condition", kind: "earnings" }],
      section_key: "EX-99.1", title: "Press release", total_chars: 72509, document_url: null, filing_url: null },
    headline: null, outlook: [outlook], adjustments: [], other_releases: [] }] };
}

export async function startDemo(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Try the demo" }).click();
  await page.locator(".sandbox-banner").waitFor();
}
