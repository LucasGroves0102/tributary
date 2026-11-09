/** The Numbers step's prebuilt charts: ordinary chart builder states (chart/model.ts), so each
 * draws like any saved chart (reports/Embeds ChartView), opens in the chart builder, saves to
 * the project and pins into the thesis as an embed. Ten fiscal years, one point per year
 * (annual frequency: flows as the year's total, balances at the year end), on the company's
 * fiscal calendar, then the year in progress: the latest quarter, marked partial (series.ts
 * `annualPoints`). Receivables vs revenue is the Revenue tab's quarterly chart. Only metrics the
 * formula catalog has (query-api formula/check.py CATALOG). Pure.
 *
 * Outflows (capex, buybacks, dividends) are drawn below zero, as the cash flow statement prints them
 * (F5's one sign convention): `-capex` formula layers, since the API serves them as amounts paid. */

import type { ChartState, Layer } from "../chart/model";
import { qualityChart } from "../company/revenue";

export interface NumbersChart {
  key: "growth" | "margins" | "fcf" | "returns" | "shares" | "inventory" | "receivables" | "backlog" | "debt";
  /** what to look for, under the chart */
  read: string;
  chart: ChartState;
}

const annual = (title: string, layers: ChartState["layers"]): ChartState => ({
  title,
  layers,
  range: 10,
  align: "fiscal",
  frequency: "annual",
  nextId: layers.length + 1,
});

/** An outflow as the statement prints it: the amount paid, negated. */
const outflow = (companyId: string, metric: string, layer: Omit<Layer, "kind" | "formula" | "defaultCompany" | "transform">): Layer =>
  ({ ...layer, kind: "formula", formula: `-${metric}`, defaultCompany: companyId, transform: "ttm" });

export function numbersCharts(companyId: string, ticker: string): NumbersChart[] {
  return [
    {
      key: "growth",
      read: "Is growth steady, accelerating or fading? One-off jumps usually mean an acquisition: check the 10-K.",
      chart: annual(`${ticker}: revenue and growth`, [
        { id: 1, kind: "metric", companyId, metric: "revenue", transform: "ttm", type: "bar", axis: "L", name: "Revenue", color: 0 },
        { id: 2, kind: "metric", companyId, metric: "revenue", transform: "yoy", type: "line", axis: "R", name: "Revenue growth (YoY)", color: 2 },
      ]),
    },
    {
      key: "margins",
      read: "Gross margin is pricing power; operating margin adds cost discipline. Widening or narrowing over the decade?",
      chart: annual(`${ticker}: gross and operating margin`, [
        { id: 1, kind: "metric", companyId, metric: "gross_margin", transform: "ttm", type: "line", axis: "L", name: "Gross margin", color: 1 },
        { id: 2, kind: "metric", companyId, metric: "operating_margin", transform: "ttm", type: "line", axis: "L", name: "Operating margin", color: 0 },
      ]),
    },
    {
      key: "fcf",
      read: "Free cash flow = operating cash flow − capex (capex is an outflow, drawn below zero). Profits that don't turn into cash deserve a closer look.",
      chart: annual(`${ticker}: free cash flow (operating cash flow − capex)`, [
        { id: 1, kind: "metric", companyId, metric: "operating_cash_flow", transform: "ttm", type: "bar", axis: "L", name: "Operating cash flow", color: 4 },
        outflow(companyId, "capex", { id: 2, type: "bar", axis: "L", name: "Capex", color: 7 }),
        { id: 3, kind: "metric", companyId, metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L", name: "Free cash flow", color: 1 },
      ]),
    },
    {
      key: "returns",
      read: "Buybacks and dividends are outflows, drawn below zero; free cash flow is what pays for them. Returning more than free cash flow means borrowing or running cash down.",
      chart: annual(`${ticker}: buybacks and dividends against free cash flow`, [
        outflow(companyId, "share_buybacks", { id: 1, type: "sbar", axis: "L", name: "Share buybacks", color: 5 }),
        outflow(companyId, "dividends_paid", { id: 2, type: "sbar", axis: "L", name: "Dividends paid", color: 2 }),
        { id: 3, kind: "metric", companyId, metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L", name: "Free cash flow", color: 1 },
      ]),
    },
    {
      key: "shares",
      read: "A falling count means buybacks outpace new shares; a rising one, dilution from stock pay or deals. A jump by a whole multiple is a stock split, not new shares (Data notes says when).",
      chart: annual(`${ticker}: shares outstanding`, [
        { id: 1, kind: "metric", companyId, metric: "shares_outstanding", transform: "q", type: "line", axis: "L", name: "Shares outstanding", color: 0 },
      ]),
    },
    {
      key: "inventory",
      read: "Inventory growing faster than sales can mean slowing demand or markdowns ahead. The latest quarter (*) can be a seasonal build: compare it with the same quarter a year earlier in Statements.",
      chart: annual(`${ticker}: inventory and days of inventory`, [
        { id: 1, kind: "metric", companyId, metric: "inventory", transform: "q", type: "bar", axis: "L", name: "Inventory", color: 3 },
        { id: 2, kind: "formula", formula: "inventory / cost_of_revenue * 365", defaultCompany: companyId, transform: "ttm", type: "line", axis: "R", name: "Days of inventory (inventory / cost of revenue × 365)", color: 2 },
      ]),
    },
    {
      key: "receivables",
      read: "Receivables growing much faster than revenue can mean sales booked before the cash comes in (earnings quality).",
      chart: qualityChart(companyId, ticker),
    },
    {
      key: "backlog",
      read: "Remaining performance obligations are revenue under contract not yet recognised, the nearest tagged figure to a backlog; contract liabilities are customer advances. Rising faster than revenue means demand ahead of output. Not every company tags them; the backlog and orders the filings state in words are in the next card, from the filing's text.",
      chart: annual(`${ticker}: backlog proxy and customer advances against revenue`, [
        { id: 1, kind: "metric", companyId, metric: "remaining_performance_obligations", transform: "q", type: "bar", axis: "L", name: "Remaining performance obligations", color: 0 },
        { id: 2, kind: "metric", companyId, metric: "contract_liabilities_current", transform: "q", type: "bar", axis: "L", name: "Contract liabilities (customer advances)", color: 4 },
        { id: 3, kind: "metric", companyId, metric: "revenue", transform: "ttm", type: "line", axis: "L", name: "Revenue", color: 2 },
      ]),
    },
    {
      key: "debt",
      read: "How much is borrowed and how fast it moves; debt to equity on the right. Rising debt with flat cash flow is a warning.",
      chart: annual(`${ticker}: debt at year end`, [
        { id: 1, kind: "metric", companyId, metric: "long_term_debt", transform: "q", type: "sbar", axis: "L", name: "Long-term debt", color: 3 },
        { id: 2, kind: "metric", companyId, metric: "long_term_debt_current", transform: "q", type: "sbar", axis: "L", name: "Debt due within a year", color: 2 },
        { id: 3, kind: "metric", companyId, metric: "debt_to_equity", transform: "q", type: "line", axis: "R", name: "Debt to equity", color: 5 },
      ]),
    },
  ];
}
