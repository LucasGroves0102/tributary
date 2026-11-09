import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { clearChartCache } from "../chart/cache";
import { entry, NVDA } from "../fixtures";
import { forgetVisits } from "../home/lastVisit";
import { loadScreens } from "../pages/lazy";
import type { AlertNotification, RedLine } from "../types";
import { RedLines } from "./RedLines";
import { clearAlerts } from "./useAlerts";

const ME = { user_id: "alice", companies: [{ company_id: NVDA, name: "Nvidia", industry: null, tiers: ["financials", "filings"] }] };
const Q3 = "0001045810-26-000101";
const REL = "0001045810-26-000099";
const HOOK = "3f2b8c1e-7a4d-4e2b-9c1a-0d5e6f7a8b9c";
const SECRET = "derived-signing-secret-for-tests-only";

const TENQ: AlertNotification = {
  id: "aaaaaaaa-0000-4000-8000-000000000001", company_id: NVDA, accession_no: Q3, form: "10-Q", filed: "2026-11-19",
  period_end: "2026-10-26", items: [], items_known: true, sources: ["watchlist", "analysis"], status: "ready",
  filing_url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/",
  figures: [
    { metric: "revenue", label: "Revenue", span: "quarter", period_end: "2026-10-26", value: "57006000000", unit: "USD", not_meaningful: null, year_ago: "35082000000", change: "21924000000", pct_change: "0.6249" },
    { metric: "gross_margin", label: "Gross margin", span: "quarter", period_end: "2026-10-26", value: "0.735", unit: "ratio", not_meaningful: null, year_ago: "0.745", change: "-0.010", pct_change: null },
  ],
  figures_status: "ready",
  red_lines: [
    { rule_id: "r1", label: "Gross margin below 75%", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.75", display: "percent", value: "0.735", period_end: "2026-10-26", crossed: true, status: "ok" },
    { rule_id: "r2", label: "Gross margin below 70%", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.70", display: "percent", value: "0.735", period_end: "2026-10-26", crossed: false, status: "ok" },
  ],
  crossed: 1, created_at: "2026-11-19T21:20:00Z", ready_at: "2026-11-19T21:20:00Z", read_at: null,
};
const EIGHTK: AlertNotification = {
  ...TENQ, id: "aaaaaaaa-0000-4000-8000-000000000002", accession_no: REL, form: "8-K", period_end: "2026-11-19", sources: ["watchlist"],
  items: [{ item: "2.02", title: "Results of Operations and Financial Condition", kind: "earnings" }, { item: "9.01", title: "Financial Statements and Exhibits", kind: "exhibits" }],
  figures: null, figures_status: null, red_lines: [], crossed: 0,
};

let alertsOn = true;
let unread = 2;
let writes: { method: string; path: string; body: unknown }[] = [];
let hooks: object[] = [];

function respond(url: string, init?: RequestInit): Response {
  const u = new URL(url, "http://x");
  const path = u.pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
  if (method !== "GET") writes.push({ method, path, body });
  if (path === "/v1/demo/users") return Response.json({ users: ["alice"] });
  if (path === "/v1/demo/token") return Response.json({ token: "t" });
  if (path === "/v1/me") return Response.json(ME);
  if (path === "/v1/watchlist") return Response.json({ as_of: "", columns: [], expected_method: "", companies: [entry()] });
  if (path === "/v1/screener") {
    const ids = u.searchParams.getAll("company_id");
    return Response.json({ total: ids.length, columns: [], rows: ids.map((id) => ({ ...entry(), company_id: id, filer_categories: null, state_of_incorporation: null, sources: {}, values: {} })) });
  }
  if (path.startsWith("/v1/alerts") && !alertsOn) return Response.json({ detail: "Not Found" }, { status: 404 });
  if (path === "/v1/alerts/unread") return Response.json({ unread, latest: TENQ.created_at, last_check: { checked_at: "2026-11-19T21:30:00Z", filings_through: "2026-11-19" } });
  if (path === "/v1/alerts/notifications") return Response.json({ notifications: [TENQ, EIGHTK], has_more: false, lookback_days: 3, pending_days: 3 });
  if (path === "/v1/alerts/notifications/read") {
    unread = 0;
    return Response.json({ marked: body.ids?.length ?? 2 });
  }
  if (path === "/v1/alerts/settings") {
    return Response.json({ forms: ["10-K", "10-Q", "8-K"], companies: [{ company_id: NVDA, sources: ["watchlist"], since: "2026-11-10", forms: ["10-K", "10-Q", "8-K"] }],
      webhooks: hooks, formats: ["json", "discord", "slack"], max_webhooks: 3, email: { available: false },
      last_check: { checked_at: "2026-11-19T21:30:00Z", filings_through: "2026-11-19" }, schedule: "every 10 minutes", lookback_days: 3, pending_days: 3 });
  }
  if (path === `/v1/alerts/settings/${NVDA}`) return Response.json({ company_id: NVDA, forms: body.forms });
  if (path === "/v1/alerts/deliveries") return Response.json({ deliveries: [], retries: [300, 1800, 7200, 21600] });
  if (path === "/v1/alerts/webhooks" && method === "POST") {
    // the Query API's own refusals (query_api/webhooks.py)
    if (body.url.startsWith("http://")) return Response.json({ detail: "only https:// URLs can receive alerts", code: "scheme" }, { status: 422 });
    const hook = { id: HOOK, url: "https://discord.com/…wxyz", format: "discord", enabled: true, created_at: "2026-11-19T22:00:00Z", rotated_at: null, last_delivery: null };
    hooks = [hook];
    return Response.json({ webhook: hook, secret: SECRET, secret_shown_once: true });
  }
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

async function settle() {
  for (let i = 0; i < 8; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  await act(async () => { await vi.dynamicImportSettled(); });
  for (let i = 0; i < 4; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(hash: string, ready: string) {
  window.location.hash = hash;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<App picker />));
  for (let i = 0; i < 30 && !container.querySelector(ready); i++) await settle();
}

const button = (name: string) => [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name)!;
function type(el: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeAll(() => loadScreens());

beforeEach(() => {
  clearChartCache();
  clearAlerts();
  alertsOn = true;
  unread = 2;
  writes = [];
  hooks = [];
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  forgetVisits();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("the bell counts new filing alerts; the list says what was filed, its figures and red lines, and marks them read", async () => {
  await render("#/", ".top .bell");
  const bell = container.querySelector<HTMLAnchorElement>(".top .bell")!;
  expect(bell.getAttribute("aria-label")).toBe("Alerts: 2 new filings");
  expect(bell.querySelector(".count")?.textContent).toBe("2");
  expect(bell.getAttribute("href")).toBe("#/alerts");
  // Home's start card says so too
  expect(container.querySelector(".start-card .start-alerts")?.textContent).toBe("2 new filing alerts");

  window.location.hash = "#/alerts";
  await settle();
  const items = [...container.querySelectorAll(".alert-item")];
  expect(items).toHaveLength(2);
  const q = items[0]!;
  expect(q.querySelector(".alert-what")?.textContent).toBe("10-Q filed Nov 19, 2026 for the quarter ended Oct 26, 2026");
  expect(q.querySelector(".alert-figures")?.textContent).toContain("Revenue$57.01B +62.5% on a year earlier");
  expect(q.querySelector(".alert-figures")?.textContent).toContain("Gross margin73.5% -1.0 pts on a year earlier");
  expect(q.querySelector(".tag.crossed")?.textContent).toBe("1 red line crossed");
  expect([...q.querySelectorAll(".alert-redlines li")].map((li) => li.textContent)).toEqual([
    "Crossed Gross margin below 75% (gross_margin below 75.0%; 73.5%)",
    "Clear Gross margin below 70% (gross_margin below 70.0%; 73.5%)",
  ]);
  const links = [...q.querySelectorAll<HTMLAnchorElement>(".alert-links a")].map((a) => [a.textContent, a.getAttribute("href")]);
  expect(links).toEqual([
    ["What changed", `#/company/${NVDA}/filings?changes=${Q3}`],
    ["Earnings", `#/company/${NVDA}/earnings`],
    ["Read the filing", `#/filing/${NVDA}/${Q3}`],
    ["EDGAR ↗", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/"],
  ]);
  expect(items[1]!.querySelector(".alert-items")?.textContent).toContain("2.02 Results of Operations and Financial Condition");
  // opening the list marked both read, and the bell asks again
  expect(writes).toContainEqual({ method: "POST", path: "/v1/alerts/notifications/read", body: { ids: [TENQ.id, EIGHTK.id] } });
  await settle();
  expect(container.querySelector(".top .bell")?.getAttribute("aria-label")).toBe("Alerts: nothing new");
});

it("an API without alerts (the deploy window): no bell, and the page says so", async () => {
  alertsOn = false;
  await render("#/alerts", ".alerts-page .empty");
  expect(container.querySelector(".top .bell")).toBeNull();
  expect(container.querySelector(".alerts-page")?.textContent).toContain("Filing alerts aren't available on this server yet.");
});

it("Settings: forms per company, and a webhook refused for plain http, then added with its secret shown once", async () => {
  await render("#/settings", "table.alert-forms");
  const box = container.querySelector<HTMLInputElement>('input[aria-label="8-K alerts for Nvidia (NVDA)"]')!;
  await act(async () => box.click());
  await settle();
  expect(writes).toContainEqual({ method: "PUT", path: `/v1/alerts/settings/${NVDA}`, body: { forms: ["10-K", "10-Q"] } });
  expect(container.textContent).toContain("Email alerts aren't set up on this server");

  const url = container.querySelector<HTMLInputElement>("#webhook-url")!;
  await act(async () => type(url, "http://169.254.169.254"));
  await act(async () => container.querySelector<HTMLFormElement>(".webhook-form")!.requestSubmit());
  await settle();
  expect(container.querySelector("#webhook-error")?.textContent).toBe("Refused: only https:// URLs can receive alerts.");
  expect(url.getAttribute("aria-invalid")).toBe("true");

  const real = "https://discord.com/api/webhooks/1234/" + "w".repeat(40) + "wxyz";
  await act(async () => type(container.querySelector<HTMLInputElement>("#webhook-url")!, real));
  await act(async () => container.querySelector<HTMLFormElement>(".webhook-form")!.requestSubmit());
  await settle();
  expect(container.querySelector(".secret-once code.secret")?.textContent).toBe(SECRET);
  expect(container.querySelector<HTMLInputElement>("#webhook-url")?.value).toBe(""); // the URL carries a token: not kept
  expect(container.querySelector(".webhook-list")?.textContent).toContain("https://discord.com/…wxyz");
  expect(container.innerHTML).not.toContain("w".repeat(40));
  expect(JSON.stringify({ ...localStorage })).not.toContain(SECRET);
  await act(async () => button("I've saved it").click());
  expect(container.textContent).not.toContain(SECRET);
});

it("Settings: 8-Ks under 7.01 / 8.01 alone are a per-company choice when the API offers it; a stalled webhook says so", async () => {
  hooks = [{ id: HOOK, url: "https://hooks.example.com/…abcd", format: "json", enabled: true, created_at: "2026-11-19T22:00:00Z", rotated_at: null,
    last_delivery: null, stalls: 2, stalled_until: "2999-01-01T12:30:00Z" }];
  vi.mocked(fetch).mockImplementation(async (url: string | URL | Request, init?: RequestInit) => {
    const r = respond(String(url), init);
    if (!String(url).endsWith("/v1/alerts/settings")) return r;
    const data = await r.json();
    return Response.json({ ...data, other_8k_items: ["7.01", "8.01"], companies: data.companies.map((c: object) => ({ ...c, other_8ks: false })) });
  });
  await render("#/settings", "table.alert-forms");
  expect(container.querySelector("table.alert-forms thead")?.textContent).toContain("7.01 / 8.01");
  expect(container.textContent).toContain("alerts only for the companies you tick under 7.01 / 8.01");
  const other = container.querySelector<HTMLInputElement>('input[aria-label="8-Ks under 7.01 or 8.01 alone for Nvidia (NVDA)"]')!;
  expect(other.checked).toBe(false);
  await act(async () => other.click());
  await settle();
  expect(writes).toContainEqual({ method: "PUT", path: `/v1/alerts/settings/${NVDA}`, body: { forms: ["10-K", "10-Q", "8-K"], other_8ks: true } });
  expect(other.checked).toBe(true);
  // without 8-Ks at all the choice means nothing: off to the touch, and the saved choice is sent as it is
  await act(async () => container.querySelector<HTMLInputElement>('input[aria-label="8-K alerts for Nvidia (NVDA)"]')!.click());
  await settle();
  expect(writes.at(-1)).toEqual({ method: "PUT", path: `/v1/alerts/settings/${NVDA}`, body: { forms: ["10-K", "10-Q"], other_8ks: true } });
  expect(other.disabled).toBe(true);
  expect(container.querySelector(".webhook-stalled")?.textContent).toContain("Not reachable on its last 2 tries: alerts wait until");
});

it("an older API without the 7.01 / 8.01 choice: no column, and saves send the forms only", async () => {
  await render("#/settings", "table.alert-forms");
  expect(container.querySelector("table.alert-forms thead")?.textContent).not.toContain("7.01");
  expect(container.querySelector('input[aria-label^="8-Ks under 7.01"]')).toBeNull();
  await act(async () => container.querySelector<HTMLInputElement>('input[aria-label="10-K alerts for Nvidia (NVDA)"]')!.click());
  await settle();
  expect(writes.at(-1)).toEqual({ method: "PUT", path: `/v1/alerts/settings/${NVDA}`, body: { forms: ["10-Q", "8-K"] } });
  expect(container.querySelector(".webhook-stalled")).toBeNull();
});

it("a demo sandbox sees why it can't add a webhook", async () => {
  vi.mocked(fetch).mockImplementation(async (url: string | URL | Request, init?: RequestInit) => {
    const r = respond(String(url), init);
    if (!String(url).endsWith("/v1/alerts/settings")) return r;
    return Response.json({ ...(await r.json()), webhooks_allowed: false });
  });
  await render("#/settings", "table.alert-forms");
  expect(container.querySelector("#webhook-url")).toBeNull();
  expect(container.textContent).toContain("Webhooks need a permanent account");
});

// --- the Monitor step's red lines ---------------------------------------------------------------

const RULES: RedLine[] = [
  { rule_id: "r1", company_id: NVDA, label: "Gross margin below 75%", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.75", display: "percent", created_at: "" },
  { rule_id: "r2", company_id: NVDA, label: "Receivables outgrow revenue by 10 pts", formula: "(receivables / receivables[-4q] - 1) - (revenue / revenue[-4q] - 1)", view: "quarter", op: ">", threshold: "0.1", display: "percent", created_at: "" },
];

it("Monitor's red lines: crossed and clear on the latest figures; a typed 55 is saved as 0.55", async () => {
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url, "http://x");
    if (u.pathname.endsWith("/v1/alerts/red-lines") && init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return Response.json({ red_line: RULES[0] });
    }
    if (u.pathname.endsWith("/v1/alerts/red-lines")) {
      return Response.json({ red_lines: RULES, max_per_company: 10, evaluated_at: "2026-11-19T22:00:00Z", evaluation: [
        { ...RULES[0], value: "0.735", period_end: "2026-10-26", crossed: true, status: "ok" },
        { ...RULES[1], value: "0.031", period_end: "2026-10-26", crossed: false, status: "ok" },
      ] });
    }
    if (u.pathname.endsWith("/v1/formulas/validate")) return Response.json({ valid: true, unit: "ratio", display: "percent", alignment: "fiscal", inputs: [] });
    return Response.json({}, { status: 404 });
  }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<RedLines token="t" companyId={NVDA} name="Nvidia" />));
  await settle();
  const rows = [...container.querySelectorAll(".redline-list li")];
  expect(rows.map((r) => r.querySelector(".redline-state")?.textContent)).toEqual(["Crossed", "Clear"]);
  expect(rows[0]!.textContent).toContain("gross_margin below 75.0% · trailing twelve months · 73.5% at Oct 26, 2026");
  expect(rows[1]!.textContent).toContain("3.1% at Oct 26, 2026");
  await act(async () => type(container.querySelector<HTMLInputElement>('input[inputmode="decimal"]')!, "55"));
  await settle();
  await act(async () => container.querySelector<HTMLFormElement>(".redline-form")!.requestSubmit());
  await settle();
  expect(posted).toEqual([{ company_id: NVDA, label: "Gross margin below 55%", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.55" }]);
});
