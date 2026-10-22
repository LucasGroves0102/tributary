import { describe, expect, it } from "vitest";

import { entry, TESLA } from "../fixtures";
import type { ScreenerRow } from "../types";
import { MASKED_TITLE, screenRow } from "./rows";
import { describe as describeScreen, fraction, parseScreen, screenParams, screenQuery } from "./screen";

describe("screen filters", () => {
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
    expect(r.name).toBe("Tesla, Inc.");
    expect(r.cells.revenue_ttm).toEqual({ value: null, text: "—", title: MASKED_TITLE });
  });
});
