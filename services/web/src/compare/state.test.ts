import { describe, expect, it } from "vitest";

import { entry, NVDA, TESLA, TSLA } from "../fixtures";
import type { ScreenerRow } from "../types";
import {
  calendarLabel,
  chartMetric,
  cleanCompare,
  compareCsv,
  compareParams,
  compareQuery,
  compareRows,
  DEFAULT_COLUMNS,
  fiscalLabel,
  median,
  medianTitle,
  parseCompare,
  peerStats,
} from "./state";

const AAPL = "0000320193";
const KO = "0000021344";
const row = (e: ReturnType<typeof entry>, values: Record<string, string | null> = {}): ScreenerRow => ({
  ...e,
  filer_categories: null,
  state_of_incorporation: null,
  values: { ...e.values, ...values },
});
const SRC = (end: string) => ({ period_end: end, accession_no: "0000320193-26-000010", form: "10-Q", filed: "2026-08-01" });
const APPLE = (margin: string): ScreenerRow => ({
  ...row(entry({ company_id: AAPL, name: "Apple", tickers: ["AAPL"], fiscal_year_end: "0927" })),
  values: { revenue_ttm: "400000000000", operating_margin_ttm: margin, revenue_q: "94000000000" },
  sources: { revenue_ttm: SRC("2026-06-27"), operating_margin_ttm: SRC("2026-06-27"), revenue_q: SRC("2026-06-27") },
});

describe("the URL", () => {
  it("round-trips and drops what it doesn't recognize", () => {
    const s = { target: NVDA, peers: [AAPL, TSLA], cols: ["operating_margin_ttm", "revenue_ttm"] };
    expect(parseCompare(compareQuery(s))).toEqual(s);
    expect(compareQuery(s)).toBe(`target=${NVDA}&peers=${AAPL},${TSLA}&cols=operating_margin_ttm,revenue_ttm`);
    expect(parseCompare("target=NVDA&peers=../x,0000320193,0000320193&cols=salary_ttm")).toEqual({ peers: [AAPL] });
    expect(compareQuery({})).toBe("");
  });

  it("keeps the target out of the peers, caps them at 20, and leaves default columns unset", () => {
    const peers = Array.from({ length: 25 }, (_, i) => String(i + 1).padStart(10, "0"));
    const c = cleanCompare({ target: NVDA, peers: [NVDA, ...peers], cols: DEFAULT_COLUMNS });
    expect(c.peers).toHaveLength(20);
    expect(c.peers).not.toContain(NVDA);
    expect(c.cols).toBeUndefined();
  });

  it("asks the screener for exactly these companies", () => {
    expect(compareParams({ target: NVDA, peers: [AAPL], cols: ["revenue_ttm"] })).toEqual({
      company_id: [NVDA, AAPL],
      columns: ["revenue_ttm", "revenue_q"],
      sort: "name",
      limit: 2,
    });
  });
});

describe("period labels", () => {
  it("match gold's calendar quarter (period end - 35 days)", () => {
    expect(calendarLabel("2026-07-26")).toBe("CY2026 Q2");
    expect(calendarLabel("2025-12-31")).toBe("CY2025 Q4");
    expect(calendarLabel("2026-01-03")).toBe("CY2025 Q4");
  });

  it("match gold's fiscal quarter, 52/53-week years included", () => {
    expect(fiscalLabel("2026-07-26", "0126")).toBe("FY2027 Q2"); // Nvidia
    expect(fiscalLabel("2026-01-25", "0126")).toBe("FY2026 Q4");
    expect(fiscalLabel("2026-06-27", "0927")).toBe("FY2026 Q3"); // Apple
    expect(fiscalLabel("2025-12-31", "1231")).toBe("FY2025 Q4");
    expect(fiscalLabel("2026-01-03", "1231")).toBe("FY2025 Q4"); // ends in the first week of January
    expect(fiscalLabel("2026-03-31", null)).toBeNull();
  });
});

describe("the table", () => {
  const s = { target: NVDA, peers: [TSLA, AAPL, KO], cols: ["revenue_ttm", "operating_margin_ttm"] };
  const rows = compareRows(s, [APPLE("0.3"), row(TESLA), row(entry(), { operating_margin_ttm: "0.6234" })]);

  it("keeps the comps order, labels the latest quarter, and cites every value", () => {
    expect(rows.map((r) => r.companyId)).toEqual([NVDA, TSLA, AAPL]); // KO unknown: left out
    expect(rows[0]).toMatchObject({ isTarget: true, readable: true, ticker: "NVDA" });
    expect(rows[0]!.latest).toMatchObject({ calendar: "CY2026 Q2", fiscal: "FY2027 Q2", form: "10-Q" });
    expect(rows[2]!.latest).toMatchObject({ calendar: "CY2026 Q2", fiscal: "FY2026 Q3" });
    expect(rows[0]!.cells.revenue_ttm).toMatchObject({ text: "$130.50B", raw: "130497000000" });
    expect(rows[0]!.cells.revenue_ttm!.title).toContain("trailing twelve months to Jul 26, 2026 (CY2026 Q2)");
    expect(rows[0]!.cells.revenue_ttm!.title).toContain("10-Q filed Aug 26, 2026");
  });

  it("shows ungranted companies with metadata only and leaves them out of the median", () => {
    expect(rows[1]).toMatchObject({ readable: false, latest: null, name: "Tesla" });
    expect(rows[1]!.cells.revenue_ttm).toMatchObject({ value: null, text: "—" });
    expect(rows[1]!.cells.revenue_ttm!.title).toContain("doesn't cover");
    const stats = peerStats(rows, s.cols);
    // peers: TSLA (no values) and AAPL; NVDA is the target and doesn't count
    expect(stats.operating_margin_ttm).toEqual({ median: 0.3, n: 1, high: 0.3, low: 0.3, banks: 0 });
  });

  it("medians of even and odd counts", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([])).toBeNull();
  });

  it("exports exact values with their periods and sources, and the peer median", () => {
    const csv = compareCsv(s, rows).split("\r\n");
    expect(csv[0]).toBe(
      "role,company_id,ticker,company,access,latest_quarter_calendar,latest_quarter_fiscal,latest_period_end," +
        "revenue_ttm,revenue_ttm_period_end,revenue_ttm_source_accession," +
        "operating_margin_ttm,operating_margin_ttm_period_end,operating_margin_ttm_source_accession",
    );
    expect(csv[1]).toContain(`target,${NVDA},NVDA,Nvidia,financials,CY2026 Q2,FY2027 Q2,2026-07-26,130497000000,2026-07-26,`);
    expect(csv[2]).toBe(`peer,${TSLA},TSLA,Tesla,metadata only,,,,,,,,,`);
    expect(csv[4]).toBe("peer median (excludes target),,,,,,,,400000000000,,1 peers,0.3,,1 peers");
  });

  it("guards the CSV against spreadsheet formulas in names", () => {
    const evil = compareRows({ target: NVDA }, [row(entry({ name: "=HYPERLINK(\"x\")" }))]);
    expect(compareCsv({ target: NVDA }, evil)).toContain(`"'=HYPERLINK(""x"")"`);
  });
});

it("charts a column as its metric in the chart builder", () => {
  expect(chartMetric("operating_margin_ttm")).toEqual({ metric: "operating_margin", transform: "ttm", title: "Operating margin, TTM" });
  expect(chartMetric("net_debt_q")).toEqual({ metric: "net_debt", transform: "q", title: "Net debt, quarter end" });
  expect(chartMetric("revenue_growth_ttm")).toMatchObject({ metric: "revenue", transform: "yoy" });
  expect(chartMetric("salary_ttm")).toBeNull();
});

describe("banks", () => {
  const JPM = "0000019617";
  const BANK = (over: Record<string, string | null>): ScreenerRow => ({
    ...row(entry({ company_id: JPM, name: "JPMorgan Chase", tickers: ["JPM"], industry: "National Commercial Banks" })),
    values: { revenue_ttm: "180000000000", operating_margin_ttm: null, net_debt_q: null, ...over },
  });
  const s = { target: NVDA, peers: [AAPL, JPM], cols: ["revenue_ttm", "operating_margin_ttm", "net_debt_q"] };

  it("show n/a for operating margin and net debt, and stay out of those medians", () => {
    const rows = compareRows(s, [row(entry()), APPLE("0.3"), BANK({})]);
    const jpm = rows.find((r) => r.companyId === JPM)!;
    expect(jpm.cells.operating_margin_ttm).toMatchObject({ text: "n/a (bank)", bank: true });
    expect(jpm.cells.operating_margin_ttm!.title).toContain("doesn't describe a bank");
    expect(jpm.cells.revenue_ttm!.bank).toBeUndefined();
    const stats = peerStats(rows, s.cols);
    expect(stats.operating_margin_ttm).toMatchObject({ median: 0.3, n: 1, banks: 1 });
    expect(stats.revenue_ttm).toMatchObject({ n: 2, banks: 0 });
    expect(medianTitle(stats.operating_margin_ttm!)).toContain("1 bank left out");
  });

  it("leave a bank's value out of the median even when the API has one", () => {
    const rows = compareRows(s, [row(entry()), APPLE("0.3"), BANK({ operating_margin_ttm: "0.9" })]);
    expect(rows[2]!.cells.operating_margin_ttm).toMatchObject({ text: "90.0%", bank: true });
    expect(peerStats(rows, s.cols).operating_margin_ttm!.median).toBe(0.3);
  });
});
