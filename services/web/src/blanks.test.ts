import { describe, expect, it } from "vitest";

import { NO_DATA, NOT_APPLICABLE, NOT_MEANINGFUL, NOT_REPORTED, notReportedTitle, readFigure } from "./blanks";

const base = {
  label: "Gross margin",
  columnKey: "gross_margin_ttm",
  source: { period_end: "2024-07-31", accession_no: "a", form: "10-K", filed: "2025-10-29" },
  format: (v: number) => `${(v * 100).toFixed(1)}%`,
  sourceTitle: "Gross margin, TTM to Jul 31, 2024.",
};

describe("the marks", () => {
  it("each mean one thing and differ", () => {
    expect(new Set([NOT_REPORTED, NO_DATA, NOT_APPLICABLE, NOT_MEANINGFUL]).size).toBe(4);
  });

  it("a plain figure is its value, ranks and carries its source", () => {
    expect(readFigure({ ...base, raw: "0.5" })).toMatchObject({ value: 0.5, text: "50.0%", title: base.sourceTitle, asOf: null, old: false, status: null });
  });

  it("a user's own metric with an input that has no figure names the input (Riley, test 10, finding 26)", () => {
    const own = readFigure({ ...base, label: "Revenue per share", columnKey: "m_abc", raw: null, source: null,
      reading: { status: "not_reported", reason: "shares_outstanding: no figure on file" } });
    expect(own).toMatchObject({ value: null, text: NOT_REPORTED });
    expect(own.title).toBe("Revenue per share: no value for this company: an input has no figure (shares_outstanding: no figure on file).");
  });

  it("a real zero is a figure, not a blank", () => {
    expect(readFigure({ ...base, raw: "0" })).toMatchObject({ value: 0, text: "0.0%" });
  });

  it("no value at all is not reported, or can't be computed when it's made of others", () => {
    expect(readFigure({ ...base, raw: null, source: null })).toMatchObject({ value: null, text: NOT_REPORTED });
    expect(notReportedTitle("Free cash flow", "free_cash_flow_ttm")).toContain("needs operating cash flow and capital expenditure");
    expect(notReportedTitle("Cash", "cash_q")).toBe("Cash: not reported. This company's filings don't give this figure.");
  });

  it("n/m hides the figure from ranking, not from the reader", () => {
    const f = readFigure({ ...base, raw: "2098.283", reading: { status: "not_meaningful", reason: "gross profit is larger than revenue" } });
    expect(f).toMatchObject({ value: null, text: NOT_MEANINGFUL, status: "not_meaningful" });
    expect(f.title).toContain("because gross profit is larger than revenue");
    expect(f.title).toContain("209828.3%");
  });

  it("n/m without a figure, as on negative equity, just gives the reason", () => {
    const f = readFigure({ ...base, raw: null, source: null, reading: { status: "not_meaningful", reason: "stockholders' equity is negative" } });
    expect(f).toMatchObject({ text: NOT_MEANINGFUL });
    expect(f.title).toContain("equity is negative");
    expect(f.title).not.toContain("on file");
  });

  it("old reads as not reported since its date, the figure in the hover, out of ranking; behind keeps it in", () => {
    const old = readFigure({ ...base, raw: "0.5", reading: { status: "old" }, latestPeriod: "2026-09-30" });
    expect(old).toMatchObject({ value: null, text: NOT_REPORTED, old: true, asOf: "Jul '24" });
    expect(old.title).toMatch(/^Gross margin: not reported since Jul \d+, 2024\. It needs gross profit and revenue/);
    expect(old.title).toContain("The last figure on file, 50.0%");
    expect(old.title).toContain("latest period is Sep 30, 2026");
    expect(old.title).toContain("stays out of sorts, filters and medians");
    const behind = readFigure({ ...base, raw: "0.5", reading: { status: "behind" }, latestPeriod: "2026-09-30" });
    expect(behind).toMatchObject({ value: 0.5, old: false, asOf: "Jul '24" });
  });
});

describe("069: why a blank is blank, a company long past its latest period, an annual figure", () => {
  it("not computed reads n/a with the API's reason (Riley 3: Walmart, Costco, AT&T, Verizon)", () => {
    const f = readFigure({ ...base, raw: null, source: null,
      reading: { status: "not_computed", reason: "a warehouse club's or variety store's revenue includes membership fees" } });
    expect(f).toMatchObject({ value: null, text: NOT_APPLICABLE, status: "not_computed" });
    expect(f.title).toBe("Gross margin: not computed for this company. A warehouse club's or variety store's revenue includes membership fees.");
  });

  it("stale keeps the figure and its date but never ranks (Riley 23: Aphoenity's Sep 2024 margin)", () => {
    const f = readFigure({ ...base, raw: "0.847", reading: { status: "stale",
      reason: "this company's latest figures here are for the period ended Sep 30, 2024, and no report since has reached Tributary" } });
    expect(f).toMatchObject({ value: null, text: "84.7%", asOf: "Jul '24", old: false, status: "stale" });
    expect(f.title).toContain("Gross margin: This company's latest figures here are for the period ended Sep 30, 2024");
    expect(f.title).toContain("stays out of sorts, filters and medians");
  });

  it("an annual report's figure says it is a year, beside quarterly ones (owner decision 4)", () => {
    const source = { period_end: "2025-12-31", accession_no: "a", form: "20-F", filed: "2026-04-16" };
    const f = readFigure({ ...base, raw: "0.457", source });
    expect(f).toMatchObject({ value: 0.457, asOf: "annual, period ended Dec '25" });
    expect(f.title).toContain("An annual figure: the 20-F's fiscal year, period ended Dec 31, 2025");
    expect(readFigure({ ...base, raw: "0.457" }).asOf).toBeNull();
  });

  it("an amount in another currency carries its code for the table's foot", () => {
    const f = readFigure({ ...base, columnKey: "revenue_ttm", raw: "2894307700000", reading: { status: "other_currency", unit: "TWD" } });
    expect(f).toMatchObject({ value: null, currency: "TWD" });
  });
});
