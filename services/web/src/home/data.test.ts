import { describe, expect, it } from "vitest";

import { ACC, entry, feedFiling, NVDA, revenueRow, TESLA, TSLA } from "../fixtures";
import type { CalendarEntry } from "../types";
import { expectedItems, median, recentFilings, revenueSeries, status, watchRows } from "./data";

describe("watchRows", () => {
  const revenue = {
    [NVDA]: [
      revenueRow("2026-07-26", "46743000000", "FY2027 Q2"),
      revenueRow("2026-04-26", "44062000000", "FY2027 Q1"),
    ],
  };
  const [nvda, tsla] = watchRows([entry(), TESLA], revenue, new Set([NVDA]));

  it("formats each value with its unit and gives period and source on hover", () => {
    expect(nvda!.cells.revenue_ttm!.text).toBe("$130.50B");
    expect(nvda!.cells.revenue_growth_ttm!.text).toBe("+69.0%");
    expect(nvda!.cells.operating_margin_ttm!.text).toBe("62.3%");
    expect(nvda!.cells.net_debt_q!.text).toBe("-$43.21B");
    expect(nvda!.cells.revenue_ttm!.title).toBe(
      `Revenue, trailing twelve months to Jul 26, 2026. Source: 10-Q filed Aug 26, 2026, ${ACC}`,
    );
    expect(nvda!.cells.net_debt_q!.title).toContain("at the quarter end, Jul 26, 2026");
  });

  it("labels the last filing's period from the financials and orders the sparkline", () => {
    expect(nvda!.last).toMatchObject({ form: "10-Q", fiscalPeriod: "FY2027 Q2", periodEnd: "2026-07-26" });
    expect(nvda!.revenue).toEqual([44062000000, 46743000000]);
    expect(nvda!.ticker).toBe("NVDA");
    expect(nvda!.isNew).toBe(true);
  });

  it("shows no values for a watched company without a grant, and says why", () => {
    expect(tsla!.readable).toBe(false);
    for (const c of Object.values(tsla!.cells)) {
      expect(c).toMatchObject({ value: null, text: "—" });
      expect(c.title).toContain("Watching it grants nothing");
    }
    expect(tsla!.last?.fiscalPeriod).toBeNull();
    expect(tsla!.revenue).toEqual([]);
  });
});

it("ignores rows of other metrics and unparseable values in the revenue series", () => {
  const rows = [revenueRow("2026-04-26", "1", "Q1"), { ...revenueRow("2026-07-26", "x", "Q2") }];
  rows.push({ ...revenueRow("2026-07-26", "5", "Q2"), metric: "net_income" });
  expect(revenueSeries(rows).values).toEqual([1]);
});

describe("recentFilings", () => {
  const latest = [
    feedFiling({ accession_no: "0001045810-26-000080", form: "8-K", filed: "2026-10-15" }),
    feedFiling(),
    feedFiling({ accession_no: "0001045810-26-000001", form: "10-K", filed: "2026-03-01" }),
  ];
  const items = recentFilings(latest, [entry(), TESLA], "2026-10-01T09:00:00Z", "2026-10-21");

  it("merges the feed with watched companies' last reports, newest first, within 90 days", () => {
    expect(items.map((f) => f.accessionNo)).toEqual([
      "0001045810-26-000080",
      ACC,
      "0001628280-26-000010",
    ]);
  });

  it("opens feed filings in the reader and marks what's new since the last visit", () => {
    expect(items.map((f) => [f.readable, f.isNew])).toEqual([
      [true, true],
      [true, false],
      [false, false],
    ]);
  });

  it("adds the public index, so a free plan sees 8-Ks it can't read", () => {
    const index = [{
      accession_no: "0001318605-26-000099", company_id: TSLA, name: "Tesla, Inc.", form: "8-K",
      filed: "2026-10-20", accepted_at: null, period_of_report: null, filing_url: null,
      document_url: null, sections: null, sections_available: false,
    }, {
      // a parsed filing the feed also has: the feed's entry wins (readable)
      accession_no: ACC, company_id: NVDA, name: null, form: "10-Q", filed: "2026-08-26",
      accepted_at: null, period_of_report: null, filing_url: null, document_url: null,
      sections: null, sections_available: false,
    }];
    const merged = recentFilings(latest, [entry(), TESLA], "2026-10-01T09:00:00Z", "2026-10-21", 90, index);
    expect(merged[0]).toMatchObject({ accessionNo: "0001318605-26-000099", form: "8-K", readable: false, isNew: true, name: "Tesla, Inc." });
    expect(merged.find((f) => f.accessionNo === ACC)).toMatchObject({ readable: true, name: "NVIDIA CORP" });
    expect(merged).toHaveLength(4);
  });
});

it("merges calendars without duplicates and marks watched companies", () => {
  const base = entry().next_expected!;
  const cal = (id: string, name: string, date: string): CalendarEntry => ({
    ...base,
    company_id: id,
    name,
    expected_date: date,
  });
  const items = expectedItems(
    [cal(TSLA, "Tesla, Inc.", "2026-10-30")],
    [cal(NVDA, "NVIDIA CORP", "2026-11-19"), cal(TSLA, "Tesla, Inc.", "2026-10-30")],
    new Set([TSLA]),
  );
  expect(items.map((i) => [i.name, i.watched])).toEqual([
    ["Tesla, Inc.", true],
    ["NVIDIA CORP", false],
  ]);
});

it("summarizes the watchlist for the status strip", () => {
  const rows = watchRows(
    [
      entry(),
      entry({ company_id: "0000789019", name: "MICROSOFT CORP", tickers: ["MSFT"], values: { ...entry().values, revenue_growth_ttm: "0.15" } }),
      TESLA,
    ],
    {},
    new Set(),
  );
  const s = status(rows, [
    { companyId: NVDA, name: "", accessionNo: "a", form: "8-K", filed: "2026-10-15", periodEnd: null, readable: true, isNew: true },
    { companyId: NVDA, name: "", accessionNo: "b", form: "10-Q", filed: "2026-10-14", periodEnd: null, readable: true, isNew: true },
    { companyId: NVDA, name: "", accessionNo: "c", form: "10-Q", filed: "2026-08-26", periodEnd: null, readable: true, isNew: false },
  ]);
  expect(s).toMatchObject({ companies: 3, industries: 2, newFilings: 2, newPeriodic: 1, new8k: 1 });
  expect(s.medianGrowth).toBeCloseTo(0.42);
  expect(s.best?.row.ticker).toBe("NVDA");
  expect(s.next?.expected.expected_date).toBe("2026-11-19");
  expect(median([])).toBeNull();
  expect(median([3, 1, 2])).toBe(2);
});
