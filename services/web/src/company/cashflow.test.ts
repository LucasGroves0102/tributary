import { describe, expect, it } from "vitest";

import { fin } from "../fixtures";
import type { Callout, FinancialRow } from "../types";
import { calloutLines, calloutText, SWING } from "./callouts";
import { factWhy, figureText, headline, partName, roleMark, tagText } from "./components";
import { financialsCsv, metricTable, negated, opensIntoParts, STATEMENTS, statementRequest, ties, withBridges } from "./data";

// NVIDIA's FY2027 Q1-Q2 cash flow (production gold, Jul 26, 2026 10-Q; the subtotals as 049 maps them)
const Q1 = ["2026-04-26", "FY2027 Q1", "2026-01-26"] as const;
const Q2 = ["2026-07-26", "FY2027 Q2", "2026-04-27"] as const;
const at = (q: readonly [string, string, string], metric: string, value: string, over: Partial<FinancialRow> = {}) =>
  fin(metric, q[0], value, q[1], { period_start: metric === "cash_and_restricted_cash" ? null : q[2], ...over });
const cashflow = (extra: FinancialRow[] = []) => [
  fin("cash_and_restricted_cash", "2026-01-25", "10605000000", "FY2026 Q4"),
  at(Q1, "operating_cash_flow", "50340000000"), at(Q1, "investing_cash_flow", "-31000000000"),
  at(Q1, "financing_cash_flow", "-20000000000"), at(Q1, "change_in_cash", "-660000000"),
  at(Q1, "cash_and_restricted_cash", "9945000000"), at(Q1, "capex", "1760000000"),
  at(Q1, "dividends_paid", "243000000"),
  at(Q2, "operating_cash_flow", "24080000000"), at(Q2, "investing_cash_flow", "-2000000000"),
  at(Q2, "financing_cash_flow", "-8500000000"), at(Q2, "change_in_cash", "13477000000"),
  at(Q2, "cash_and_restricted_cash", "23422000000"), at(Q2, "capex", "2680000000"),
  at(Q2, "dividends_paid", "6047000000", { value_source: "derived" }),
  ...extra,
];
// two columns shown: the request's extra quarter before them only gives the first its starting cash
const table = (rows: FinancialRow[], callouts: Callout[] = []) =>
  withBridges(metricTable(rows, STATEMENTS.cf.rows, "q", "fiscal", [], 2, [], callouts), rows, "cf");

const DIVIDENDS: Callout = {
  metric: "dividends_paid", period_end: "2026-07-26", kind: "outlier", edge: true, value: 6047e6, factor: 24.9,
  beside: [{ period_end: "2026-01-25", value: 242e6 }, { period_end: "2026-04-26", value: 243e6 }], seasonal: false,
};

describe("the cash flow statement (F5, #111)", () => {
  it("reads as a statement: items, then their subtotal, then the change in cash and where it starts and ends", () => {
    const t = table(cashflow());
    expect(t.rows.map((r) => r.metric)).toEqual([
      "operating_cash_flow", "capex", "investing_cash_flow", "dividends_paid", "financing_cash_flow", "fx_and_other",
      "change_in_cash", "cash_beginning", "cash_and_restricted_cash", "cash_reconciliation"]);
    expect(t.rows.find((r) => r.metric === "investing_cash_flow")!.opts).toMatchObject({ total: true });
    expect(t.rows.find((r) => r.metric === "capex")!.opts).toMatchObject({ sub: true, outflow: true });
  });

  it("shows every outflow negative, as the filing prints it, the CSV too, and says so in the hover", () => {
    const rows = cashflow();
    const t = table(rows);
    const capex = t.rows.find((r) => r.metric === "capex")!;
    expect(capex.cells.map((c) => c?.text)).toEqual(["-$1.76B", "-$2.68B"]);
    expect(capex.cells[1]!.raw).toBe("-2680000000");
    expect(capex.cells[1]!.title).toContain("Shown negative: an outflow");
    expect(capex.cells[1]!.title).toContain("the amount paid, $2.68B");
    expect(t.rows.find((r) => r.metric === "investing_cash_flow")!.cells[0]!.text).toBe("-$31.00B");
    const csv = financialsCsv(rows, t, "quarter");
    expect(csv).toContain("capex,quarter,FY2027 Q2,,2026-04-27,2026-07-26,-2680000000,");
    expect(csv).toContain("investing_cash_flow,quarter,FY2027 Q2,,2026-04-27,2026-07-26,-2000000000,");
    expect(csv).not.toContain("cash_reconciliation");
    expect(negated("-5")).toBe("5");
    expect(negated("0")).toBe("0");
  });

  it("starts each period with the cash the one before ended with, and checks start + change = end", () => {
    const t = table(cashflow());
    expect(t.rows.find((r) => r.metric === "cash_beginning")!.cells.map((c) => c?.text)).toEqual(["$10.61B", "$9.95B"]);
    const check = t.rows.find((r) => r.metric === "cash_reconciliation")!;
    expect(check.cells.map((c) => [c?.text, c?.tone])).toEqual([["✓ ties", "ok"], ["✓ ties", "ok"]]);
    expect(check.cells[1]!.title).toContain("$9.95B + $13.48B = $23.42B");
  });

  it("says where it doesn't tie, by how much, without calling a figure wrong", () => {
    const rows = cashflow().map((r) => (r.metric === "cash_and_restricted_cash" && r.period_end === Q2[0] ? { ...r, value: "26422000000" } : r));
    const cell = table(rows).rows.find((r) => r.metric === "cash_reconciliation")!.cells[1]!;
    expect(cell).toMatchObject({ text: "off $3.00B", tone: "warn" });
    expect(cell.title).toContain("$3.00B more");
    expect(cell.title).toContain("The figures are as filed");
    expect(ties(100e6, 1e6, 101.4e6)).toBe(true); // within 0.5%
    expect(ties(1e6, 0, 1.09e6)).toBe(true); // within $100K
    expect(ties(1e6, 0, 1.2e6)).toBe(false);
  });

  it("puts exchange rates and anything else apart in its own line, only when it is more than rounding", () => {
    const t = table(cashflow());
    const other = t.rows.find((r) => r.metric === "fx_and_other")!;
    expect(other.cells.map((c) => c?.text)).toEqual(["$0", "-$103.00M"]);
    expect(other.cells[1]!.title).toContain("the effect of exchange rates on cash");
    const exact = cashflow().map((r) => (r.metric === "change_in_cash" && r.period_end === Q2[0] ? { ...r, value: "13580000000" } : r));
    expect(table(exact).rows.some((r) => r.metric === "fx_and_other")).toBe(false);
  });

  it("asks for four more quarters so the first column has its starting cash", () => {
    expect(statementRequest("q", "8q", "cf")).toEqual({ view: "quarter", quarters: 12 });
    expect(statementRequest("a", "8q", "cf")).toEqual({ view: "ttm", quarters: 44 });
    expect(statementRequest("q", "max", "cf").quarters).toBe(120);
    expect(statementRequest("q", "8q", "is")).toEqual({ view: "quarter", quarters: 8 });
  });
});

describe("callouts (F5, #106, #112)", () => {
  it("mark the quarter the API calls out, in quarterly tables only", () => {
    const t = table(cashflow(), [DIVIDENDS]);
    const div = t.rows.find((r) => r.metric === "dividends_paid")!;
    expect(div.cells[1]!.callout).toEqual(DIVIDENDS);
    expect(div.cells[0]!.callout).toBeUndefined();
    const ttm = metricTable(cashflow(), STATEMENTS.cf.rows, "ttm", "fiscal", [], 8, [], [DIVIDENDS]);
    expect(ttm.rows.some((r) => r.cells.some((c) => c?.callout))).toBe(false);
  });

  it("say what the figure is next to, in the statement's sign, and where to read why", () => {
    expect(calloutText(DIVIDENDS, "Dividends paid", "FY2027 Q2", -1)).toBe(
      "Dividends paid, FY2027 Q2: -$6.05B, 25 times the two quarters before it (-$242.00M and -$243.00M). The filing says why.");
    const seasonal = { ...DIVIDENDS, edge: false, seasonal: true, factor: 6.4 };
    expect(calloutText(seasonal, "Operating cash flow", "FY2026 Q3")).toContain("6.4 times the quarters either side");
    expect(calloutText(seasonal, "Operating cash flow", "FY2026 Q3")).toContain("it's the company's pattern");
    const lines = calloutLines(table(cashflow(), [DIVIDENDS]));
    expect(lines).toHaveLength(1);
    expect(lines[0]!.cells[0]!.cell.source).toMatchObject({ form: "10-Q" });
  });

  it("say a row that keeps changing sign once, for the row (JPMorgan's operating cash flow)", () => {
    const ends = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"];
    const values = ["29547000000", "-45214000000", "119724000000", "-211761000000", "-25283000000", "33161000000"];
    const rows = ends.map((e, i) => fin("operating_cash_flow", e, values[i]!, `FY2025 Q${i + 1}`));
    const flips: Callout[] = [0, 1, 2, 5].map((i) => ({
      metric: "operating_cash_flow", period_end: ends[i]!, kind: "sign", edge: i === 5, value: Number(values[i]), factor: null,
      beside: [{ period_end: "x", value: 1 }, { period_end: "y", value: 1 }], seasonal: false,
    }));
    const t = metricTable(rows, STATEMENTS.cf.rows, "q", "fiscal", [], 8, [], flips);
    const lines = calloutLines(t);
    expect(lines).toHaveLength(1);
    expect(flips.length).toBeGreaterThanOrEqual(SWING);
    expect(lines[0]!.text).toBe("Net cash from operating activities changes sign in 4 of these 6 quarters, from -$211.76B to $119.72B: read any one quarter with the ones beside it.");
    expect(lines[0]!.cells).toHaveLength(4);
  });
});

describe("a standardised line opens into its parts (F5)", () => {
  it("opens derived figures, computed gross profit and sums, not plain or TTM figures", () => {
    const derived = fin("net_debt", "2026-07-26", "-66003000000", "FY2027 Q2", { inputs: { cash: 23422000000 } });
    expect(opensIntoParts(derived)).toBe(true);
    const sum = fin("short_term_investments", "2026-07-26", "1", "FY2027 Q2", { citation: { ...derived.citation, xbrl_tag: "us-gaap:DebtSecuritiesCurrent+EquitySecuritiesFvNi" } });
    expect(opensIntoParts(sum)).toBe(true);
    expect(opensIntoParts(fin("gross_profit", "2026-07-26", "1", "x", { citation: { ...derived.citation, xbrl_tag: "derived:revenue-cost_of_revenue" } }))).toBe(true);
    expect(opensIntoParts(fin("revenue", "2026-07-26", "1", "x"))).toBe(false);
    expect(opensIntoParts(fin("revenue", "2026-07-26", "1", "x", { inputs: { quarters: [] } }))).toBe(false);
    const t = metricTable([derived], STATEMENTS.bs.rows, "q");
    expect(t.rows[0]!.cells[0]).toMatchObject({ opens: true, inputs: { cash: 23422000000 } });
  });

  it("words the parts: names, signs, tags and why a tag wasn't used", () => {
    expect(partName("long_term_debt_current")).toBe("Debt due within a year");
    expect(partName("revenue_year_ago")).toBe("Revenue a year earlier");
    expect([roleMark("+"), roleMark("-"), roleMark("denominator")]).toEqual(["+", "−", "÷ by"]);
    expect(tagText("us-gaap:DebtSecuritiesCurrent+EquitySecuritiesFvNi")).toBe("us-gaap: DebtSecuritiesCurrent + EquitySecuritiesFvNi (added up)");
    expect(tagText("derived:revenue-cost_of_revenue")).toBe("computed: revenue less cost of revenue");
    expect(figureText("0.751", "ratio")).toBe("75.1%");
    const fact = (tag: string, priority: number, used: boolean, line = true) => ({
      tag, taxonomy: "us-gaap", priority, line, period_start: null, period_end: "2026-07-26", value: "1", accession_no: "a",
      form: "10-Q", filed: "2026-08-26", filing_url: null, filings: 1, used,
    });
    const facts = [fact("LongTermDebtNoncurrent", 1, true), fact("LongTermDebt", 3, false), fact("EquitySecuritiesFvNi", 11, false, false)];
    expect(facts.map((f) => factWhy(f, facts))).toEqual(["used", "not used: LongTermDebtNoncurrent comes first", "only ever part of a sum"]);
    expect(headline({
      company_id: "0001045810", metric: "net_debt", span: "quarter", period_end: "2026-07-26", how: "derived", definition: null,
      components: [], parts: [], facts: [],
      figure: { metric: "net_debt", period_start: null, period_end: "2026-07-26", fiscal_period: "FY2027 Q2", value: "-66003000000", unit: "USD", value_source: "computed", how: "derived",
        citation: { accession_no: "a", form: "10-Q", filed: "2026-08-26", xbrl_tag: null, source_accessions: null, filing_url: null } },
    }, "Net debt")).toBe("Net debt, FY2027 Q2 (Jul 26, 2026): -$66.00B");
  });
});
