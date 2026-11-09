import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { ACC, fin, NVDA, nvdaRow } from "../fixtures";
import type { Recap, RecapValue, SegmentRow } from "../types";
import {
  eightKItems,
  figuresBehind,
  FINANCE_ARMS,
  financeArmNote,
  filingHeadings,
  fiscalYear,
  parsedNote,
  financialsCsv,
  formMatches,
  header,
  keyStats,
  metricTable,
  RATIOS,
  recapAccession,
  recapRows,
  segmentTables,
  STATEMENTS,
  listingLine,
  withAnnualPerShare,
  withBridges,
} from "./data";

describe("the header's listing line", () => {
  it("names the main ticker and counts the rest, whose tickers go in the hover", () => {
    expect(listingLine(["JPM", "JPM-PC", "JPM-PD", "VYLD"], ["NYSE"])).toEqual({
      text: "JPM +3 more · NYSE",
      more: "Also listed: JPM-PC, JPM-PD, VYLD",
    });
    expect(listingLine(["NVDA"], ["Nasdaq"])).toEqual({ text: "NVDA · Nasdaq", more: null });
    expect(listingLine([], [])).toEqual({ text: "", more: null });
  });
});

const SRC = { period_end: "2026-07-26", accession_no: ACC, form: "10-Q", filed: "2026-08-26" };

describe("header and key stats", () => {
  it("label every value with its period and source", () => {
    const stats = keyStats(nvdaRow());
    const by = (l: string) => stats.find((s) => s.label === l)!;
    expect(by("Revenue")).toMatchObject({ text: "$165.22B", period: "TTM to Jul 26, 2026" });
    expect(by("Revenue")!.title).toContain(`Source: 10-Q filed Aug 26, 2026, ${ACC}`);
    expect(by("Revenue growth")).toMatchObject({ text: "+69.0%", period: "TTM vs prior TTM, to Jul 26, 2026" });
    expect(by("EPS (diluted)")).toMatchObject({ text: "$1.08", period: "quarter to Jul 26, 2026" });
    expect(by("Cash")).toMatchObject({ period: "at Jul 26, 2026" });
    expect(by("Return on equity")).toMatchObject({ text: "—", period: "not reported" });
  });

  it("names the company from public metadata", () => {
    expect(header(nvdaRow())).toEqual({
      name: "Nvidia", tickers: ["NVDA"], exchanges: ["Nasdaq"], industry: "Semiconductors & Related Devices",
      sic: "3674", fye: "January", filer: "Large accelerated", state: "DE", currency: null,
    });
  });

  it("recaps only a 10-Q or 10-K", () => {
    expect(recapAccession(nvdaRow())).toBe(ACC);
    expect(recapAccession(nvdaRow({ sources: { revenue_q: { ...SRC, form: "8-K" } } }))).toBeNull();
    expect(recapAccession(nvdaRow({ sources: {} }))).toBeNull();
  });
});

describe("a company reporting in euros (migration 056)", () => {
  const eur = (metric: string, end: string, value: string, fiscal: string) =>
    ({ ...fin(metric, end, value, fiscal), unit: metric === "eps_diluted" ? "EUR/shares" : "EUR" });

  it("its statements read in euros and say so", () => {
    const t = metricTable([eur("revenue", "2025-09-30", "2097429000", "FY2025 Q4"), eur("eps_diluted", "2025-09-30", "1.87", "FY2025 Q4")],
      STATEMENTS.is.rows, "q");
    expect(t.currency).toBe("EUR");
    expect(t.rows[0]!.cells[0]).toMatchObject({ text: "€2.10B" });
    expect(t.rows[1]!.cells[0]).toMatchObject({ text: "€1.87" });
    expect(metricTable([fin("revenue", "2026-07-26", "46743000000", "FY2027 Q2")], STATEMENTS.is.rows, "q").currency).toBeUndefined();
  });

  it("its header and key stats name the currency", () => {
    const row = nvdaRow({ values: { revenue_ttm: "2097429000" }, readings: { revenue_ttm: { status: "other_currency", unit: "EUR", reason: "reported in EUR" } } });
    expect(header(row).currency).toBe("EUR");
    expect(keyStats(row).find((s) => s.key === "revenue_ttm")!.text).toBe("€2.10B");
    expect(header(nvdaRow()).currency).toBeNull();
  });
});

describe("statements", () => {
  const rows = [
    fin("revenue", "2026-04-26", "44062000000", "FY2027 Q1"),
    fin("revenue", "2026-07-26", "46743000000", "FY2027 Q2"),
    fin("revenue", "2026-01-25", "39331000000", "FY2026 Q4", { value_source: "derived", citation: { ...fin("x", "", "", "").citation, source_accessions: ["a", "b"] } }),
    fin("eps_diluted", "2026-07-26", "1.08", "FY2027 Q2"),
  ];

  it("make a metric × period table, oldest first, with sources and derived marks", () => {
    const t = metricTable(rows, STATEMENTS.is.rows, "q");
    expect(t.columns.map((c) => c.label)).toEqual(["FY2026 Q4", "FY2027 Q1", "FY2027 Q2"]);
    expect(t.rows.map((r) => r.metric)).toEqual(["revenue", "eps_diluted"]); // empty rows dropped
    expect(t.rows[0]!.cells[0]).toMatchObject({ text: "$39.33B", derived: true });
    expect(t.rows[0]!.cells[0]!.title).toContain("derived (from a, b)");
    expect(t.rows[0]!.cells[2]!.title).toContain(`Source: 10-Q filed Aug 26, 2026, ${ACC}; reported, Revenues`);
    expect(t.rows[1]!.cells).toEqual([null, null, expect.objectContaining({ text: "$1.08" })]);
  });

  it("annual columns are the fiscal Q4s; TTM columns say TTM", () => {
    expect(metricTable(rows, STATEMENTS.is.rows, "a").columns.map((c) => c.label)).toEqual(["FY2026"]);
    expect(metricTable(rows, STATEMENTS.is.rows, "ttm").columns[0]!.label).toBe("TTM FY2026 Q4");
  });

  it("labels calendar columns CY, and annual calendar columns are the calendar Q4s", () => {
    const cal = [
      fin("revenue", "2025-10-26", "35082000000", "FY2026 Q3", { calendar_period: "CY2025 Q4" }),
      fin("revenue", "2026-01-25", "39331000000", "FY2026 Q4", { calendar_period: "CY2026 Q1" }),
    ];
    expect(metricTable(cal, STATEMENTS.is.rows, "q", "calendar").columns.map((c) => c.label)).toEqual(["CY2025 Q4", "CY2026 Q1"]);
    expect(metricTable(cal, STATEMENTS.is.rows, "a", "calendar").columns.map((c) => c.label)).toEqual(["CY2025"]);
    expect(metricTable(cal, STATEMENTS.is.rows, "a").columns.map((c) => c.label)).toEqual(["FY2026"]);
  });

  it("exports the table's values with their sources", () => {
    const t = metricTable(rows, STATEMENTS.is.rows, "q");
    const csv = financialsCsv(rows, t, "quarter").split("\r\n");
    expect(csv[0]).toBe("metric,view,fiscal_period,calendar_period,period_start,period_end,value,unit,value_source,accession_no,form,filed,xbrl_tag,source_accessions");
    expect(csv[1]).toBe(`eps_diluted,quarter,FY2027 Q2,,,2026-07-26,1.08,USD/shares,reported,${ACC},10-Q,2026-08-26,Revenues,`);
    expect(csv[2]).toBe(`revenue,quarter,FY2026 Q4,,,2026-01-25,39331000000,USD,derived,${ACC},10-Q,2026-08-26,Revenues,a b`);
    expect(csv).toHaveLength(6); // header, 4 values, trailing newline
  });
});

describe("recap", () => {
  const v = (value: string, fiscal: string, over: Partial<RecapValue> = {}): RecapValue => ({
    value, period_start: null, period_end: "2026-07-26", fiscal_period: fiscal, calendar_period: null,
    value_source: "reported",
    citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", xbrl_tag: null, source_accessions: null, filing_url: null },
    freshness: { filed: "2026-08-26", ingested_at: "" },
    ...over,
  });
  const recap: Recap = {
    accession_no: ACC, company_id: NVDA, form: "10-Q", filed: "2026-08-26", period_end: "2026-07-26", as_of: "", note: "",
    flags: null, flags_unavailable: "needs filings",
    metrics: [
      { metric: "operating_margin", span: "quarter", unit: "ratio", current: v("0.6", "FY2027 Q2"),
        prior_quarter: v("0.62", "FY2027 Q1", { change: "-0.02" }), year_ago: v("0.58", "FY2026 Q2", { change: "0.02" }) },
      { metric: "revenue", span: "quarter", unit: "USD", current: v("46743000000", "FY2027 Q2"),
        prior_quarter: v("44062000000", "FY2027 Q1", { change: "2681000000", pct_change: "0.060846" }),
        year_ago: v("30040000000", "FY2026 Q2", { change: "16703000000", pct_change: "0.556026" }) },
      { metric: "revenue", span: "ttm", unit: "USD", current: v("1", "x"), prior_quarter: null, year_ago: null },
    ],
  };

  it("shows quarters vs prior and year-ago: growth in percent, margins in basis points", () => {
    const { labels, rows } = recapRows(recap);
    expect(labels).toEqual(["FY2027 Q2", "FY2027 Q1", "FY2026 Q2"]);
    expect(rows.map((r) => r.label)).toEqual(["Revenue", "Operating margin"]); // TTM left out, fixed order
    expect(rows[0]).toMatchObject({ current: "$46.74B", prior: "$44.06B", qoq: { text: "+6.1%", sign: 1 }, yoy: { text: "+55.6%" } });
    expect(rows[1]).toMatchObject({ current: "60.0%", qoq: { text: "-200 bp", sign: -1 }, yoy: { text: "+200 bp" } });
    expect(rows[0]!.title).toContain(`FY2027 Q1: 10-Q filed Aug 26, 2026, ${ACC}`);
  });

  it("a margin that isn't meaningful reads n/m, moves no delta, and keeps its figure in the hover", () => {
    const margin = recap.metrics[0]!;
    const { rows } = recapRows({
      ...recap,
      metrics: [{ ...margin, current: { ...margin.current!, value: "20.9", not_meaningful: "the amount is more than ten times revenue" } }],
    });
    expect(rows[0]).toMatchObject({ current: "n/m", qoq: null, yoy: null });
    expect(rows[0]!.title).toContain("because the amount is more than ten times revenue");
    expect(rows[0]!.title).toContain("2090.0%");
  });
});

describe("segments", () => {
  const seg = (member: string, label: string, end: string, value: string, axis: SegmentRow["axis"] = "business_segment"): SegmentRow => ({
    axis, member, member_label: label, member_aliases: [], metric: "revenue", period_start: null, period_end: end,
    fiscal_period: null, calendar_period: null, value, unit: "USD", value_source: "reported",
    citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", xbrl_tag: "Revenues", xbrl_axis: "us-gaap:StatementBusinessSegmentsAxis",
      xbrl_member: member, source_accessions: null, filing_url: null },
    freshness: { filed: "", ingested_at: "" },
  });
  const ends = ["2025-07-27", "2025-10-26", "2026-01-25", "2026-04-26", "2026-07-26"];

  it("tables members by period with share and YoY, largest first, reconciliation left out", () => {
    const rows = [
      ...ends.map((e, i) => seg("nvda:GraphicsMember", "Graphics", e, String(4e9 + i * 1e8))),
      ...ends.map((e, i) => seg("nvda:ComputeMember", "Compute & Networking", e, String(2e10 + i * 2e9))),
      seg("us-gaap:CorporateMember", "Corporate", "2026-07-26", "-1000000", "reconciliation"),
      seg("country:US", "United States", "2026-07-26", "1000", "geography"),
    ];
    const [biz, geo] = segmentTables(rows);
    expect(biz!.title).toBe("Revenue by business segments");
    expect(biz!.columns.map((c) => c.label)).toEqual(["CY2025 Q2", "CY2025 Q3", "CY2025 Q4", "CY2026 Q1", "CY2026 Q2"]);
    expect(biz!.rows.map((r) => r.label)).toEqual(["Compute & Networking", "Graphics"]);
    expect(biz!.rows[0]!.share).toBeCloseTo(2.8e10 / 3.24e10);
    expect(biz!.rows[0]!.yoy).toBeCloseTo(0.4);
    expect(geo!.title).toBe("Revenue by geography");
    expect(segmentTables([])).toEqual([]);
  });

  // M8: Deckers reorganised in 2025 (UGG Wholesale and Direct to Consumer became UGG Brand) and has
  // reported gross profit, not operating income, by segment since; production rows, 2026-11-08
  const deck = (member: string, label: string, metric: string, end: string, value: number): SegmentRow =>
    ({ ...seg(member, label, end, String(value * 1e6)), metric });
  const QUARTERS = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"];
  const DECK = [
    ...["2023-09-30", "2023-12-31", "2024-09-30", "2024-12-31"].flatMap((e, i) => [
      deck("deck:UGGWholesaleSegmentMember", "UGG Wholesale", "revenue", e, [512, 467, 512, 467][i]!),
      deck("deck:UGGWholesaleSegmentMember", "UGG Wholesale", "operating_income", e, [180, 170, 195, 190][i]!),
      deck("deck:DirectToConsumerSegmentMember", "Direct to Consumer", "operating_income", e, [130, 401, 143, 481][i]!),
    ]),
    deck("deck:SanukWholesaleSegmentMember", "Sanuk Wholesale", "revenue", "2024-09-30", 1),
    ...QUARTERS.map((e, i) => deck("deck:UGGBrandSegmentMember", "UGG Brand", "revenue", e, [1244, 374, 265, 759, 1305, 408, 278, 786][i]!)),
    ...QUARTERS.map((e, i) => deck("deck:UGGBrandSegmentMember", "UGG Brand", "gross_profit", e, [783, 226, 139, 421, 808, 253, 152, 480][i]!)),
  ];

  it("M8: a table that ended long ago says it isn't current, and YoY finds the year-ago quarter by date", () => {
    const tables = segmentTables(DECK);
    expect(tables.map((t) => t.title)).toEqual([
      "Revenue by business segments", "Gross profit by business segments", "Operating income by business segments",
    ]);
    const oi = tables[2]!;
    expect(oi.staleNote).toContain("Not current: the last operating income by business segments is for CY2024 Q4");
    expect(oi.staleNote).toContain("to Sep 30, 2026");
    // 2024-12-31 against 2023-12-31, with no quarter in between on file
    expect(oi.rows.find((r) => r.label === "UGG Wholesale")!.yoy).toBeCloseTo(190 / 170 - 1);
    expect(oi.rows.find((r) => r.label === "Direct to Consumer")!.yoy).toBeCloseTo(481 / 401 - 1);
    expect(tables[0]!.staleNote).toBeNull();
    expect(tables[1]!.staleNote).toBeNull();
  });

  it("M8: members that stopped read 'until' after the current ones; members out of the window aren't empty rows", () => {
    const [rev] = segmentTables(DECK);
    expect(rev!.rows.map((r) => [r.label, r.until])).toEqual([["UGG Brand", null], ["UGG Wholesale", "CY2024 Q4"]]);
    expect(rev!.rows.some((r) => r.label === "Sanuk Wholesale")).toBe(false); // only before the eight quarters shown
    expect(rev!.retiredNote).toContain("UGG Wholesale was last reported for CY2024 Q4");
    expect(rev!.rows[0]!.yoy).toBeCloseTo(786 / 759 - 1);
  });

  it("M8: a change from a year-ago loss isn't a growth rate (Teva Wholesale's -491.9%)", () => {
    const [t] = segmentTables([
      deck("deck:Other", "Other Wholesale", "operating_income", "2023-12-31", -1.36),
      deck("deck:Other", "Other Wholesale", "operating_income", "2024-12-31", 5.35),
    ]);
    expect(t!.rows[0]).toMatchObject({ yoy: null, yoyNotMeaningful: true });
  });
});

describe("figures behind the filing index (H9)", () => {
  // Humana, as the audit found it: revenue to Mar 31, 2026 while EDGAR had the June 10-Q of Jul 29
  const rows = [
    fin("revenue", "2025-12-31", "32515000000", "FY2025 Q4", { citation: { accession_no: "0000049071-26-000009", form: "10-K", filed: "2026-02-18", xbrl_tag: "Revenues", source_accessions: null, filing_url: null } }),
    fin("revenue", "2026-03-31", "39648000000", "FY2026 Q1", { citation: { accession_no: "0000049071-26-000023", form: "10-Q", filed: "2026-04-29", xbrl_tag: "Revenues", source_accessions: null, filing_url: null } }),
  ];
  const june = { form: "10-Q", accession_no: "0000049071-26-000050", filed: "2026-07-29", period_of_report: "2026-06-30" };
  const NOW = Date.parse("2026-11-06T12:00:00Z");

  it("says how far behind: the filing's period, when it was filed and how many days ago", () => {
    expect(figuresBehind(rows, june, NOW)).toEqual({
      form: "10-Q", accessionNo: june.accession_no, filed: "2026-07-29", period: "2026-06-30", through: "2026-03-31", days: 100, quarters: 1,
    });
  });

  it("isn't behind when the figures hold that period or cite that filing, or for an older or other filing", () => {
    expect(figuresBehind([...rows, fin("revenue", "2026-06-30", "40867000000", "FY2026 Q2")], june, NOW)).toBeNull();
    expect(figuresBehind(rows, { ...june, period_of_report: "2026-04-02" }, NOW)).toBeNull(); // within a week
    expect(figuresBehind(rows, { ...june, form: "8-K" }, NOW)).toBeNull();
    expect(figuresBehind(rows, { ...june, period_of_report: null }, NOW)).toBeNull();
    expect(figuresBehind(rows, { ...june, filed: "2026-04-01" }, NOW)).toBeNull(); // filed before what's cited
    expect(figuresBehind(rows, null, NOW)).toBeNull();
    expect(figuresBehind([], june, NOW)).toBeNull();
  });
});

it("labels 8-K items and filters forms", () => {
  expect(eightKItems(["2.02", "9.01", "EX-99.1", "5.02"])).toEqual(["2.02 Results of operations", "5.02 Officer or director change"]);
  expect(formMatches("10-K/A", "10-K")).toBe(true);
  expect(formMatches("10-Q", "10-K")).toBe(false);
  expect(formMatches("8-K", "all")).toBe(true);
});

describe("banks on the tearsheet", () => {
  const BANK_METRICS = ["gross_margin", "operating_margin", "free_cash_flow", "fcf_margin", "net_debt", "debt_to_equity", "current_ratio"];
  it("key stats and ratio rows read n/a for what doesn't describe a bank, by the API's rule", () => {
    const reason = "this is a bank: deposits are its debt and lending its operations";
    const bank = nvdaRow({
      values: { revenue_ttm: "180000000000" },
      readings: { operating_margin_ttm: { status: "not_applicable", reason }, net_debt_q: { status: "not_applicable", reason } },
    });
    const stats = keyStats(bank);
    expect(stats.find((s) => s.label === "Operating margin")).toMatchObject({ text: "n/a (bank)", period: "not applicable" });
    expect(stats.find((s) => s.label === "Net debt")!.title).toContain("This is a bank");
    const rows = [fin("roe", "2026-07-26", "0.17", "FY2026 Q2"), fin("fcf_margin", "2026-07-26", "1.56", "FY2026 Q2")];
    const t = metricTable(rows, RATIOS, "ttm", "fiscal", BANK_METRICS);
    expect(t.rows.map((r) => r.metric)).toEqual(["gross_margin", "operating_margin", "fcf_margin", "roe", "debt_to_equity", "current_ratio"]);
    // a figure on file doesn't change what it reads: it moves to the hover
    const fcf = t.rows.find((r) => r.metric === "fcf_margin")!;
    expect(fcf.cells[0]).toMatchObject({ text: "n/a (bank)", url: "https://www.sec.gov/x/" });
    expect(fcf.cells[0]!.title).toContain("156.0%");
    expect(t.rows[0]).toMatchObject({ cells: [null], blanks: [{ text: "n/a (bank)" }] });
    // not a bank: empty rows are dropped as before
    expect(metricTable(rows, RATIOS, "ttm").rows.map((r) => r.metric)).toEqual(["fcf_margin", "roe"]);
  });
});

describe("what a blank means in a period table", () => {
  it("no data in this period is ·; a fiscal Q4's EPS is not reported, and says why", () => {
    const rows = [
      fin("revenue", "2025-10-26", "57", "FY2026 Q3"), fin("revenue", "2026-01-25", "68", "FY2026 Q4"),
      fin("eps_diluted", "2025-10-26", "1.3", "FY2026 Q3"),
      fin("cash", "2025-10-26", "0", "FY2026 Q3"),
    ];
    const t = metricTable(rows, [["revenue", "Revenue", "usd"], ["eps_diluted", "EPS (diluted)", "eps"], ["cash", "Cash", "usd"]], "q");
    const eps = t.rows[1]!;
    expect(eps.cells[1]).toBeNull();
    expect(eps.blanks[1]).toMatchObject({ text: "—" });
    expect(eps.blanks[1]!.title).toContain("year less nine months isn't the quarter");
    const cash = t.rows[2]!;
    expect(cash.blanks[1]).toMatchObject({ text: "·" });
    // a real zero is a figure
    expect(cash.cells[0]!.text).toBe("$0");
  });

  it("TTM keeps the per-share rows, as n/a, so they don't just vanish", () => {
    const rows = [fin("revenue", "2026-07-26", "302", "FY2027 Q2")];
    const t = metricTable(rows, [["revenue", "Revenue", "usd"], ["eps_diluted", "EPS (diluted)", "eps"]], "ttm");
    expect(t.rows.map((r) => r.metric)).toEqual(["revenue", "eps_diluted"]);
    expect(t.rows[1]!.blanks[0]).toMatchObject({ text: "n/a" });
    expect(t.rows[1]!.blanks[0]!.title).toContain("don't add across quarters");
  });

  it("Annual takes EPS from the 10-K, in the column of the year it ends, a day's difference allowed", () => {
    const ttm = [fin("revenue", "2026-01-25", "216", "FY2026 Q4"), fin("revenue", "2025-01-26", "130", "FY2025 Q4")];
    const annual = [
      fin("eps_diluted", "2026-01-26", "4.90", "", { fiscal_period: null }),
      fin("eps_diluted", "2019-01-27", "6.63", "", { fiscal_period: null }), // no such column
    ];
    const t = metricTable(withAnnualPerShare(ttm, annual, "fiscal"), [["revenue", "Revenue", "usd"], ["eps_diluted", "EPS (diluted)", "eps"]], "a");
    expect(t.columns.map((c) => c.label)).toEqual(["FY2025", "FY2026"]);
    expect(t.rows[1]!.cells.map((c) => c?.text ?? null)).toEqual([null, "$4.90"]);
  });

  it("a ratio on negative equity reads n/m, not as a gap, and a ratio the API calls n/m keeps its figure in the hover", () => {
    const rows = [
      { ...fin("debt_to_equity", "2026-04-26", "563.3", "FY2027 Q1"), not_meaningful: "debt is over 100 times equity, so equity is close to zero" },
      fin("stockholders_equity", "2026-04-26", "6700000", "FY2027 Q1"),
      fin("stockholders_equity", "2026-07-26", "-5000000", "FY2027 Q2"),
      fin("gross_margin", "2026-07-26", "0.7", "FY2027 Q2"),
    ];
    // the API's not_meaningful_periods says so (migration 035); the table doesn't read equity itself
    const said = [{ metric: "debt_to_equity", period_end: "2026-07-26", reason: "stockholders' equity is negative" }];
    const t = metricTable(rows, RATIOS, "ttm", "fiscal", [], undefined, said);
    const de = t.rows.find((r) => r.metric === "debt_to_equity")!;
    expect(de.cells[0]).toMatchObject({ text: "n/m" });
    expect(de.cells[0]!.title).toContain("over 100 times equity");
    expect(de.cells[0]!.title).toContain("563.30x");
    expect(de.cells[1]).toMatchObject({ text: "n/m" });
    expect(de.cells[1]!.title).toContain("equity is negative at Jul 26, 2026");
    // an older API, which doesn't say: a gap, as any other, never a rule re-derived here
    const older = metricTable(rows, RATIOS, "ttm").rows.find((r) => r.metric === "debt_to_equity")!;
    expect(older.cells[1]).toBeNull();
  });
});

describe("segment shares", () => {
  // NVIDIA's FY2027 Q2, as filed: Data Center is Hyperscale + AI Clouds, and with Edge is revenue
  const row = (member: string, label: string, value: string): SegmentRow => ({
    axis: "product_service", member, member_label: label, member_aliases: [], metric: "revenue", period_start: null,
    period_end: "2026-07-26", fiscal_period: null, calendar_period: null, value, unit: "USD", value_source: "reported",
    citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", xbrl_tag: "Revenues", xbrl_axis: "srt:ProductOrServiceAxis",
      xbrl_member: member, source_accessions: null, filing_url: null },
    freshness: { filed: "", ingested_at: "" },
  });
  const NVDA_ROWS = [
    row("nvda:DataCenterMember", "Data Center", "89023000000"),
    row("nvda:HyperscaleMember", "Hyperscale", "48710000000"),
    row("nvda:AICloudsMember", "AI Clouds", "40313000000"),
    row("nvda:EdgeComputingMember", "Edge Computing", "7198000000"),
  ];
  const REVENUE = [fin("revenue", "2026-07-26", "96221000000", "FY2027 Q2")];

  it("are of consolidated revenue, so a parent beside its children reads its true share", () => {
    const [t] = segmentTables(NVDA_ROWS, REVENUE);
    expect(t!.shareOf).toBe("consolidated");
    expect(t!.rows.find((r) => r.label === "Data Center")!.share).toBeCloseTo(0.9252, 3);
    expect(t!.rows.find((r) => r.label === "Hyperscale")!.share).toBeCloseTo(0.5062, 3);
    expect(t!.overlapNote).toContain("Hyperscale, AI Clouds are part of another line");
    expect(t!.rows.filter((r) => r.overlaps).map((r) => r.label)).toEqual(["Hyperscale", "AI Clouds"]);
  });

  it("two cuts of the same revenue: Vertiv's Products read 84.3%, and the larger cut is the one kept", () => {
    const [t] = segmentTables([
      row("vrt:Products", "Products", "3637100000"),
      row("vrt:ProductExSpares", "Product excluding Spares", "3593800000"),
      row("vrt:ServicesAndSpares", "Services and Spares", "719000000"),
      row("vrt:Services", "Services", "675700000"),
    ], [fin("revenue", "2026-07-26", "4312800000", "FY2026 Q3")]);
    expect(t!.rows.find((r) => r.label === "Products")!.share).toBeCloseTo(0.8433, 3);
    expect(t!.rows.filter((r) => r.overlaps).map((r) => r.label)).toEqual(["Product excluding Spares", "Services and Spares"]);
  });

  it("without revenue: of the members' sum if nothing shows they overlap, and none if something does", () => {
    const [plain] = segmentTables([row("x:A", "A", "75"), row("x:B", "B", "25")]);
    expect(plain!.shareOf).toBe("members");
    expect(plain!.rows[0]!.share).toBeCloseTo(0.75);
  });

  it("#171: the filer's All Other is a last row, only in quarters where it and the segments make the total", () => {
    // NVIDIA's operating income as filed (production, 2026-11-09): in FY2025 Q3 the reconciling member is
    // All Other (22,081 + 1,502 - 1,714 = 21,869, the statement's figure); in FY2027 Q2 it carries the
    // segments' total (66,595) and doesn't tie, so it isn't shown as All Other
    const op = (axis: SegmentRow["axis"], member: string, label: string, end: string, value: string): SegmentRow => ({
      ...row(member, label, value), axis, metric: "operating_income", period_end: end,
    });
    const rows = [
      op("business_segment", "nvda:ComputeAndNetworkingSegmentMember", "Compute and Networking", "2024-10-27", "22081000000"),
      op("business_segment", "nvda:GraphicsSegmentMember", "Graphics", "2024-10-27", "1502000000"),
      op("reconciliation", "us-gaap:CorporateNonSegmentMember", "Corporate", "2024-10-27", "-1714000000"),
      op("business_segment", "nvda:ComputeAndNetworkingSegmentMember", "Compute and Networking", "2026-07-26", "62696000000"),
      op("business_segment", "nvda:GraphicsSegmentMember", "Graphics", "2026-07-26", "3899000000"),
      op("reconciliation", "us-gaap:CorporateNonSegmentMember", "Corporate", "2026-07-26", "66595000000"),
    ];
    const totals = [fin("operating_income", "2024-10-27", "21869000000", "FY2025 Q3"), fin("operating_income", "2026-07-26", "63734000000", "FY2027 Q2")];
    const [t] = segmentTables(rows, totals);
    expect(t!.rows.map((r) => r.label)).toEqual(["Compute and Networking", "Graphics", "All other"]);
    const other = t!.rows.at(-1)!;
    expect(other.reconciling).toBe(true);
    expect(other.cells.map((c) => c?.text ?? null)).toEqual(["-$1.71B", null]);
    expect(other.cells[0]!.title).toContain("reported outside the segments as Corporate");
    expect(other.share).toBeNull();
    // without the consolidated figure nothing can tie: no row
    expect(segmentTables(rows)[0]!.rows.map((r) => r.label)).toEqual(["Compute and Networking", "Graphics"]);
    // a reconciling member that's zero where it ties (NVIDIA's revenue) isn't a row either
    const rev = rows.map((r) => ({ ...r, metric: "revenue", value: r.axis === "reconciliation" ? "0" : r.value }));
    expect(segmentTables(rev, [fin("revenue", "2024-10-27", "23583000000", "FY2025 Q3")])[0]!.rows.some((r) => r.reconciling)).toBe(false);
  });

  it("a member bigger than reported revenue isn't a share: the two aren't on one basis", () => {
    const [t] = segmentTables([row("x:A", "A", "900000000"), row("x:B", "B", "20000000")], [fin("revenue", "2026-07-26", "114000000", "FY2026 Q3")]);
    expect(t!.rows[0]!.shareTooBig).toBe(true);
    expect(t!.rows[1]!.shareTooBig).toBe(false);
  });
});

describe("the Filings tab's headings and notes", () => {
  it("names a filing's fiscal year as gold does, 52/53-week ends included", () => {
    expect(fiscalYear("2026-07-26", "0126")).toBe(2027); // Nvidia's FY2027 Q2
    expect(fiscalYear("2026-01-25", "0126")).toBe(2026);
    expect(fiscalYear("2024-09-01", "0831")).toBe(2024); // Costco's year, a day past the nominal end
    expect(fiscalYear("2024-11-24", "0831")).toBe(2025);
    expect(fiscalYear("2026-12-31", "1231")).toBe(2026);
    expect(fiscalYear("2027-01-02", "1231")).toBe(2026); // a 53-week December year
  });

  it("heads a newest-first page with years that only move back", () => {
    const f = (form: string, filed: string, period_of_report: string | null = null) => ({ form, filed, period_of_report });
    const page = [
      f("10-Q", "2026-05-20", "2026-04-26"), // FY2027
      f("8-K", "2026-03-02", "2026-03-02"), // FY2027
      f("10-K", "2026-02-25", "2026-01-25"), // FY2026
      f("8-K", "2026-02-25", "2026-02-25"), // dated FY2027, but the page is past it: stays under FY2026
      f("4", "2026-01-30", "2020-01-01"), // not a reporting period: the date filed, FY2027, stays too
      f("10-Q", "2025-11-19", "2025-10-26"), // FY2026
      f("10-K", "2025-02-26", "2025-01-26"), // FY2025
    ];
    expect(filingHeadings(page, "0125")).toEqual(["FY2027", null, "FY2026", null, null, null, "FY2025"]);
    expect(filingHeadings(page.slice(5), null)).toEqual(["Filed in 2025", null]);
    expect(filingHeadings([], "0125")).toEqual([]);
  });

  it("says 'not parsed' only where a parsed filing was to be expected", () => {
    expect(parsedNote({ form: "4", sections: 0 })).toBeNull();
    expect(parsedNote({ form: "144", sections: null })).toBeNull();
    expect(parsedNote({ form: "10-Q", sections: 0 })).toBe("not parsed yet");
    expect(parsedNote({ form: "8-K", sections: null })).toBe("index only");
    expect(parsedNote({ form: "10-K", sections: 12 })).toBe("12 sections");
  });
});

describe("what a statement's own lines can't say", () => {
  const row = (metric: string, value: string, extra: Partial<ReturnType<typeof fin>> = {}) =>
    fin(metric, "2026-09-30", value, "FY2026 Q3", extra);
  const table = (rows: ReturnType<typeof fin>[], kind: "is" | "bs") =>
    withBridges(metricTable(rows, STATEMENTS[kind].rows, "q"), rows, kind);

  it("bridges operating to net income where no pretax line is tagged (Guerrilla RF)", () => {
    const t = table([row("operating_income", "2428257"), row("interest_expense", "186257"), row("net_income", "-3731159")], "is");
    const bridge = t.rows.find((r) => r.metric === "other_items")!;
    expect(bridge.label).toBe("Other items, net");
    expect(bridge.cells[0]).toMatchObject({ text: "-$6.16M", derived: true, url: null });
    expect(bridge.cells[0]!.title).toContain("where the filing tags no pretax income");
    expect(t.rows.map((r) => r.metric)).toEqual(["operating_income", "interest_expense", "other_items", "net_income"]);
  });

  it("adds no bridge where a pretax line says it all", () => {
    const t = table([row("operating_income", "100"), row("pretax_income", "90"), row("net_income", "70")], "is");
    expect(t.rows.some((r) => r.metric === "other_items")).toBe(false);
  });

  it("says a bank's interest expense is already out of revenue", () => {
    const tag = { citation: { ...fin("revenue", "2026-09-30", "1", "FY2026 Q3").citation, xbrl_tag: "us-gaap:RevenuesNetOfInterestExpense" } };
    const t = table([row("revenue", "50170000000", tag), row("interest_expense", "26020000000")], "is");
    expect(t.rows.find((r) => r.metric === "interest_expense")!.label).toBe("Interest expense (already taken out of revenue)");
    const sum = { citation: { ...tag.citation, xbrl_tag: "us-gaap:InterestIncomeExpenseNet+NoninterestIncome" } };
    const t2 = table([row("revenue", "2791000000", sum), row("interest_expense", "1320000000")], "is");
    expect(t2.rows.find((r) => r.metric === "interest_expense")!.label).toContain("already taken out");
    const t3 = table([row("revenue", "100"), row("interest_expense", "5")], "is");
    expect(t3.rows.find((r) => r.metric === "interest_expense")!.label).toBe("Interest expense");
  });

  it("shows what assets less liabilities and equity leave, when it's real (Guerrilla RF's $20M)", () => {
    const t = table([row("total_assets", "23327718"), row("total_liabilities", "25742723"), row("stockholders_equity", "-22448560")], "bs");
    const gap = t.rows.find((r) => r.metric === "other_equity")!;
    expect(gap.label).toBe("Not itemised");
    expect(gap.cells[0]).toMatchObject({ text: "$20.03M", derived: true });
    expect(gap.cells[0]!.title).toContain("redeemable or mezzanine equity");
    const tied = table([row("total_assets", "100"), row("total_liabilities", "60"), row("stockholders_equity", "40")], "bs");
    expect(tied.rows.some((r) => r.metric === "other_equity")).toBe(false);
  });

  it("never puts a computed row in the CSV", () => {
    const rows = [row("operating_income", "100"), row("net_income", "70")];
    const csv = financialsCsv(rows, table(rows, "is"), "quarter");
    expect(csv).not.toContain("other_items");
    expect(csv).toContain("net_income");
  });
});

describe("debt that includes a finance arm (migration 055)", () => {
  it("Ford's and GM's pages say their net debt includes Ford Credit's and GM Financial's", () => {
    expect(financeArmNote("0000037996", "Ford Motor")).toBe("Ford Motor's debt and net debt are consolidated: they include " +
      "Ford Credit's borrowings, which fund its loans and leases to dealers and customers, as well as the automotive " +
      "business's own debt. The 10-K shows the two separately in its segment information.");
    expect(financeArmNote("0001467858", "General Motors")).toContain("they include GM Financial's borrowings");
    expect(financeArmNote(NVDA, "Nvidia")).toBeNull();
  });

  it("names exactly the companies whose debt 055 sums with a finance member", () => {
    const sql = readFileSync(`${process.cwd()}/../../db/migrations/055_filing_xbrl_facts.sql`, "utf8");
    const summed = new Set([...sql.matchAll(/\('(\d{10})', '(?:DebtCurrent|LongTermDebt\w*)\[[^\]]*(?:FordCredit|GmFinancial)Member/g)].map((m) => m[1]));
    expect([...summed].sort()).toEqual(Object.keys(FINANCE_ARMS).sort());
  });
});
