import { describe, expect, it } from "vitest";

import type { FinancialRow } from "../types";
import {
  annualHistory,
  baseYear,
  checkAssumptions,
  GROWTH_FLAG_POINTS,
  defaultAssumptions,
  growthOf,
  historyStats,
  modelRows,
  modelSeries,
  modelState,
  parsePercent,
  project,
  resizeAssumptions,
  restoreModel,
  statLabel,
  type HistoryYear,
} from "./model";
import fixture from "./stats-fixture.json";

const NVDA = "0001045810";

function row(metric: string, fp: string, end: string, value: string): FinancialRow {
  return {
    metric,
    period_start: null,
    period_end: end,
    fiscal_period: fp,
    calendar_period: null,
    value,
    unit: "USD",
    value_source: "derived",
    citation: {
      accession_no: `0001045810-${end.slice(2, 4)}-000001`,
      form: "10-K",
      filed: `${end.slice(0, 4)}-12-01`,
      xbrl_tag: "Revenues",
      source_accessions: null,
      filing_url: null,
    },
    freshness: { filed: `${end.slice(0, 4)}-12-01`, ingested_at: `${end.slice(0, 4)}-12-02T00:00:00Z` },
  };
}

function year(fy: number, revenue: number | null, operatingIncome: number | null = null): HistoryYear {
  return { fy, periodEnd: `${fy}-12-31`, revenue, operatingIncome, revenueSource: null, operatingIncomeSource: null };
}

describe("annualHistory", () => {
  const rows = [
    row("revenue", "FY2023 Q4", "2023-12-31", "100"),
    row("operating_income", "FY2023 Q4", "2023-12-31", "20"),
    row("revenue", "FY2024 Q3", "2024-09-30", "105"), // a TTM at Q3: not a fiscal year
    row("revenue", "FY2024 Q4", "2024-12-31", "110"),
    row("operating_income", "FY2024 Q4", "2024-12-31", "33"),
    row("revenue", "FY2025 Q4", "2025-12-31", "121"),
    row("gross_profit", "FY2025 Q4", "2025-12-31", "60"), // another metric
    row("revenue", "FY2022 Q4", "2022-12-31", "not a number"),
  ];

  it("keeps fiscal Q4 TTM rows as fiscal years, oldest first, with their filings", () => {
    const h = annualHistory(rows, 8, NVDA);
    expect(h.map((y) => [y.fy, y.revenue, y.operatingIncome])).toEqual([
      [2023, 100, 20],
      [2024, 110, 33],
      [2025, 121, null],
    ]);
    expect(h[0]!.revenueSource).toMatchObject({ form: "10-K", accessionNo: "0001045810-23-000001", companyId: NVDA });
  });

  it("keeps only the newest years asked for", () => {
    expect(annualHistory(rows, 2).map((y) => y.fy)).toEqual([2024, 2025]);
  });
});

describe("historyStats", () => {
  it("computes a 5-year CAGR and the mean of the last 5 margins", () => {
    // 100 → 161.051 over 5 years is 10% a year
    const h = [year(2019, 80, 8), year(2020, 100, 10), year(2021, 110, 22), year(2022, 121, 24.2), year(2023, 133.1, 13.31), year(2024, 146.41, 29.282), year(2025, 161.051, 16.1051)];
    const s = historyStats(h);
    expect(s.cagr).toBeCloseTo(0.1, 10);
    expect(s.cagrYears).toBe(5);
    // margins 2021-2025: 20%, 20%, 10%, 20%, 10%
    expect(s.avgMargin).toBeCloseTo(0.16, 10);
    expect(s.marginYears).toBe(5);
    expect(s.lastGrowth).toBeCloseTo(0.1, 10);
    expect(s.lastMargin).toBeCloseTo(0.1, 10);
    expect(statLabel(s.cagr, s.cagrYears, "CAGR")).toBe("5-yr CAGR 10.0%");
    expect(statLabel(s.avgMargin, s.marginYears, "avg")).toBe("5-yr avg 16.0%");
  });

  it("uses a shorter window when history is short, and says when there's none", () => {
    const s = historyStats([year(2024, 100, 10), year(2025, 120, 30)]);
    expect(s.cagr).toBeCloseTo(0.2, 10);
    expect(s.cagrYears).toBe(1);
    expect(s.avgMargin).toBeCloseTo(0.175, 10);
    const none = historyStats([year(2025, 120)]);
    expect([none.cagr, none.avgMargin]).toEqual([null, null]);
    expect(statLabel(none.cagr, none.cagrYears, "CAGR")).toBe("no growth history");
    expect(historyStats([]).cagr).toBeNull();
  });

  it("starts the CAGR at the first positive revenue and ends at the latest year with revenue", () => {
    const s = historyStats([year(2021, 0), year(2022, 100), year(2023, 110), year(2024, null)]);
    expect(baseYear([year(2023, 121), year(2024, null)])!.fy).toBe(2023);
    expect(s.cagr).toBeCloseTo(0.1, 10);
    expect(s.cagrYears).toBe(1);
  });

  it("gives no growth across a missing year", () => {
    expect(growthOf(year(2022, 100), year(2024, 150))).toBeNull();
    expect(growthOf(year(2023, -5), year(2024, 150))).toBeNull();
    expect(growthOf(year(2023, 100), year(2024, 150))).toBeCloseTo(0.5, 10);
  });
});

describe("project", () => {
  const history = [year(2024, 100, 10), year(2025, 200, 50)];

  it("grows revenue from the base year and applies each year's margin", () => {
    const p = project(history, [
      { growth: 10, margin: 20, note: "price increase" },
      { growth: -50, margin: 0 },
      { growth: 0, margin: -10 },
    ]);
    expect(p.map((y) => y.fy)).toEqual([2026, 2027, 2028]);
    expect(p[0]).toMatchObject({ growth: 0.1, margin: 0.2, note: "price increase" });
    expect(p[0]!.revenue).toBeCloseTo(220, 10);
    expect(p[0]!.operatingIncome).toBeCloseTo(44, 10);
    expect(p[1]!.revenue).toBeCloseTo(110, 10);
    expect(p[1]!.operatingIncome).toBeCloseTo(0, 10);
    expect(p[2]!.revenue).toBeCloseTo(110, 10);
    expect(p[2]!.operatingIncome).toBeCloseTo(-11, 10);
  });

  it("starts from the latest year with revenue, and is empty without one", () => {
    expect(project([...history, year(2026, null, 5)], [{ growth: 50, margin: 10 }])[0]).toMatchObject({ fy: 2026, revenue: 300 });
    expect(project([year(2025, null)], [{ growth: 5, margin: 5 }])).toEqual([]);
    expect(project([], [{ growth: 5, margin: 5 }])).toEqual([]);
  });

  it("labels the table's history and forecast rows", () => {
    const rows = modelRows(history, project(history, [{ growth: 10, margin: 20 }]));
    expect(rows.map((r) => [r.label, r.forecast])).toEqual([
      ["FY2024", false],
      ["FY2025", false],
      ["FY2026E", true],
    ]);
    expect(rows[0]!.growth).toBeNull();
    expect(rows[1]!.growth).toBeCloseTo(1, 10);
    expect(rows[1]!.margin).toBeCloseTo(0.25, 10);
  });
});

describe("modelSeries", () => {
  it("draws history solid and the forecast dashed from the base year, same colours", () => {
    const history = [year(2024, 100, 10), year(2025, 200, 50)];
    const s = modelSeries(history, project(history, [{ growth: 10, margin: 20 }]));
    expect(s.map((x) => [x.name, x.color, x.dashed ?? false])).toEqual([
      ["Revenue", 0, false],
      ["Revenue (forecast)", 0, true],
      ["Operating income", 1, false],
      ["Operating income (forecast)", 1, true],
    ]);
    expect(s[1]!.points.map((p) => [p.key, p.period, p.y])).toEqual([
      [2025, "FY2025", 200],
      [2026, "FY2026E", 220.00000000000003],
    ]);
    expect(s[1]!.points.every((p) => p.sources.length === 0)).toBe(true);
  });

  it("leaves out a line with no values (no operating income reported)", () => {
    const history = [year(2024, 100), year(2025, 200)];
    const names = modelSeries(history, []).map((x) => x.name);
    expect(names).toEqual(["Revenue", "Revenue (forecast)"]);
  });
});

describe("assumptions", () => {
  it("defaults to the historical CAGR and average margin", () => {
    const s = historyStats([year(2024, 100, 10), year(2025, 108.14, 21.628)]);
    expect(defaultAssumptions(s, 2)).toEqual([
      { growth: 8.1, margin: 15 },
      { growth: 8.1, margin: 15 },
    ]);
    expect(defaultAssumptions(historyStats([]), 1)).toEqual([{ growth: 0, margin: 0 }]);
    expect(defaultAssumptions(historyStats([]), 50)).toHaveLength(10);
  });

  it("resizes by repeating the last year (without its note) or cutting", () => {
    const list = [{ growth: 5, margin: 10, note: "why" }];
    expect(resizeAssumptions(list, 3)).toEqual([list[0], { growth: 5, margin: 10 }, { growth: 5, margin: 10 }]);
    expect(resizeAssumptions(resizeAssumptions(list, 3), 1)).toEqual(list);
    expect(resizeAssumptions(list, 99)).toHaveLength(10);
    expect(resizeAssumptions([], 0)).toEqual([{ growth: 0, margin: 0 }]);
  });

  it("parses typed percentages within range", () => {
    expect(parsePercent(" 12.5 ", [-100, 1000])).toBe(12.5);
    expect(parsePercent("-3%", [-100, 1000])).toBe(-3);
    expect(parsePercent("", [-100, 1000])).toBeNull();
    expect(parsePercent("1e3", [-100, 1000])).toBeNull();
    expect(parsePercent("-101", [-100, 1000])).toBeNull();
    expect(parsePercent("101", [-1000, 100])).toBeNull();
  });
});

describe("restoreModel / modelState", () => {
  it("restores a saved state and drops what doesn't fit", () => {
    expect(restoreModel({ companyId: "NVDA" })).toBeNull();
    expect(restoreModel(null)).toBeNull();
    const m = restoreModel({
      companyId: NVDA,
      historyYears: 40,
      assumptions: [{ growth: 5000, margin: 30, note: "  big  " }, { growth: "x", margin: 1 }, { growth: 1, margin: 2 }],
    })!;
    expect(m).toEqual({ companyId: NVDA, historyYears: 10, assumptions: [{ growth: 1000, margin: 30, note: "big" }, { growth: 1, margin: 2 }] });
    expect(restoreModel({ companyId: NVDA })!).toEqual({ companyId: NVDA, historyYears: 8, assumptions: [{ growth: 0, margin: 0 }] });
  });

  it("saves rounded numbers and no blank notes", () => {
    expect(modelState({ companyId: NVDA, historyYears: 8, assumptions: [{ growth: 1.23456, margin: 2, note: "  " }, { growth: 3, margin: 4, note: " ok " }] })).toEqual({
      companyId: NVDA,
      historyYears: 8,
      assumptions: [{ growth: 1.23, margin: 2 }, { growth: 3, margin: 4, note: "ok" }],
    });
  });
});

/** The same inputs and numbers as services/agent tests/test_model_stats.py: the thesis check
 * (Python) and the model page (here) must compute identical statistics and flags. */
describe("checkAssumptions matches the agent's Python port (stats-fixture.json)", () => {
  const close = (a: number | null, b: number | null) => {
    if (a === null || b === null) expect(a).toBe(b);
    else expect(a).toBeCloseTo(b, 12);
  };
  for (const c of fixture.cases) {
    it(c.name, () => {
      const history = annualHistory(c.rows as unknown as FinancialRow[], 10);
      expect(history.map((y) => ({ fy: y.fy, periodEnd: y.periodEnd, revenue: y.revenue, operatingIncome: y.operatingIncome }))).toEqual(c.expected.history);
      const r = checkAssumptions(history, c.assumptions);
      const e = c.expected;
      expect(r.baseFy).toBe(e.base_fy);
      close(r.stats.cagr, e.stats.cagr);
      expect(r.stats.cagrYears).toBe(e.stats.cagr_years);
      close(r.stats.avgMargin, e.stats.avg_margin);
      expect(r.stats.marginYears).toBe(e.stats.margin_years);
      close(r.stats.lastGrowth, e.stats.last_growth);
      close(r.stats.lastMargin, e.stats.last_margin);
      close(r.marginMin, e.margin_min);
      close(r.marginMax, e.margin_max);
      expect(r.years.length).toBe(e.years.length);
      r.years.forEach((y, i) => {
        const x = e.years[i]!;
        expect([y.fy, y.growth, y.margin, y.growthFlag, y.marginFlag]).toEqual([x.fy, x.growth, x.margin, x.growth_flag, x.margin_flag]);
        close(y.growthVsCagr, x.growth_vs_cagr);
      });
    });
  }

  it("flags growth more than GROWTH_FLAG_POINTS from the CAGR and margins outside the 5-year range", () => {
    const h = [year(2020, 100, 10), year(2021, 110, 22), year(2022, 121, 24.2), year(2023, 133.1, 13.31), year(2024, 146.41, 29.282), year(2025, 161.051, 16.1051)];
    const r = checkAssumptions(h, [
      { growth: 10 + GROWTH_FLAG_POINTS + 0.5, margin: 20 },
      { growth: 10 - GROWTH_FLAG_POINTS - 0.5, margin: 10 },
      { growth: 15, margin: 25 },
    ]);
    expect(r.years.map((y) => [y.fy, y.growthFlag, y.marginFlag])).toEqual([
      [2026, "above", null],
      [2027, "below", null],
      [2028, null, "above"],
    ]);
  });
});
