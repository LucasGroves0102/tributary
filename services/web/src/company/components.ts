/** Reported vs standardised in words (F5, Riley's top fix #1): what /v1/companies/{id}/components says a
 * figure is made of (query_api/components.py decides; this file only words it). A standardised line opens
 * into its parts, each with the tag the filing used, its value and its filing, and the other tags that
 * reported a value for the same period and why gold didn't use them. */

import { metricLabel } from "../chart/metrics";
import { day, money, pct } from "../format";
import type { ComponentFact, Components, ComponentsHow } from "../types";

/** The statements' own names for the metrics a figure is made of, where they differ from metricLabel. */
const NAMES: Record<string, string> = {
  long_term_debt: "Long-term debt",
  long_term_debt_current: "Debt due within a year",
  cash: "Cash",
  short_term_investments: "Short-term investments",
  operating_cash_flow: "Net cash from operating activities",
  capex: "Capital expenditure",
  gross_profit: "Gross profit",
  revenue: "Revenue",
  cost_of_revenue: "Cost of revenue",
  stockholders_equity: "Stockholders' equity",
};

/** Each derived metric's inputs in the order a statement reads them, with their role: mirrors
 * query_api/components.py ROLES (change both). Only the order and sign of an opened row's lines across
 * the periods; what one period is made of always comes from the API. */
export const INPUT_ROLES: Record<string, [string, string][]> = {
  net_debt: [["long_term_debt", "+"], ["long_term_debt_current", "+"], ["cash", "-"], ["short_term_investments", "-"]],
  cash_and_investments: [["cash", "+"], ["short_term_investments", "+"]],
  free_cash_flow: [["operating_cash_flow", "+"], ["capex", "-"]],
  gross_margin: [["gross_profit", "numerator"], ["revenue", "denominator"]],
  operating_margin: [["operating_income", "numerator"], ["revenue", "denominator"]],
  net_margin: [["net_income", "numerator"], ["revenue", "denominator"]],
  fcf_margin: [["operating_cash_flow", "numerator"], ["capex", "numerator"], ["revenue", "denominator"]],
  debt_to_equity: [["long_term_debt", "numerator"], ["long_term_debt_current", "numerator"], ["stockholders_equity", "denominator"]],
  current_ratio: [["current_assets", "numerator"], ["current_liabilities", "denominator"]],
};

/** An opened row's line label: "+ Long-term debt", "− Cash", "Gross profit ÷", "÷ Revenue". */
export function inputLabel(metric: string, role: string | undefined): string {
  const name = partName(metric);
  return role === "+" ? `+ ${name}` : role === "-" ? `− ${name}` : role === "numerator" ? `${name} ÷` : role === "denominator" ? `÷ ${name}` : name;
}

export const partName = (metric: string): string =>
  NAMES[metric] ?? (metric.endsWith("_year_ago") ? `${partName(metric.slice(0, -9))} a year earlier` : metricLabel(metric));

/** The sign a part takes in the figure, as a statement writes it. */
export function roleMark(role: string | null | undefined): string {
  return role === "+" ? "+" : role === "-" ? "−" : role === "numerator" ? "÷ top" : role === "denominator" ? "÷ by" : "";
}

export const HOW: Record<ComponentsHow, string> = {
  derived: "Computed by Tributary from the lines below, by the rule in the definition.",
  ttm: "The trailing twelve months: the quarters below added up (or the year a 10-K states, where four quarters aren't on file).",
  sum: "The filing tags this line only as parts, so it is their total.",
  computed: "The filing prints no gross profit: revenue less cost of revenue.",
  ytd: "A quarter the filing doesn't state on its own: the year to date less the year to date a quarter earlier.",
  reported: "As the filing tags it.",
};

export const LEFT_OUT: Record<"inside_cash" | "not_reported", string> = {
  inside_cash: "Left out: already inside cash. The investments are larger than what current assets hold beside cash, receivables and inventory, so the balance sheet counts them in cash.",
  not_reported: "Not reported at this date.",
};

/** A value in the figure's unit: ratios as percents, the rest as dollars. */
export function figureText(value: string | null, unit: string): string {
  if (value === null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return unit === "ratio" ? pct(n) : money(n, unit === "pure" ? "USD" : unit);
}

/** Why one fact behind a figure was, or wasn't, the one gold used. */
export function factWhy(f: ComponentFact, facts: ComponentFact[]): string {
  if (f.used) return f.role === "-" ? "used: the earlier year to date, taken away" : f.role === "+" ? "used: the year to date" : f.line ? "used" : "used, as a part of the sum";
  if (!f.line) return "only ever part of a sum";
  const chosen = facts.find((x) => x.used);
  if (chosen && chosen.priority < f.priority) return `not used: ${chosen.tag} comes first`;
  return "not used";
}

/** "us-gaap:LongTermDebtNoncurrent · 10-Q filed Aug 26, 2026" */
export function factSource(f: ComponentFact): string {
  return `${f.taxonomy}:${f.tag} · ${f.form ?? "filing"} filed ${day(f.filed, true)}${f.filings > 1 ? ` (stated in ${f.filings} filings, the latest used)` : ""}`;
}

/** The tag a figure's own row cites, worded: a sum's parts, a computed line, else the tag. */
export function tagText(tag: string | null): string {
  if (!tag) return "computed";
  if (tag.startsWith("derived:")) return "computed: revenue less cost of revenue";
  const [tax, name] = tag.includes(":") ? tag.split(/:(.*)/s) : ["", tag];
  return name!.includes("+") ? `${tax}: ${name!.split("+").join(" + ")} (added up)` : tag;
}

/** The heading of an opened figure: "Net debt, FY2027 Q2 (Jul 26, 2026): −$66.00B". */
export function headline(c: Components, label: string): string {
  const when = c.figure.fiscal_period ? `${c.figure.fiscal_period} (${day(c.period_end, true)})` : day(c.period_end, true);
  return `${label}, ${c.span === "ttm" ? "TTM to " : ""}${when}: ${figureText(c.figure.value, c.figure.unit)}`;
}
