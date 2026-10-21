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

const SPECIAL: Record<string, string> = {
  sga: "SG&A",
  eps_basic: "EPS (basic)",
  eps_diluted: "EPS (diluted)",
  roe: "ROE",
  capex: "Capex",
  fcf_margin: "FCF margin",
  free_cash_flow: "Free cash flow",
  research_and_development: "R&D",
  long_term_debt_current: "Long-term debt, current",
  depreciation_amortization: "D&A",
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

/** Axis and tooltip text for a value in a series' unit ("pct" for YoY, "index" for indexed). */
export function formatValue(v: number, unit: string, compact = false): string {
  switch (unit) {
    case "USD":
      return money(v, "USD");
    case "USD/shares":
      return `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
    case "ratio":
    case "pct":
      return `${(v * 100).toFixed(compact ? 0 : 1)}%`;
    case "index":
      return v.toFixed(compact ? 0 : 1);
    case "shares": {
      const abs = Math.abs(v);
      const [d, s] = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : abs >= 1e3 ? [1e3, "K"] : [1, ""];
      return `${(v / d).toFixed(2)}${s} sh`;
    }
    default:
      return compact ? v.toPrecision(3) : v.toPrecision(6);
  }
}
