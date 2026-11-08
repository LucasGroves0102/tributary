import { describe, expect, it } from "vitest";

import { entry, TESLA } from "../fixtures";
import type { ScreenerRow } from "../types";
import { MASKED_TITLE, screenRow } from "./rows";
import { activeFilters, describe as describeScreen, fraction, fyeMonth, parseScreen, screenParams, screenQuery } from "./screen";

describe("screen filters", () => {
  it("count what's set, for the folded filter band", () => {
    expect(activeFilters({})).toBe(0);
    expect(activeFilters({ sort: "name", page: 2 })).toBe(0);
    expect(activeFilters({ q: "KO", growth: 0, mine: true })).toBe(3);
    expect(activeFilters({ mine: false, q: "" })).toBe(0);
  });

  it("map to the Query API's screener parameters", () => {
    const p = screenParams({
      q: "pep",
      industry: "beverages",
      band: "10b-100b",
      growth: 5,
      margin: 12.5,
      fye: 12,
      exchange: "Nasdaq",
      filer: "large_accelerated",
      mine: true,
      sort: "-operating_margin_ttm",
      page: 2,
    });
    expect(p).toMatchObject({
      q: "pep",
      industry: "beverages",
      revenue_band: "10b-100b",
      where: ["revenue_growth_ttm:gte:0.05", "operating_margin_ttm:gte:0.125"],
      fiscal_year_end_month: 12,
      exchange: ["Nasdaq"],
      filer_category: ["large_accelerated"],
      access: "financials",
      sort: "-operating_margin_ttm",
      limit: 50,
      offset: 100,
    });
    expect(p.columns).toContain("debt_to_equity_q");
  });

  it("defaults to every company with financials, largest first", () => {
    const p = screenParams({});
    expect(p).toMatchObject({ sort: "-revenue_ttm", offset: 0 });
    expect(p).not.toHaveProperty("where");
    expect(p).not.toHaveProperty("access");
  });

  it("turns percents into fractions without float noise", () => {
    expect(fraction(7)).toBe("0.07");
    expect(fraction(12.5)).toBe("0.125");
    expect(fraction(-3)).toBe("-0.03");
    expect(fraction(29)).toBe("0.29");
  });

  it("serialize canonically and parse back", () => {
    const f = { margin: 20, band: "1b-10b" as const, mine: true };
    expect(screenQuery(f)).toBe("band=1b-10b&margin=20&mine=1");
    expect(parseScreen(screenQuery(f))).toEqual(f);
    expect(screenQuery({ sort: "-revenue_ttm", page: 0 })).toBe("");
  });

  it("describe themselves for the assistant and the page", () => {
    expect(describeScreen({})).toBe("no filters");
    expect(describeScreen({ margin: 25, industry: "software" })).toBe(
      'industry contains "software", operating margin ≥ 25%',
    );
  });
});

describe("screener rows", () => {
  const row = (e: typeof TESLA): ScreenerRow => ({
    ...e,
    filer_categories: null,
    state_of_incorporation: null,
    values: { ...e.values },
  });

  it("formats values with units and their source filing", () => {
    const r = screenRow(row(entry()));
    expect(r.readable).toBe(true);
    expect(r.ticker).toBe("NVDA");
    expect(r.fye).toBe("January");
    expect(r.cells.revenue_ttm?.text).toBe("$130.50B");
    expect(r.cells.revenue_ttm?.title).toContain("trailing twelve months to Jul 26, 2026");
    expect(r.cells.operating_margin_ttm?.text).toBe("62.3%");
    expect(r.cells.revenue_growth_ttm?.text).toBe("+69.0%");
    expect(r.cells.net_debt_q?.text).toBe("-$43.21B");
    // not requested in the fixture: shown as missing, not masked
    expect(r.cells.debt_to_equity_q?.text).toBe("—");
    expect(r.cells.debt_to_equity_q?.title).not.toBe(MASKED_TITLE);
  });

  it("shows ungranted companies as metadata only, even if a value slipped through", () => {
    const r = screenRow({ ...row(TESLA), values: { revenue_ttm: "97690000000" } });
    expect(r.readable).toBe(false);
    expect(r.name).toBe("Tesla");
    expect(r.cells.revenue_ttm).toEqual({ value: null, text: "—", title: MASKED_TITLE });
  });
});

describe("how a row reads what the API says about each value", () => {
  const row = (over: Partial<ScreenerRow>): ScreenerRow => ({
    ...entry({ company_id: "0000019617", name: "JPMorgan Chase", industry: "National Commercial Banks", values: {} }),
    filer_categories: null, state_of_incorporation: null, ...over,
  });
  const SRC = { period_end: "2020-12-31", accession_no: "a", form: "10-K", filed: "2021-03-01" };

  it("a bank reads n/a (bank) with or without a figure, and its figure is in the hover", () => {
    const reason = "this is a bank: deposits are its debt";
    const r = screenRow(row({
      values: { revenue_ttm: "180000000000", free_cash_flow_ttm: "-74150000000", net_debt_q: null },
      sources: { free_cash_flow_ttm: SRC },
      readings: { free_cash_flow_ttm: { status: "not_applicable", reason }, net_debt_q: { status: "not_applicable", reason } },
    }));
    for (const k of ["free_cash_flow_ttm", "net_debt_q"]) {
      expect(r.cells[k]).toMatchObject({ text: "n/a (bank)", value: null });
      expect(r.cells[k]?.title).toContain("This is a bank");
    }
    expect(r.cells.free_cash_flow_ttm?.title).toContain("The figure on file: -$74.15B");
    expect(r.cells.gross_margin_ttm?.text).toBe("—");
    expect(r.cells.gross_margin_ttm?.title).toContain("can't be computed");
    expect(r.cells.revenue_ttm).toMatchObject({ text: "$180.00B", value: 180000000000 });
  });

  it("a value that isn't meaningful reads n/m and can't rank, with the exact figure and the reason", () => {
    const r = screenRow(row({
      values: { gross_margin_ttm: "2098.283" },
      sources: { gross_margin_ttm: SRC },
      readings: { gross_margin_ttm: { status: "not_meaningful", reason: "revenue is under $100K, too small a base" } },
    }));
    expect(r.cells.gross_margin_ttm).toMatchObject({ text: "n/m", value: null, status: "not_meaningful" });
    expect(r.cells.gross_margin_ttm?.title).toContain("because revenue is under $100K");
    expect(r.cells.gross_margin_ttm?.title).toContain("209828.3%");
  });

  it("an old value shows, dimmed and dated, and can't rank; one merely behind shows its date and counts", () => {
    const r = screenRow(row({
      latest_period: "2026-09-30",
      values: { net_debt_q: "4680000", debt_to_equity_q: "1.5" },
      sources: { net_debt_q: SRC, debt_to_equity_q: { ...SRC, period_end: "2026-06-30" } },
      readings: { net_debt_q: { status: "old" }, debt_to_equity_q: { status: "behind" } },
    }));
    expect(r.cells.net_debt_q).toMatchObject({ text: "$4.68M", value: null, old: true, asOf: "Dec '20" });
    expect(r.cells.net_debt_q?.title).toContain("latest period is Sep 30, 2026");
    expect(r.cells.debt_to_equity_q).toMatchObject({ text: "1.50x", value: 1.5, old: false, asOf: "Jun '26" });
  });
});

describe("the month a fiscal year ends in", () => {
  it("gives a date in the first week to the month before, for every month", () => {
    expect(fyeMonth("0103")).toBe("December"); // J&J
    expect(fyeMonth("0102")).toBe("December"); // L3Harris, Snap-on, Textron, Stanley Black & Decker
    expect(fyeMonth("0101")).toBe("December"); // Leidos, Exponent
    expect(fyeMonth("0201")).toBe("January"); // Target, Macy's
    expect(fyeMonth("0403")).toBe("March");
    expect(fyeMonth("0703")).toBe("June");
    expect(fyeMonth("1003")).toBe("September"); // Disney
    expect(fyeMonth("1101")).toBe("October"); // Broadcom, Deere
  });

  it("keeps a late date in its own month", () => {
    expect(fyeMonth("0131")).toBe("January"); // NVIDIA, Home Depot
    expect(fyeMonth("0126")).toBe("January");
    expect(fyeMonth("1231")).toBe("December");
    expect(fyeMonth("0926")).toBe("September"); // Apple
    expect(fyeMonth("0108")).toBe("January");
  });

  it("reads nothing into nothing", () => {
    expect(fyeMonth(null)).toBeNull();
    expect(fyeMonth("")).toBeNull();
    expect(fyeMonth("1399")).toBeNull();
  });
});
