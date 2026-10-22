import { beforeEach, expect, it } from "vitest";

import { NVDA } from "../fixtures";
import type { FormulaSeries } from "../types";
import { apply, complete, type Vocabulary } from "./autocomplete";
import { cached, clearChartCache, MAX_ENTRIES, remember } from "./cache";
import { autoSubtitle, methodNote, sourceFootnote } from "./deck";
import { layout } from "./layout";
import { autoName, emptyChart, rangeDates, request, restoreChart, type ChartState, type Layer } from "./model";
import { annualPoints, type Point, type Series } from "./series";

const VOCAB: Vocabulary = {
  metrics: [
    { name: "revenue", label: "Revenue", unit: "USD", ttm: true },
    { name: "net_income", label: "Net income", unit: "USD", ttm: true },
    { name: "operating_margin", label: "Operating margin", unit: "ratio", ttm: true },
    { name: "cash", label: "Cash", unit: "USD", ttm: false },
  ],
  tickers: [{ ticker: "NVDA", name: "Nvidia" }, { ticker: "NFLX", name: "Netflix" }, { ticker: "MSFT", name: "Microsoft" }],
};
const labels = (text: string, cursor = text.length) => complete(text, cursor, VOCAB)?.items.map((i) => i.label) ?? null;

it("completes metric names by prefix, then by label words", () => {
  expect(labels("net")).toEqual(["net_income"]);
  expect(labels("inc")).toEqual(["net_income"]); // in the name
  expect(labels("rev")).toEqual(["revenue"]);
  expect(labels("revenue")).toBeNull(); // typed in full: nothing to add
  expect(labels("t")).toEqual(["ttm( )", "net_income", "operating_margin"]); // ttm( ) by prefix first
});

it("completes tickers for upper-case words and metrics after TICKER.", () => {
  expect(labels("N")).toEqual(["NVDA.", "NFLX."]);
  expect(labels("NV")).toEqual(["NVDA."]);
  expect(labels("NVDA.")).toEqual(["revenue", "net_income", "operating_margin", "cash"].sort((a, b) => a.localeCompare(b)));
  expect(labels("MSFT.op")).toEqual(["operating_margin"]);
  expect(labels("ZZZ")).toBeNull();
});

it("offers operators and lags after an operand, functions and metrics at the start", () => {
  expect(labels("revenue ")).toEqual(["+", "−", "×", "÷", "[-4q]", "[-1q]"]);
  expect(labels("ttm(revenue) ")?.[0]).toBe("+");
  expect(labels("")?.slice(0, 3)).toEqual(["ttm( )", "abs( )", "cash"]);
  expect(labels("revenue / ")?.slice(0, 2)).toEqual(["ttm( )", "abs( )"]);
});

it("replaces the word at the cursor, including the rest of it", () => {
  const text = "net_inc / revenue";
  const c = complete(text, 5, VOCAB)!; // cursor inside "net_inc"
  expect(apply(text, c, c.items[0]!)).toEqual({ text: "net_income / revenue", cursor: 10 });
  const ops = complete("revenue ", 8, VOCAB)!;
  expect(apply("revenue ", ops, ops.items[3]!)).toEqual({ text: "revenue / ", cursor: 10 });
});

const TODAY = new Date(Date.UTC(2026, 9, 21));
const metric = (over: Partial<Layer> = {}): Layer => ({ id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "q", type: "bar", axis: "L", ...over });
const hasTtm = (m: string) => m !== "cash";

it("custom ranges ask for their start and end; other ranges count back from today", () => {
  const s: ChartState = { ...emptyChart(), range: "custom", start: "2020-01-01", end: "2024-12-31" };
  expect(rangeDates(s, TODAY)).toEqual({ from: "2020-01-01", to: "2024-12-31" });
  expect(request(metric(), s, hasTtm, TODAY)).toMatchObject({ from: "2020-01-01", to: "2024-12-31" });
  expect(rangeDates({ ...s, end: undefined }, TODAY)).toEqual({ from: "2020-01-01", to: null });
  expect(rangeDates({ ...s, range: 5 }, TODAY)).toEqual({ from: "2021-07-21", to: null });
});

it("annual frequency: flows as TTM, balances as the quarter, QoQ as YoY", () => {
  const s: ChartState = { ...emptyChart(), frequency: "annual" };
  expect(request(metric(), s, hasTtm, TODAY)).toMatchObject({ formula: "revenue", view: "ttm" });
  expect(request(metric({ metric: "cash" }), s, hasTtm, TODAY)).toMatchObject({ formula: "cash", view: "quarter" });
  expect(request(metric({ transform: "qoq" }), s, hasTtm, TODAY)).toMatchObject({ formula: "(revenue) / (revenue)[-4q] - 1", view: "ttm" });
  expect(request(metric(), emptyChart(), hasTtm, TODAY)).toMatchObject({ view: "quarter" }); // quarterly unchanged
  const short = () => "NVDA";
  expect(autoName(metric({ transform: "ttm" }), short, (m) => m, "annual")).toBe("NVDA revenue");
  expect(autoName(metric({ transform: "ttm" }), short, (m) => m)).toBe("NVDA revenue (TTM)");
});

const pt = (key: number, period: string): Point => ({ key, period, periodEnd: "2025-12-31", y: key, raw: String(key), sources: [] });

it("annual points are each year's fourth quarter, labelled by the year", () => {
  const out = annualPoints([pt(8099, "FY2024 Q4"), pt(8100, "FY2025 Q1"), pt(8103, "FY2025 Q4"), pt(8104, "CY2026 Q1")]);
  expect(out.map((p) => [p.key, p.period])).toEqual([[8099, "FY2024"], [8103, "FY2025"]]);
});

it("restores the new chart settings and drops malformed ones", () => {
  const s = restoreChart({ layers: [], range: "custom", start: "2020-01-01", end: "x", frequency: "annual", deck: true, subtitle: " a\u0007b " });
  expect(s).toMatchObject({ range: "custom", start: "2020-01-01", frequency: "annual", deck: true, subtitle: "ab" });
  expect(s.end).toBeUndefined();
  expect(restoreChart({ frequency: "weekly", deck: "yes" })).not.toHaveProperty("frequency");
});

const src = (companyId: string, form: string, filed: string) => ({ form, filed, accessionNo: `${companyId}-${filed}`, url: null, periodEnd: filed, companyId });
const series = (id: number, unit: string, axis: "L" | "R", sources: ReturnType<typeof src>[]): Series => ({
  id, name: `s${id}`, unit, color: id, type: "line", axis,
  points: [{ key: 8100, period: "CY2025 Q1", periodEnd: "2025-03-31", y: 1, raw: "1", sources },
    { key: 8103, period: "CY2025 Q4", periodEnd: "2025-12-31", y: 2, raw: "2", sources: [] }],
});

it("deck subtitle and source footnote say what, when and from which filings", () => {
  const plotted = [
    series(1, "USD", "L", [src("0000077476", "10-Q", "2026-07-22"), src("0000077476", "10-K", "2026-02-03")]),
    series(2, "ratio", "R", [src("0000021344", "10-Q", "2026-07-23")]),
  ];
  const short = (id: string) => (id === "0000077476" ? "PEP" : "KO");
  expect(autoSubtitle(plotted, "quarterly", "calendar")).toBe("Calendar quarters, CY2025 Q1–CY2025 Q4 · USD (left), % (right)");
  expect(autoSubtitle(plotted.slice(0, 1), "annual", "fiscal")).toBe("Fiscal years, CY2025 Q1–CY2025 Q4 · USD");
  expect(sourceFootnote(plotted, short, "2026-10-21T12:00:00Z")).toBe(
    "Source: SEC filings via Tributary; 10-K, 10-Q for KO, PEP; latest KO 10-Q filed Jul 23, 2026; as of Oct 21, 2026.",
  );
  expect(sourceFootnote([], short, null)).toBe("Source: SEC filings via Tributary.");
  expect(methodNote("fiscal", "annual", new Set(["yoy"]))).toBe(
    "Fiscal years: flows are fiscal-year totals (TTM at Q4), balances year-end values; YoY over years.",
  );
  expect(methodNote("calendar", "quarterly", new Set(["qoq"]))).toBe("Calendar quarters; TTM = trailing four quarters; QoQ over quarters.");
});

it("layout makes room for a subtitle and extra footnote lines, and captions the axes' units", () => {
  const plotted = [series(1, "USD", "L", []), series(2, "ratio", "R", [])];
  const plain = layout(plotted, true);
  const deck = layout(plotted, true, [], { subtitle: true, footLines: 3 });
  expect(deck.plot.t - plain.plot.t).toBe(18);
  expect(deck.height - plain.height).toBe(18 + 14);
  expect(plain.units).toEqual({ left: "USD", right: "%" });
  expect(layout(plotted.slice(0, 1), false).units).toEqual({ left: "USD", right: null });
});

const data = { formula: "revenue", unit: "USD", basis: "calendar", view: "ttm", alignment: "calendar", as_of: "", inputs: [], points: [] } as unknown as FormulaSeries;

beforeEach(() => clearChartCache());

it("caches evaluate answers per user token and request, oldest out first", () => {
  remember("t1", "a", data);
  expect(cached("t1", "a")).toBe(data);
  expect(cached("t2", "a")).toBeUndefined(); // another user: nothing carried over
  expect(cached("t1", "a")).toBeUndefined();
  for (let i = 0; i <= MAX_ENTRIES; i++) remember("t1", `k${i}`, data);
  expect(cached("t1", "k0")).toBeUndefined();
  expect(cached("t1", `k${MAX_ENTRIES}`)).toBe(data);
});
