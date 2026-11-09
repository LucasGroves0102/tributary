import { describe, expect, it } from "vitest";

import { NVDA } from "../fixtures";
import { parse } from "../route";
import type { AlertFigure, AlertNotification, RedLineResult, WebhookDelivery } from "../types";
import {
  alertLinks,
  bellLabel,
  deliveryText,
  figureChange,
  figuresNote,
  figureValue,
  headline,
  inDisplay,
  redLineState,
  ruleText,
  stallNote,
  thresholdFromInput,
} from "./alerts";

const ACC = "0001045810-26-000101";
const fig = (over: Partial<AlertFigure>): AlertFigure => ({
  metric: "revenue", label: "Revenue", span: "quarter", period_end: "2026-10-26", value: "57006000000", unit: "USD",
  not_meaningful: null, year_ago: "35082000000", change: "21924000000", pct_change: "0.6249", ...over,
});
const note = (over: Partial<AlertNotification>): AlertNotification => ({
  id: "n1", company_id: NVDA, accession_no: ACC, form: "10-Q", filed: "2026-11-19", period_end: "2026-10-26", items: [],
  items_known: true, sources: ["watchlist"], status: "ready", figures: [], figures_status: "ready", red_lines: [], crossed: 0,
  created_at: "2026-11-19T21:10:00Z", ready_at: null, read_at: null, ...over,
});

it("words figures as the statements do", () => {
  expect(figureValue(fig({}))).toBe("$57.01B");
  expect(figureChange(fig({}))).toBe("+62.5% on a year earlier");
  const gm = fig({ metric: "gross_margin", label: "Gross margin", value: "0.735", unit: "ratio", change: "-0.010", pct_change: null });
  expect(figureValue(gm)).toBe("73.5%");
  expect(figureChange(gm)).toBe("-1.0 pts on a year earlier");
  expect(figureValue(fig({ value: "1.3", unit: "USD/shares" }))).toBe("$1.30");
  expect(figureChange(fig({ change: null, pct_change: null }))).toBeNull();
});

it("reads red lines in their formula's display, and says crossed or clear", () => {
  const r: RedLineResult = { rule_id: "r", label: "GM", formula: "gross_margin", view: "ttm", op: "<", threshold: "0.55", display: "percent",
    value: "0.735", period_end: "2026-10-26", crossed: false, status: "ok" };
  expect(ruleText(r)).toBe("gross_margin below 55.0%");
  expect(redLineState(r)).toBe("clear");
  expect(redLineState({ ...r, crossed: true })).toBe("crossed");
  expect(redLineState({ ...r, status: "no_value", crossed: null })).toBe("no_value");
  expect(inDisplay("1.2", "multiple")).toBe("1.20x");
  expect(inDisplay(null, "percent")).toBe("—");
  // 057: an amount red line on a yuan filer says yuan; dollars (or an older API) as before
  expect(inDisplay("996347000000", null, "CNY")).toBe("CN¥996.35B");
  expect(inDisplay("996347000000", null)).toBe("$996.35B");
  expect(ruleText({ formula: "revenue", op: "<", threshold: "1000000000000", display: null, unit: "CNY" }))
    .toBe("revenue below CN¥1.00T");
});

it("turns a typed threshold into what the API stores", () => {
  expect(thresholdFromInput("55", "percent")).toBe("0.55");
  expect(thresholdFromInput("55%", "percent")).toBe("0.55");
  expect(thresholdFromInput("10.5", "percent")).toBe("0.105");
  expect(thresholdFromInput("1.5x", "multiple")).toBe("1.5");
  expect(thresholdFromInput("-2", null)).toBe("-2");
  expect(thresholdFromInput("lots", "percent")).toBeNull();
  expect(thresholdFromInput("", "percent")).toBeNull();
});

it("links What changed, Earnings, the reader and EDGAR", () => {
  const links = alertLinks({ ...note({}), filing_url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/" });
  expect(links.map((l) => l.label)).toEqual(["What changed", "Earnings", "Read the filing", "EDGAR ↗"]);
  expect(parse(links[0]!.href)).toEqual({ view: "company", companyId: NVDA, tab: "filings", changes: ACC });
  expect(parse(links[1]!.href)).toEqual({ view: "company", companyId: NVDA, tab: "earnings" });
  expect(links[3]!.href).toBe("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/");
  // an 8-K has no What changed; a link that isn't sec.gov's isn't used
  const eightK = alertLinks({ ...note({ form: "8-K" }), filing_url: "javascript:alert(1)" });
  expect(eightK.map((l) => l.label)).toEqual(["Earnings and events", "Read the filing", "EDGAR ↗"]);
  expect(eightK[2]!.href).toBe("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/");
});

it("says what an alert is and why its figures are missing", () => {
  expect(headline(note({}))).toBe("10-Q filed Nov 19, 2026 for the quarter ended Oct 26, 2026");
  expect(headline(note({ form: "10-K" }))).toBe("10-K filed Nov 19, 2026 for the year ended Oct 26, 2026");
  expect(headline(note({ form: "8-K", period_end: "2026-11-18" }))).toBe("8-K filed Nov 19, 2026");
  expect(figuresNote(note({ figures_status: "pending", figures: null, status: "pending" }), 3)).toContain("usually within a day");
  expect(figuresNote(note({ figures_status: "no_access", figures: null }), 3)).toContain("Your plan doesn't include");
  expect(figuresNote(note({ form: "8-K", items_known: false, status: "pending" }), 3)).toContain("Items not read yet");
});

it("words deliveries and the bell", () => {
  const d = (over: Partial<WebhookDelivery>): WebhookDelivery => ({
    id: "d", webhook_id: "w", notification_id: "n", test: false, status: "delivered", attempts: 1, next_attempt_at: null,
    last_attempt_at: null, last_status: 204, last_error: null, created_at: "", delivered_at: null, ...over,
  });
  expect(deliveryText(d({}))).toBe("Delivered (204)");
  expect(deliveryText(d({ status: "pending", attempts: 2, last_status: 500, last_error: "http_500" }))).toBe("Retrying: it answered 500; tried 2 times");
  expect(deliveryText(d({ status: "failed", attempts: 5, last_status: null, last_error: "timeout" }))).toBe("Failed: no answer in time; tried 5 times");
  expect(deliveryText(d({ status: "failed", last_status: 302, last_error: "redirect" }))).toBe("Failed: it answered with a redirect, which isn't followed");
  expect(bellLabel(3)).toBe("Alerts: 3 new filings");
  expect(bellLabel(0)).toBe("Alerts: nothing new");
});

describe("stalled webhooks", () => {
  const now = new Date("2026-11-20T15:00:00Z");
  it("say so only while the job leaves them out", () => {
    expect(stallNote({ stalls: 0, stalled_until: null }, now)).toBeNull();
    expect(stallNote({}, now)).toBeNull(); // an older API
    expect(stallNote({ stalls: 1, stalled_until: "2026-11-20T15:00:00Z" }, now)).toBeNull(); // only this run
    expect(stallNote({ stalls: 3, stalled_until: "2026-11-20T17:00:00Z" }, now)).toMatch(/^Not reachable on its last 3 tries: alerts wait until .+, then it's tried again$/);
  });
  it("a slow lookup has its own words", () => {
    expect(deliveryText({ id: "d", webhook_id: "w", notification_id: "n", test: false, status: "pending", attempts: 1, next_attempt_at: null,
      last_attempt_at: null, last_status: null, last_error: "dns_timeout", created_at: "", delivered_at: null }))
      .toBe("Retrying: its host name took too long to resolve");
  });
});
