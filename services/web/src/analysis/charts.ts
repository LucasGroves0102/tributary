/** The Numbers step's prebuilt charts: ordinary chart builder states (chart/model.ts), so each
 * draws like any saved chart (reports/Embeds ChartView), opens in the chart builder, saves to
 * the project and pins into the thesis as an embed. Ten fiscal years, one point per year
 * (annual frequency: flows as the year's total, balances at the year end), on the company's
 * fiscal calendar; receivables vs revenue is the Revenue tab's quarterly chart. Only metrics the
 * formula catalog has (query-api formula/check.py CATALOG). Pure. */

import type { ChartState } from "../chart/model";
import { qualityChart } from "../company/revenue";

export interface NumbersChart {
  key: "growth" | "margins" | "fcf" | "receivables" | "debt";
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
      read: "Free cash flow = operating cash flow − capex. Profits that don't turn into cash deserve a closer look.",
      chart: annual(`${ticker}: free cash flow (operating cash flow − capex)`, [
        { id: 1, kind: "metric", companyId, metric: "operating_cash_flow", transform: "ttm", type: "bar", axis: "L", name: "Operating cash flow", color: 4 },
        { id: 2, kind: "metric", companyId, metric: "capex", transform: "ttm", type: "bar", axis: "L", name: "Capex", color: 7 },
        { id: 3, kind: "metric", companyId, metric: "free_cash_flow", transform: "ttm", type: "line", axis: "L", name: "Free cash flow", color: 1 },
      ]),
    },
    {
      key: "receivables",
      read: "Receivables growing much faster than revenue can mean sales booked before the cash comes in (earnings quality).",
      chart: qualityChart(companyId, ticker),
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
