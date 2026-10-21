/** Query API responses for tests, shaped like the real ones (strings for Decimals, dates as
 * ISO strings). Demo data: alice can read NVDA financials and watches TSLA without a grant. */

import type { FinancialRow, LatestFiling, ScreenerRow, WatchlistEntry } from "./types";

export const NVDA = "0001045810";
export const TSLA = "0001318605";
export const ACC = "0001045810-26-000075";

const SRC = { period_end: "2026-07-26", accession_no: ACC, form: "10-Q", filed: "2026-08-26" };

export function entry(over: Partial<WatchlistEntry> = {}): WatchlistEntry {
  return {
    company_id: NVDA,
    name: "NVIDIA CORP",
    tickers: ["NVDA"],
    exchanges: ["Nasdaq"],
    sic: "3674",
    industry: "Semiconductors & Related Devices",
    fiscal_year_end: "0126",
    filer_category: "large_accelerated",
    access: ["financials"],
    values: {
      revenue_ttm: "130497000000",
      revenue_growth_ttm: "0.69",
      operating_margin_ttm: "0.6234",
      free_cash_flow_ttm: "72000000000",
      net_debt_q: "-43210000000",
    },
    sources: {
      revenue_ttm: SRC,
      revenue_growth_ttm: SRC,
      operating_margin_ttm: SRC,
      free_cash_flow_ttm: SRC,
      net_debt_q: SRC,
    },
    added_at: "2026-10-01T00:00:00Z",
    last_filing: { accession_no: ACC, form: "10-Q", filing_date: "2026-08-26", period_end: "2026-07-26" },
    next_expected: {
      form: "10-Q",
      period_end: "2026-10-25",
      expected_date: "2026-11-19",
      deadline: "2026-12-04",
      deadline_days: 40,
      filer_category: "large_accelerated",
      basis: "median lag over the last 8 10-Qs",
      typical_lag_days: 25,
      lag_samples: 8,
      status: "upcoming",
      estimate: true,
      last_period_end: "2026-07-26",
      last_filed: "2026-08-26",
    },
    ...over,
  };
}

export const TESLA = entry({
  company_id: TSLA,
  name: "Tesla, Inc.",
  tickers: ["TSLA"],
  industry: "Motor Vehicles & Passenger Car Bodies",
  access: [],
  values: {
    revenue_ttm: null,
    revenue_growth_ttm: null,
    operating_margin_ttm: null,
    free_cash_flow_ttm: null,
    net_debt_q: null,
  },
  sources: {},
  last_filing: {
    accession_no: "0001628280-26-000010",
    form: "10-Q",
    filing_date: "2026-07-24",
    period_end: "2026-06-30",
  },
  next_expected: null,
});

/** NVDA as one /v1/screener row with the company page's header columns. */
export function nvdaRow(over: Partial<ScreenerRow> = {}): ScreenerRow {
  return {
    ...entry(),
    filer_categories: ["Large accelerated filer"],
    state_of_incorporation: "DE",
    values: {
      revenue_ttm: "165218000000",
      revenue_growth_ttm: "0.69",
      eps_diluted_q: "1.08",
      cash_q: "11639000000",
      revenue_q: "46743000000",
    },
    sources: { revenue_ttm: SRC, revenue_growth_ttm: SRC, eps_diluted_q: SRC, cash_q: SRC, revenue_q: SRC },
    ...over,
  };
}

/** One financials row of any metric. */
export function fin(metric: string, end: string, value: string, fiscal: string, over: Partial<FinancialRow> = {}): FinancialRow {
  return {
    metric,
    period_start: null,
    period_end: end,
    fiscal_period: fiscal,
    calendar_period: null,
    value,
    unit: metric === "eps_diluted" ? "USD/shares" : metric.endsWith("margin") ? "ratio" : "USD",
    value_source: "reported",
    citation: {
      accession_no: ACC,
      form: "10-Q",
      filed: "2026-08-26",
      xbrl_tag: "Revenues",
      source_accessions: null,
      filing_url: "https://www.sec.gov/x/",
    },
    freshness: { filed: "2026-08-26", ingested_at: "2026-08-26T21:00:00Z" },
    ...over,
  };
}

export function revenueRow(periodEnd: string, value: string, fiscal: string): FinancialRow {
  return {
    metric: "revenue",
    period_start: null,
    period_end: periodEnd,
    fiscal_period: fiscal,
    value,
    unit: "USD",
    value_source: "reported",
    citation: {
      accession_no: ACC,
      form: "10-Q",
      filed: "2026-08-26",
      xbrl_tag: "us-gaap:Revenues",
      source_accessions: null,
      filing_url: null,
    },
    freshness: { filed: "2026-08-26", ingested_at: "2026-08-26T21:00:00Z" },
  };
}

export function feedFiling(over: Partial<LatestFiling> = {}): LatestFiling {
  return {
    company_id: NVDA,
    name: "NVIDIA CORP",
    accession_no: ACC,
    form: "10-Q",
    filed: "2026-08-26",
    period_of_report: "2026-07-26",
    document_url: null,
    sections: 12,
    ingested_at: "2026-08-26T21:00:00Z",
    ...over,
  };
}
