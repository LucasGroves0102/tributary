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

  it("old keeps the figure, dated and dimmed, out of ranking; behind keeps it in", () => {
    const old = readFigure({ ...base, raw: "0.5", reading: { status: "old" }, latestPeriod: "2026-09-30" });
    expect(old).toMatchObject({ value: null, text: "50.0%", old: true, asOf: "Jul '24" });
    expect(old.title).toContain("stays out of sorts, filters and medians");
    const behind = readFigure({ ...base, raw: "0.5", reading: { status: "behind" }, latestPeriod: "2026-09-30" });
    expect(behind).toMatchObject({ value: 0.5, old: false, asOf: "Jul '24" });
  });
});
