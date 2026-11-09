/** Formula metrics for the chart builder, from /v1/screener/columns (the API's formula
 * catalog: each metric with its unit and the spans it has values for). */

import { money } from "../format";
import type { ColumnInfo } from "../types";

export interface MetricInfo {
  name: string;
  label: string;
  unit: string;
  ttm: boolean;
}

/** Balances and ratios of balances: one date each, no trailing twelve months (query-api formula/check.py
 * POINT_IN_TIME). The API's /v1/screener/columns says so per column (`balance`); this is for an older
 * API's answer and for a chart point, which doesn't carry it. Change with check.py. */
export const POINT_IN_TIME = new Set(["cash", "short_term_investments", "receivables", "inventory", "current_assets",
  "total_assets", "current_liabilities", "long_term_debt_current", "long_term_debt", "total_liabilities",
  "stockholders_equity", "net_debt", "shares_outstanding", "debt_to_equity", "current_ratio", "loans", "deposits",
  "cet1_ratio", "cash_and_restricted_cash", "contract_liabilities_current", "contract_liabilities",
  "remaining_performance_obligations", "accounts_payable", "cash_and_investments"]);

const SPECIAL: Record<string, string> = {
  sga: "SG&A",
  eps_basic: "EPS (basic)",
  eps_diluted: "EPS (diluted)",
  roe: "ROE",
  capex: "Capex",
  fcf_margin: "FCF margin",
  free_cash_flow: "Free cash flow",
  research_and_development: "R&D",
  long_term_debt: "Long-term debt",
  long_term_debt_current: "Long-term debt, current",
  eps_growth: "EPS growth",
  depreciation_amortization: "D&A",
  // 049 (F5)
  contract_liabilities_current: "Contract liabilities, current",
  contract_liabilities: "Contract liabilities",
  remaining_performance_obligations: "Remaining performance obligations",
  cash_and_restricted_cash: "Cash with restricted cash",
  cash_and_investments: "Cash and short-term investments",
  change_in_cash: "Change in cash",
};

/** "operating_margin" → "Operating margin". */
export function metricLabel(name: string): string {
  if (SPECIAL[name]) return SPECIAL[name];
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function catalog(columns: ColumnInfo[]): MetricInfo[] {
  const out = new Map<string, MetricInfo>();
  for (const c of columns) {
    const m = out.get(c.metric) ?? { name: c.metric, label: metricLabel(c.metric), unit: c.unit, ttm: false };
    if (c.span === "ttm") m.ttm = true;
    out.set(c.metric, m);
  }
  return [...out.values()].sort((a, b) => a.label.localeCompare(b.label));
}

/** A unit as a caption: axis titles, table headers. A plain number has none. */
export function unitLabel(unit: string): string {
  return ({
    USD: "USD", ratio: "%", pct: "% change", pts: "% points", index: "index = 100", "USD/shares": "USD per share",
    shares: "shares", multiple: "multiple (x)", number: "",
  } as Record<string, string>)[unit] ?? unit;
}

/** Decimals an axis needs so that ticks `step` apart don't read the same. */
const decimalsFor = (step: number | undefined) =>
  step === undefined || step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3;

function plainNumber(v: number, compact: boolean, step?: number): string {
  const abs = Math.abs(v);
  if (compact && abs >= 1e3) {
    const [d, s] = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : [1e3, "K"];
    return `${Number((v / d).toPrecision(3))}${s}`;
  }
  const most = compact ? decimalsFor(step) : abs >= 100 ? 1 : 2;
  return v.toLocaleString("en-US", { maximumFractionDigits: most, minimumFractionDigits: compact ? most : 0 });
}

/** Axis and tooltip text for a value in a series' unit: "pct" for a change, "pts" for points of
 * growth, "index" for indexed, "multiple" and "number" for a ratio that isn't a percent. `compact`
 * is an axis tick; `step`, the distance between ticks, gives it the decimals it needs. */
export function formatValue(v: number, unit: string, compact = false, step?: number): string {
  switch (unit) {
    case "USD":
      // an axis tick is a round number: "$400B", not "$400.00B"
      return compact ? money(v, "USD").replace(/\.?0+(?=[TBMK]$)/, "") : money(v, "USD");
    case "USD/shares":
      if (compact && Math.abs(v) >= 1000) return money(v, "USD").replace(/\.?0+(?=[TBMK]$)/, "");
      return `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(compact && step !== undefined && step >= 1 ? 0 : 2)}`;
    case "ratio":
    case "pct":
      return `${(v * 100).toFixed(compact ? decimalsFor(step === undefined ? undefined : step * 100) : 1)}%`;
    case "pts":
      return `${(v * 100).toFixed(compact ? decimalsFor(step === undefined ? undefined : step * 100) : 1)} pts`;
    case "index":
      return v.toFixed(compact ? 0 : 1);
    case "multiple":
      return `${v.toFixed(compact ? decimalsFor(step) : 2)}x`;
    case "number":
      return plainNumber(v, compact, step);
    case "shares": {
      const abs = Math.abs(v);
      const [d, s] = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : abs >= 1e3 ? [1e3, "K"] : [1, ""];
      const text = `${(v / d).toFixed(2)}${s} sh`;
      return compact ? text.replace(/\.?0+(?=[BMK]? sh$)/, "") : text;
    }
    default:
      return compact ? v.toPrecision(3) : v.toPrecision(6);
  }
}
