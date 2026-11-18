import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { entry, NVDA } from "../fixtures";
import type { Holding, JournalEntry, Portfolio } from "../types";
import { HoldingLine } from "./HoldingLine";
import { PortfolioPage } from "./PortfolioPage";

const HID = "3f2b8c1e-7a4d-4e2b-9c1a-0d5e6f7a8b9c";
const VOO = "9a1b2c3d-0000-4000-8000-000000000001";

const NV: Holding = {
  id: HID, company_id: NVDA, covered: true, symbol: "NVDA", name: null, added_at: "2026-11-16T15:00:00Z", open: true,
  shares: "1.75", cost_basis: "285.00", average_cost: "162.857142", realised: "0.00", first_bought: null,
  price: { value: "186.26", on: "2026-11-16", source: "fidelity", provider: null, stale: false },
  value: "325.96", unrealised: "40.96", unrealised_pct: "0.1437", weight: "0.9194",
  next_report: { form: "10-Q", period_end: "2027-01-25", expected_date: "2027-02-25" },
  link: null, candidates: [{ kind: "project", id: "p1", name: "NVDA deep dive" }],
  review_due: { accession_no: "0001045810-26-000101", form: "10-Q", filed: "2026-11-19", period_end: "2026-10-26", cik: NVDA },
  lots: [{ id: "l1", shares: "1.25", cost_total: "200.00", cost_per_share: "160", bought_on: null, account_label: "Individual", source: "fidelity", created_at: "2026-11-16T15:00:00Z" }],
  sells: [],
};
const FUND: Holding = {
  ...NV, id: VOO, company_id: null, covered: false, symbol: "VOO", name: "Vanguard S&P 500 ETF", shares: "0.05", cost_basis: "25.00",
  average_cost: "500", price: null, value: null, unrealised: null, unrealised_pct: null, weight: "0.0806", next_report: null,
  candidates: [], review_due: null, lots: [], link: null,
};
const PORTFOLIO = (holdings: Holding[]): Portfolio => ({
  holdings, currency: "USD", method: "average_cost", weight_basis: "cost", stale_after_days: 7, today: "2026-11-16",
  totals: { open: holdings.length, priced: holdings.filter((h) => h.price).length, cost_basis: "310.00", value: holdings.some((h) => h.price) ? "325.96" : null,
    unrealised: holdings.some((h) => h.price) ? "40.96" : null, realised: "0.00" },
  reviews_due: holdings.filter((h) => h.review_due).length, prices_connected: false, refresh: false,
});
const KEPT: JournalEntry = {
  id: "e1", company_id: NVDA, symbol: null, kind: "bought", decided_on: "2026-11-02", why: "Data-centre demand outruns supply.",
  sell_rules: "Gross margin under 60% two quarters running", confidence: 4, corrects: null,
  red_lines: [{ rule_id: "r1", label: "Margin", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.6", display: "percent" }],
  created_at: "2026-11-02T15:00:00Z", edited_at: null, editable_until: "2026-11-03T15:00:00Z", kept: true,
};
const FRESH: JournalEntry = { ...KEPT, id: "e2", kind: "note", why: "Fresh thought", sell_rules: null, red_lines: [], confidence: null,
  created_at: "2026-11-16T15:00:00Z", editable_until: "2026-11-17T15:00:00Z", kept: false };

const CSV = "Account Number,Account Name,Symbol,Description,Quantity,Last Price,Cost Basis Total\r\nZ09876543,Individual,NVDA,NVIDIA,1.25,$186.26,$200.00\r\n";

let holdings: Holding[] = [];
let entries: JournalEntry[] = [];
let writes: { method: string; path: string; body: unknown; type?: string }[] = [];

function respond(url: string, init?: RequestInit): Response {
  const u = new URL(url, "http://x");
  const path = u.pathname.replace(/^\/api\/query/, "");
  const method = init?.method ?? "GET";
  const type = (init?.headers as Record<string, string> | undefined)?.["Content-Type"];
  const body = typeof init?.body === "string" && type === "application/json" ? JSON.parse(init.body) : init?.body;
  if (method !== "GET") writes.push({ method, path, body, type });
  if (path === "/v1/screener") {
    const ids = u.searchParams.getAll("company_id");
    return Response.json({ total: ids.length, columns: [], rows: ids.map((id) => ({ ...entry(), company_id: id, filer_categories: null, state_of_incorporation: null, sources: {}, values: {} })) });
  }
  if (path === "/v1/portfolio") return Response.json(PORTFOLIO(holdings));
  if (path === `/v1/portfolio/position/${NVDA}`) return Response.json({ position: { holding_id: HID, shares: "1.75", open: true, first_bought: "2026-11-02", added_at: "2026-11-16T15:00:00Z" } });
  if (path === "/v1/portfolio/journal" && method === "GET") return Response.json({ entries, has_more: false, edit_hours: 24, kinds: [] });
  if (path === "/v1/portfolio/journal") return Response.json({ entry: { ...FRESH, id: "e3" } });
  if (path === "/v1/alerts/red-lines") return Response.json({ red_lines: noRedLines ? [] : [{ ...KEPT.red_lines[0], company_id: NVDA, created_at: "" }], max_per_company: 10 });
  if (path === "/v1/portfolio/holdings") return Response.json({ holding_id: VOO, created: true, covered: false, company_id: null });
  if (path === "/v1/portfolio/import/preview") {
    return Response.json({
      positions: [
        { row: 0, symbol: "NVDA", description: "NVIDIA CORPORATION COM", shares: "1.25", cost_total: "200.00", last_price: "186.26", account_label: "Individual", company_id: NVDA, name: "Nvidia", covered: true, held: null },
        { row: 1, symbol: "MSFT", description: "MICROSOFT CORP", shares: "0.2", cost_total: null, last_price: "505", account_label: "ROTH IRA", company_id: "0000789019", name: "Microsoft", covered: true, held: null },
        { row: 2, symbol: "VOO", description: "VANGUARD S&P 500 ETF", shares: "0.05", cost_total: "25.00", last_price: "560", account_label: null, company_id: null, name: null, covered: false, held: null },
      ],
      skipped: [{ symbol: "SPAXX", description: "HELD IN MONEY MARKET", reason: "cash" }, { symbol: null, description: "x", reason: "option" }],
      downloaded: "2026-11-16", source: "fidelity",
    });
  }
  if (path === "/v1/portfolio/import") return Response.json({ holdings_added: 3, lots_added: 3, prices_saved: 2 });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;
let noRedLines = false;

async function settle() {
  for (let i = 0; i < 10; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function show(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

const button = (name: string) => [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name);
function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  noRedLines = false;
  holdings = [];
  entries = [];
  writes = [];
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

it("an empty portfolio says so in a line, offers an import, and folds how it works (Riley, test 10, 97 and 106)", async () => {
  await show(<PortfolioPage token="t" />);
  expect(container.querySelector(".portfolio-empty .empty-line")?.textContent).toContain("No holdings yet. Track what you own against the research behind it");
  expect(container.querySelector(".portfolio-empty details.how-to-read")?.textContent).toContain("Not investment advice");
  expect(container.querySelector<HTMLAnchorElement>('a[href="#/portfolio/import"]')).not.toBeNull();
  // one heading and one Add a holding: the page bar's
  expect([...container.querySelectorAll("h1, h2")].map((h) => h.textContent)).toEqual(["Portfolio"]);
  expect([...container.querySelectorAll("button")].filter((b) => b.textContent === "Add a holding")).toHaveLength(1);
  await act(async () => button("Add a holding")!.click());
  expect(container.querySelector("form.pf-form")).not.toBeNull();
});

it("lists holdings with their own price's source and day, the gain, weight, next report and a review due", async () => {
  holdings = [NV, FUND];
  await show(<PortfolioPage token="t" />);
  const story = container.querySelector(".portfolio-story")!.textContent!;
  expect(story).toContain("1 company");
  expect(story).toContain("1 fund");
  expect(story).toContain("$310.00");
  expect(story).toContain("up $40.96");
  const rows = [...container.querySelectorAll(".portfolio-table tbody tr")];
  expect(rows).toHaveLength(2);
  const cells = [...rows[0]!.querySelectorAll("td")].map((td) => td.textContent);
  expect(cells[1]).toBe("1.75");
  expect(cells[2]).toBe("$285.00");
  expect(cells[3]).toBe("$186.26Nov 16");
  expect(rows[0]!.querySelectorAll("td")[3]!.getAttribute("title")).toBe("Your Fidelity export of Nov 16, 2026");
  expect(cells[5]).toBe("$40.96");
  expect(cells[6]).toBe("91.9%");
  expect(rows[0]!.querySelector(".holding-review")?.textContent).toBe("Review due");
  // a fund: symbol and cost, no price or value
  const fund = [...rows[1]!.querySelectorAll("td")].map((td) => td.textContent);
  expect(fund[0]).toContain("VOO, not covered");
  expect(fund[3]).toBe("—");
  expect(fund[4]).toBe("—");
  expect(container.querySelector(".portfolio-table thead")!.textContent).toContain("Weight (cost)");
  expect(container.querySelector(".review-due-note")?.textContent).toContain("One holding has a new report since your last review");
});

it("a holding's journal: a kept entry is corrected beside itself, a fresh one can still be edited", async () => {
  holdings = [NV];
  entries = [FRESH, KEPT];
  await show(<PortfolioPage token="t" holdingId={HID} />);
  expect(container.querySelector(".review-due-note")?.textContent).toContain("Review due: the 10-Q filed Nov 19, 2026");
  const items = [...container.querySelectorAll(".journal-item")];
  expect(items).toHaveLength(2);
  const kept = items[1]!;
  expect(kept.querySelector(".entry-why")?.textContent).toBe("Data-centre demand outruns supply.");
  expect(kept.querySelector(".entry-redlines")?.textContent).toBe("Red line: Margin, Gross margin below 60.0%");
  expect(kept.textContent).toContain("kept as written");
  expect([...kept.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Correct"]);
  expect([...items[0]!.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Edit", "Delete"]);

  await act(async () => [...kept.querySelectorAll("button")][0]!.click());
  await settle();
  const form = container.querySelector<HTMLFormElement>("form.entry-form")!;
  expect(form.querySelector("h3")?.textContent).toBe("Correct the entry of Nov 2, 2026");
  type(form.querySelector("textarea")!, "Supply caught up; demand still outruns it.");
  await act(async () => form.requestSubmit());
  await settle();
  const post = writes.find((w) => w.path === "/v1/portfolio/journal")!;
  expect(post.body).toMatchObject({ company_id: NVDA, kind: "bought", corrects: "e1", why: "Supply caught up; demand still outruns it.", red_line_ids: ["r1"] });
});

it("the review button writes a review entry", async () => {
  holdings = [NV];
  await show(<PortfolioPage token="t" holdingId={HID} />);
  await act(async () => button("Write the review")!.click());
  await settle();
  const form = container.querySelector<HTMLFormElement>("form.entry-form")!;
  expect(form.querySelector<HTMLSelectElement>("select")!.value).toBe("review");
  type(form.querySelector("textarea")!, "Thesis intact: data-centre revenue up again.");
  await act(async () => form.requestSubmit());
  await settle();
  expect(writes.find((w) => w.path === "/v1/portfolio/journal")!.body).toMatchObject({ kind: "review", company_id: NVDA });
});

it("with no red lines, the journal points to the analysis's Monitor step, where they're set (Riley, test 10, finding 12)", async () => {
  holdings = [NV];
  noRedLines = true;
  await show(<PortfolioPage token="t" holdingId={HID} />);
  await act(async () => button("Write the review")!.click());
  await settle();
  const lines = container.querySelector(".entry-lines")!;
  expect(lines.textContent).toContain("they're set in the Monitor step of its analysis");
  expect(lines.textContent).not.toContain("Settings");
  // the holding's analysis (a candidate here) opens at its Monitor step
  expect(lines.querySelector("a")?.getAttribute("href")).toBe("#/analysis/p1/monitor");
});

it("imports a Fidelity export: the preview first, a missing cost typed, nothing saved until Add", async () => {
  await show(<PortfolioPage token="t" importing />);
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file = new File([CSV], "Portfolio_Positions_Nov-16-2026.csv", { type: "text/csv" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await settle();
  const preview = writes.find((w) => w.path === "/v1/portfolio/import/preview")!;
  expect(preview.type).toBe("text/csv");
  expect(writes.some((w) => w.path === "/v1/portfolio/import")).toBe(false);
  const rows = [...container.querySelectorAll(".import-table tbody tr")];
  expect(rows).toHaveLength(3);
  expect(rows[2]!.textContent).toContain("Not covered: VANGUARD S&P 500 ETF");
  expect(container.textContent).toContain("Left out: SPAXX (cash or money market); x (an option).");
  const add = () => [...container.querySelectorAll<HTMLButtonElement>(".import-actions button")].pop()!;
  expect(add().disabled).toBe(true); // MSFT's cost is missing
  type(container.querySelector<HTMLInputElement>('input[aria-label="Cost of MSFT"]')!, "98.40");
  await settle();
  expect(add().textContent).toBe("Add 3 positions");
  await act(async () => add().click());
  await settle();
  const saved = writes.find((w) => w.path === "/v1/portfolio/import")!;
  expect(saved.body).toEqual({
    price_on: "2026-11-16",
    positions: [
      { symbol: "NVDA", description: "NVIDIA CORPORATION COM", shares: "1.25", cost_total: "200.00", last_price: "186.26", account_label: "Individual" },
      { symbol: "MSFT", description: "MICROSOFT CORP", shares: "0.2", cost_total: "98.40", last_price: "505", account_label: "ROTH IRA" },
      { symbol: "VOO", description: "VANGUARD S&P 500 ETF", shares: "0.05", cost_total: "25.00", last_price: "560", account_label: null },
    ],
  });
  expect(JSON.stringify(writes.filter((w) => w.path !== "/v1/portfolio/import/preview"))).not.toContain("Z09876543");
  expect(window.location.hash).toBe("#/portfolio");
});

it("adds a fund by its symbol, and writes why as the journal's first entry", async () => {
  await show(<PortfolioPage token="t" />);
  await act(async () => button("Add a holding")!.click());
  const form = container.querySelector<HTMLFormElement>("form.pf-form")!;
  await act(async () => [...form.querySelectorAll<HTMLButtonElement>('[role="radio"]')].find((b) => b.textContent === "A fund or ETF")!.click());
  type(form.querySelector<HTMLInputElement>('input[placeholder="VOO"]')!, "voo");
  type(form.querySelector<HTMLInputElement>('input[placeholder="e.g. 10"]')!, "0.05");
  type(form.querySelector<HTMLInputElement>('input[placeholder="e.g. 1,250.00"]')!, "25");
  type(form.querySelector<HTMLTextAreaElement>("textarea")!, "Cheap index exposure while I learn.");
  await act(async () => form.requestSubmit());
  await settle();
  expect(writes[0]).toMatchObject({ path: "/v1/portfolio/holdings", body: { symbol: "VOO", lot: { shares: "0.05", cost_total: "25" } } });
  expect(writes[1]).toMatchObject({ path: "/v1/portfolio/journal", body: { symbol: "VOO", kind: "bought", why: "Cheap index exposure while I learn." } });
});

it("a problem shown clears once the form is edited (Riley, test 10, finding 16)", async () => {
  await show(<PortfolioPage token="t" />);
  await act(async () => button("Add a holding")!.click());
  const form = container.querySelector<HTMLFormElement>("form.pf-form")!;
  await act(async () => [...form.querySelectorAll<HTMLButtonElement>('[role="radio"]')].find((b) => b.textContent === "A fund or ETF")!.click());
  await act(async () => form.requestSubmit());
  await settle();
  expect(form.querySelector(".err")?.textContent).toBe("Type the fund's symbol, such as VOO.");
  await act(async () => type(form.querySelector<HTMLInputElement>('input[placeholder="VOO"]')!, "voo"));
  expect(form.querySelector(".err")).toBeNull();
});

it("your price's example reads as an example, not a market price (Riley, test 10, finding 22)", async () => {
  holdings = [NV];
  await show(<PortfolioPage token="t" holdingId={HID} />);
  const price = container.querySelector<HTMLInputElement>('form[aria-label="Your price"] input')!;
  expect(price.placeholder).toBe("e.g. 100.00");
});

it("an account label is a name: nothing in the form asks for an account number", async () => {
  await show(<PortfolioPage token="t" />);
  await act(async () => button("Add a holding")!.click());
  const label = container.querySelector<HTMLInputElement>('input[placeholder="Brokerage, Roth IRA"]')!;
  expect(label.title).toBe("A name for the account, never its number");
  expect(label.maxLength).toBe(60);
});

it("the company page says quietly that you hold it", async () => {
  await show(<HoldingLine token="t" companyId={NVDA} />);
  expect(container.querySelector(".co-held")?.textContent).toBe("You hold 1.75 shares, since Nov 2, 2026. Your holding");
  expect(container.querySelector<HTMLAnchorElement>(".co-held a")!.getAttribute("href")).toBe(`#/portfolio/${HID}`);
});
