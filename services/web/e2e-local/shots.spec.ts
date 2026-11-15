// Throwaway (never committed): screenshots of each screen at 1440 light and dark and 390 light, over the
// e2e mock API plus richer figures. SHOTS=name,name picks screens; OUT is the folder.
import { readFileSync } from "node:fs";
import { test, type Page } from "@playwright/test";
import { byId, KO, mockApi, MSFT, NVDA, PEP, startDemo } from "../e2e/mockApi";

const OUT = process.env.OUT ?? "../../shots";
const ONLY = (process.env.SHOTS ?? "").split(",").filter(Boolean);
const WIDTHS = (process.env.WIDTHS ?? "1440-light,1440-dark,390-light").split(",");

const FLOW = ["revenue", "cost_of_revenue", "gross_profit", "research_and_development", "sga", "operating_income", "interest_expense",
  "pretax_income", "income_tax", "net_income", "operating_cash_flow", "capex", "free_cash_flow", "dividends_paid", "share_buybacks",
  "investing_cash_flow", "financing_cash_flow", "change_in_cash", "depreciation_amortization", "stock_based_compensation"];
const SHARE = { revenue: 1, cost_of_revenue: 0.42, gross_profit: 0.58, research_and_development: 0.1, sga: 0.08, interest_expense: 0.004,
  income_tax: 0.06, operating_cash_flow: 0.36, capex: 0.04, free_cash_flow: 0.32, dividends_paid: 0.01, share_buybacks: 0.12,
  investing_cash_flow: -0.1, financing_cash_flow: -0.15, change_in_cash: 0.03, depreciation_amortization: 0.02, stock_based_compensation: 0.04 } as Record<string, number>;

function quarters(n: number) {
  const out: [string, string][] = [];
  for (let i = n - 1; i >= 0; i--) {
    const end = new Date(Date.UTC(2026, 6 - 3 * i, 0));
    const start = new Date(Date.UTC(2026, 6 - 3 * i - 3, 1));
    out.push([start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)]);
  }
  return out;
}

function financials(id: string, n: number, metrics: string[], view: string) {
  const c = byId.get(id) ?? byId.get(NVDA)!;
  const qs = quarters(n);
  const rows: Record<string, unknown>[] = [];
  qs.forEach(([start, end], i) => {
    const rev = (c.revenue / 4) * Math.pow(1.05, i - n + 1) * (view === "ttm" ? 3.8 : 1);
    const opm = c.margin + 0.02 * Math.sin(i);
    const vals: Record<string, number> = {};
    for (const m of FLOW) vals[m] = rev * (SHARE[m] ?? 0);
    vals.operating_income = rev * opm;
    vals.pretax_income = vals.operating_income * 1.02;
    vals.net_income = vals.pretax_income - vals.income_tax!;
    vals.weighted_shares_diluted = 2.45e9;
    vals.eps_diluted = vals.net_income / 2.45e9;
    vals.cash = rev * 0.3; vals.short_term_investments = rev * 0.5; vals.cash_and_investments = rev * 0.8;
    vals.receivables = rev * 0.4; vals.inventory = rev * 0.2; vals.current_assets = rev * 1.6; vals.total_assets = rev * 3;
    vals.current_liabilities = rev * 0.5; vals.accounts_payable = rev * 0.15; vals.long_term_debt = rev * 0.2;
    vals.total_liabilities = rev * 0.9; vals.stockholders_equity = rev * 2.1; vals.net_debt = vals.long_term_debt - vals.cash_and_investments;
    vals.cash_and_restricted_cash = rev * 0.31;
    vals.gross_margin = 0.58; vals.operating_margin = opm; vals.net_margin = vals.net_income / rev; vals.fcf_margin = 0.32;
    vals.revenue_growth = 0.2 + 0.05 * Math.cos(i); vals.net_income_growth = 0.25; vals.roe = 0.3; vals.debt_to_equity = 0.1;
    vals.current_ratio = 3.2; vals.payout_ratio = 0.03;
    for (const [metric, v] of Object.entries(vals)) {
      if (metrics.length && !metrics.includes(metric)) continue;
      rows.push({
        metric, period_start: start, period_end: end, fiscal_period: `FY${end.slice(0, 4)} Q${Math.floor(Number(end.slice(5, 7)) / 3.01) + 1}`,
        calendar_period: `CY${end.slice(0, 4)} Q${Math.floor(Number(end.slice(5, 7)) / 3.01) + 1}`, value: String(v),
        unit: /margin|growth|roe|ratio|to_equity/.test(metric) ? "ratio" : metric === "eps_diluted" ? "USD/shares" : metric.includes("shares") ? "shares" : "USD",
        value_source: i % 4 === 3 && FLOW.includes(metric) ? "derived" : "reported",
        citation: { accession_no: `${id}-26-0000${10 + i}`, form: i % 4 === 3 ? "10-K" : "10-Q", filed: end, xbrl_tag: "Revenues", source_accessions: null, filing_url: null },
        freshness: { filed: end, ingested_at: `${end}T12:00:00Z` },
      });
    }
  });
  return { as_of: "2026-10-21T12:00:00Z", rows };
}

function filingIndex(id: string) {
  const forms = ["10-Q", "8-K", "8-K", "10-Q", "4", "8-K", "10-K", "8-K", "10-Q", "4", "10-Q", "8-K"];
  return {
    company_id: id, as_of: "2026-10-21T12:00:00Z", forms: [], from: null, to: null, limit: 25, offset: 0, has_more: false,
    filings: forms.map((form, i) => {
      const d = new Date(Date.UTC(2026, 7 - i, 26)).toISOString().slice(0, 10);
      return { accession_no: `${id}-26-0001${10 + i}`, company_id: id, name: byId.get(id)?.name ?? null, form, filed: d,
        accepted_at: null, period_of_report: d, filing_url: null, document_url: null, sections: form === "4" ? 0 : 12, sections_available: form !== "4" };
    }),
  };
}

async function richer(page: Page) {
  await page.route("**/api/query/v1/**", async (route) => {
    const u = new URL(route.request().url());
    const path = u.pathname.replace(/^\/api\/query/, "");
    let m = path.match(/^\/v1\/companies\/(\d{10})\/financials$/);
    if (m) {
      const n = Math.min(Number(u.searchParams.get("quarters") ?? 12), 24);
      return route.fulfill({ json: financials(m[1]!, n, u.searchParams.getAll("metrics").flatMap((s) => s.split(",")), u.searchParams.get("view") ?? "quarter") });
    }
    m = path.match(/^\/v1\/companies\/(\d{10})\/filing-index$/);
    if (m) return route.fulfill({ json: filingIndex(m[1]!) });
    m = path.match(/^\/v1\/filings\/([^/]+)\/recap$/);
    if (m) {
      const recap = JSON.parse(readFileSync("src/company/fixtures/vrt-recap.json", "utf8"));
      return route.fulfill({ json: { ...recap, accession_no: m[1], company_id: m[1]!.slice(0, 10) } });
    }
    m = path.match(/^\/v1\/companies\/(\d{10})\/sections\/([^/]+)$/);
    if (m) {
      const text = readFileSync("src/reader/fixtures/nvda-10k-2026-item7-excerpt.txt", "utf8");
      return route.fulfill({ json: { sections: [{ section_key: decodeURIComponent(m[2]!), title: "Business", text: text.slice(0, Number(u.searchParams.get("max_chars") ?? 5000)), truncated: true, total_chars: text.length,
        citation: { accession_no: `${m[1]}-26-000005`, form: "10-K", filed: "2026-02-25", document_url: null, filing_url: null, period_of_report: "2026-01-25" }, freshness: { filed: "2026-02-25", ingested_at: "2026-02-25T12:00:00Z" } }] } });
    }
    if (path === "/v1/auth/providers") return route.fulfill({ json: { github: false, password: true } });
    m = path.match(/^\/v1\/companies\/(\d{10})\/model$/);
    if (m) return route.fulfill({ json: JSON.parse(readFileSync("src/models/fixtures/nvda-run.json", "utf8")).run });
    if (path === "/v1/filings/latest") {
      return route.fulfill({ json: { as_of: "", filings: [NVDA, PEP, KO, MSFT].map((id, i) => ({ company_id: id, name: byId.get(id)!.name,
        accession_no: `${id}-26-00020${i}`, form: i % 2 ? "8-K" : "10-Q", filed: "2026-10-2" + i, period_of_report: "2026-09-30", document_url: null, sections: 8, ingested_at: "2026-10-21T12:00:00Z" })) } });
    }
    return route.fallback();
  });
}

interface Shot { name: string; hash: string; then?: (page: Page) => Promise<void> }
const NOW = "2026-10-21T12:00:00Z";
const PROJECT = { workspace_id: "p1", name: "Nvidia", created_at: NOW, updated_at: NOW, project: { companyId: NVDA, peers: [PEP], notes: "", done: ["business"] } };
const MODEL_STATE = JSON.parse(readFileSync("src/models/fixtures/nvda-run.json", "utf8")).state;
const THESIS_MD = "# Nvidia\n\n## Core thesis\n\nData center demand keeps growing faster than supply.\n\n## Pillars\n\n### Pillar 1\n\nMargins hold above 60%.\n\n## Risks and mitigants\n\nExport controls.\n";
async function seed(fake: Awaited<ReturnType<typeof mockApi>>) {
  fake.workspaces.push(PROJECT as never, { workspace_id: "w1", name: "Semis", created_at: NOW, updated_at: NOW });
  fake.items.set("m1", { item_id: "m1", workspace_id: "p1", kind: "model", name: "NVDA model", created_at: NOW, updated_at: NOW, opened_at: null, state: MODEL_STATE });
  fake.items.set("t1", { item_id: "t1", workspace_id: "p1", kind: "thesis", name: "NVDA thesis", created_at: NOW, updated_at: NOW, opened_at: null, state: { companyId: NVDA, markdown: THESIS_MD } });
  fake.items.set("c1", { item_id: "c1", workspace_id: "w1", kind: "chart", name: "NVDA revenue", created_at: NOW, updated_at: NOW, opened_at: null,
    state: { title: "", layers: [{ id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L" }], nextId: 2, range: 3 } });
}
const SCREENS: Shot[] = [
  { name: "analysis-business", hash: "#/analysis/p1/business" },
  { name: "analysis-numbers", hash: "#/analysis/p1/numbers" },
  { name: "analysis-changes", hash: "#/analysis/p1/changes" },
  { name: "analysis-model", hash: "#/analysis/p1/model" },
  { name: "analysis-thesis", hash: "#/analysis/p1/thesis" },
  { name: "analysis-monitor", hash: "#/analysis/p1/monitor" },
  { name: "workspace", hash: "#/workspace/w1" },
  { name: "home", hash: "#/" },
  { name: "companies", hash: "#/companies" },
  { name: "nvda-overview", hash: `#/company/${NVDA}` },
  { name: "nvda-statements", hash: `#/company/${NVDA}/statements` },
  { name: "nvda-ratios", hash: `#/company/${NVDA}/ratios` },
  { name: "nvda-filings", hash: `#/company/${NVDA}/filings` },
  { name: "nvda-peers", hash: `#/company/${NVDA}/peers` },
  { name: "compare", hash: `#/compare?target=${PEP}&peers=${KO},${NVDA}` },
  { name: "chart", hash: "#/chart", then: async (p) => {
    await p.getByRole("button", { name: /^\+ ?Metric|Metric/ }).first().click().catch(() => undefined);
  } },
  { name: "analysis", hash: "#/analysis" },
  { name: "ask", hash: "#/ask" },
  { name: "alerts", hash: "#/alerts" },
  { name: "settings", hash: "#/settings" },
  { name: "status", hash: "#/status" },
  { name: "about", hash: "#/about" },
  { name: "search", hash: "#/search?q=tariffs" },
];

for (const s of SCREENS) {
  if (ONLY.length && !ONLY.includes(s.name)) continue;
  test(`shot ${s.name}`, async ({ page }) => {
    const fake = await mockApi(page);
    await seed(fake);
    await richer(page);
    await startDemo(page);
    for (const w of WIDTHS) {
      const [width, theme] = w.split("-") as [string, "light" | "dark"];
      await page.setViewportSize({ width: Number(width), height: Number(width) < 600 ? 844 : 900 });
      await page.emulateMedia({ colorScheme: theme });
      await page.goto(`/${s.hash}`);
      await page.waitForLoadState("networkidle");
      if (s.then) await s.then(page);
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${OUT}/${s.name}-${w}.png`, fullPage: true });
    }
  });
}

// for a quick look at one hash: HASH=... NAME=...
if (process.env.HASH) {
  test(`shot ${process.env.NAME ?? "adhoc"}`, async ({ page }) => {
    const fake = await mockApi(page);
    await seed(fake);
    await richer(page);
    await startDemo(page);
    for (const w of WIDTHS) {
      const [width, theme] = w.split("-") as [string, "light" | "dark"];
      await page.setViewportSize({ width: Number(width), height: Number(width) < 600 ? 844 : 900 });
      await page.emulateMedia({ colorScheme: theme });
      await page.goto(`/${process.env.HASH}`);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${OUT}/${process.env.NAME ?? "adhoc"}-${w}.png`, fullPage: true });
    }
  });
}
void readFileSync;
