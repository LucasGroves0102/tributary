import { describe, expect, it } from "vitest";

import { ACC, fin, NVDA, nvdaRow } from "../fixtures";
import type { Recap, RecapValue, SegmentRow } from "../types";
import {
  eightKItems,
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
} from "./data";

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
      sic: "3674", fye: "January", filer: "Large accelerated", state: "DE",
    });
  });

  it("recaps only a 10-Q or 10-K", () => {
    expect(recapAccession(nvdaRow())).toBe(ACC);
    expect(recapAccession(nvdaRow({ sources: { revenue_q: { ...SRC, form: "8-K" } } }))).toBeNull();
    expect(recapAccession(nvdaRow({ sources: {} }))).toBeNull();
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
});

it("labels 8-K items and filters forms", () => {
  expect(eightKItems(["2.02", "9.01", "EX-99.1", "5.02"])).toEqual(["2.02 Results of operations", "5.02 Officer or director change"]);
  expect(formMatches("10-K/A", "10-K")).toBe(true);
  expect(formMatches("10-Q", "10-K")).toBe(false);
  expect(formMatches("8-K", "all")).toBe(true);
});

describe("banks on the tearsheet", () => {
  it("key stats and ratio rows read n/a where the metric doesn't describe a bank", () => {
    const bank = nvdaRow({ industry: "National Commercial Banks", values: { revenue_ttm: "180000000000" } });
    const stats = keyStats(bank);
    expect(stats.find((s) => s.label === "Operating margin")).toMatchObject({ text: "n/a (bank)", period: "not applicable" });
    expect(stats.find((s) => s.label === "Net debt")!.title).toContain("doesn't describe a bank");
    expect(stats.find((s) => s.label === "Gross margin")).toMatchObject({ text: "—" });
    const rows = [fin("roe", "2026-07-26", "0.17", "FY2026 Q2")];
    const t = metricTable(rows, RATIOS, "ttm", "fiscal", "National Commercial Banks");
    expect(t.rows.map((r) => r.metric)).toEqual(["operating_margin", "fcf_margin", "roe"]);
    expect(t.rows[0]).toMatchObject({ bank: true, cells: [null] });
    // not a bank: empty rows are dropped as before
    expect(metricTable(rows, RATIOS, "ttm").rows.map((r) => r.metric)).toEqual(["roe"]);
  });
});
