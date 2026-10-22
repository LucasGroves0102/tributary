/** The company page's data shaping: header facts, key stats with their periods, statement and
 * ratio tables (quarterly / TTM / annual) with a source per cell, segment tables, the latest
 * quarter recap, 8-K item labels and CSVs. Pure, tested without rendering. */

import { csvCell } from "../chart/series";
import { calendarLabel } from "../compare/state";
import { FILER_LABELS, fyeMonth, type FilerCategory } from "../companies/screen";
import { BANK_NA, bankTitle, change, day, money, notForBanks, pct } from "../format";
import { num } from "../home/data";
import type { FinancialRow, Recap, RecapValue, ScreenerRow, SegmentRow, ValueSource } from "../types";

export type Fmt = "usd" | "pct" | "growth" | "x" | "eps" | "shares";

export function fmt(kind: Fmt, v: number): string {
  switch (kind) {
    case "usd":
      return money(v, "USD");
    case "eps":
      return money(v, "USD/shares");
    case "pct":
      return pct(v);
    case "growth":
      return change(v);
    case "x":
      return `${v.toFixed(2)}x`;
    case "shares":
      return v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : `${(v / 1e6).toFixed(1)}M`;
  }
}

// --- header and key stats ------------------------------------------------------------------

export const KEY_STATS: { key: string; label: string; kind: Fmt }[] = [
  { key: "revenue_ttm", label: "Revenue", kind: "usd" },
  { key: "revenue_growth_ttm", label: "Revenue growth", kind: "growth" },
  { key: "gross_margin_ttm", label: "Gross margin", kind: "pct" },
  { key: "operating_margin_ttm", label: "Operating margin", kind: "pct" },
  { key: "net_income_ttm", label: "Net income", kind: "usd" },
  { key: "free_cash_flow_ttm", label: "Free cash flow", kind: "usd" },
  { key: "fcf_margin_ttm", label: "FCF margin", kind: "pct" },
  { key: "eps_diluted_q", label: "EPS (diluted)", kind: "eps" },
  { key: "cash_q", label: "Cash", kind: "usd" },
  { key: "net_debt_q", label: "Net debt", kind: "usd" },
  { key: "debt_to_equity_q", label: "Debt / equity", kind: "x" },
  { key: "roe_ttm", label: "Return on equity", kind: "pct" },
];

/** Asked for with the key stats: the latest quarter's revenue names the filing to recap. */
export const RECAP_COLUMN = "revenue_q";
export const HEADER_COLUMNS = [...KEY_STATS.map((s) => s.key), RECAP_COLUMN];

export interface KeyStat {
  label: string;
  text: string;
  /** the period the value covers, e.g. "TTM to Jul 26, 2026" */
  period: string;
  title: string;
}

function periodText(key: string, src: ValueSource): string {
  const end = day(src.period_end, true);
  if (key === "revenue_growth_ttm") return `TTM vs prior TTM, to ${end}`;
  if (key.endsWith("_ttm")) return `TTM to ${end}`;
  if (key === "eps_diluted_q") return `quarter to ${end}`;
  return `at ${end}`;
}

export function keyStats(row: ScreenerRow): KeyStat[] {
  return KEY_STATS.map(({ key, label, kind }) => {
    const v = num(row.values[key]);
    const src = row.sources[key] ?? null;
    if ((v === null || !src) && notForBanks(row.industry, key)) {
      return { label, text: BANK_NA, period: "not applicable", title: bankTitle(label) };
    }
    if (v === null || !src) return { label, text: "—", period: "not reported", title: `${label}: not reported for the latest period` };
    const period = periodText(key, src);
    return {
      label,
      text: fmt(kind, v),
      period,
      title: `${label}, ${period} (${calendarLabel(src.period_end)}). Source: ${src.form} filed ` +
        `${day(src.filed, true)}, ${src.accession_no}`,
    };
  });
}

export interface Header {
  name: string;
  tickers: string[];
  exchanges: string[];
  industry: string | null;
  sic: string | null;
  fye: string | null;
  filer: string | null;
  state: string | null;
}

export function header(row: ScreenerRow): Header {
  return {
    name: row.name ?? row.company_id,
    tickers: row.tickers ?? [],
    exchanges: row.exchanges ?? [],
    industry: row.industry,
    sic: row.sic,
    fye: fyeMonth(row.fiscal_year_end),
    filer: row.filer_category ? (FILER_LABELS[row.filer_category as FilerCategory] ?? row.filer_category) : null,
    state: row.state_of_incorporation,
  };
}

/** The 10-Q/10-K the latest quarter's values come from (recaps cover only those forms). */
export function recapAccession(row: ScreenerRow): string | null {
  const src = row.sources[RECAP_COLUMN];
  return src && /^10-[KQ]/.test(src.form) ? src.accession_no : null;
}

// --- statements and ratios -------------------------------------------------------------------

export type Period = "q" | "a" | "ttm";
/** Fiscal: every fiscal quarter, labelled FY; calendar: one per calendar quarter, labelled CY. */
export type Basis = "fiscal" | "calendar";
export type Statement = "is" | "bs" | "cf";

export const STATEMENTS: Record<Statement, { label: string; rows: [string, string, Fmt][] }> = {
  is: {
    label: "Income statement",
    rows: [
      ["revenue", "Revenue", "usd"],
      ["cost_of_revenue", "Cost of revenue", "usd"],
      ["gross_profit", "Gross profit", "usd"],
      ["research_and_development", "Research and development", "usd"],
      ["sga", "Selling, general and administrative", "usd"],
      ["operating_income", "Operating income", "usd"],
      ["interest_expense", "Interest expense", "usd"],
      ["pretax_income", "Pretax income", "usd"],
      ["income_tax", "Income tax", "usd"],
      ["net_income", "Net income", "usd"],
      ["eps_diluted", "EPS (diluted)", "eps"],
      ["weighted_shares_diluted", "Diluted shares", "shares"],
    ],
  },
  bs: {
    label: "Balance sheet",
    rows: [
      ["cash", "Cash", "usd"],
      ["short_term_investments", "Short-term investments", "usd"],
      ["receivables", "Receivables", "usd"],
      ["inventory", "Inventory", "usd"],
      ["current_assets", "Current assets", "usd"],
      ["total_assets", "Total assets", "usd"],
      ["current_liabilities", "Current liabilities", "usd"],
      ["long_term_debt_current", "Debt due within a year", "usd"],
      ["long_term_debt", "Long-term debt", "usd"],
      ["total_liabilities", "Total liabilities", "usd"],
      ["stockholders_equity", "Stockholders' equity", "usd"],
      ["net_debt", "Net debt", "usd"],
    ],
  },
  cf: {
    label: "Cash flow",
    rows: [
      ["operating_cash_flow", "Operating cash flow", "usd"],
      ["capex", "Capital expenditure", "usd"],
      ["free_cash_flow", "Free cash flow", "usd"],
      ["depreciation_amortization", "Depreciation and amortization", "usd"],
      ["stock_based_compensation", "Stock-based compensation", "usd"],
      ["dividends_paid", "Dividends paid", "usd"],
      ["share_buybacks", "Share buybacks", "usd"],
    ],
  },
};

export const RATIOS: [string, string, Fmt][] = [
  ["gross_margin", "Gross margin", "pct"],
  ["operating_margin", "Operating margin", "pct"],
  ["net_margin", "Net margin", "pct"],
  ["fcf_margin", "FCF margin", "pct"],
  ["revenue_growth", "Revenue growth YoY", "growth"],
  ["net_income_growth", "Net income growth YoY", "growth"],
  ["roe", "Return on equity", "pct"],
  ["debt_to_equity", "Debt / equity", "x"],
  ["current_ratio", "Current ratio", "x"],
  ["payout_ratio", "Payout ratio", "pct"],
];

/** What to request for a period choice: quarterly values, TTM ending each quarter, or annual
 * (TTM at each fiscal Q4: the fiscal year; balances at its end). */
export function statementRequest(period: Period): { view: "quarter" | "ttm"; quarters: number } {
  return period === "q" ? { view: "quarter", quarters: 8 } : period === "ttm" ? { view: "ttm", quarters: 8 } : { view: "ttm", quarters: 40 };
}

export const ANNUAL_YEARS = 10;

export interface TableCell {
  text: string;
  raw: string;
  title: string;
  derived: boolean;
  url: string | null;
}

export interface MetricTable {
  columns: { periodEnd: string; label: string; sub: string }[];
  /** bank: the metric doesn't describe this company (a bank), so empty cells read n/a */
  rows: { metric: string; label: string; cells: (TableCell | null)[]; bank?: boolean }[];
}

function cellTitle(label: string, r: FinancialRow): string {
  const src = r.value_source === "derived"
    ? `derived (${r.citation.source_accessions?.length ? `from ${r.citation.source_accessions.join(", ")}` : "computed"})`
    : `reported, ${r.citation.xbrl_tag}`;
  return `${label}, ${r.fiscal_period ?? day(r.period_end, true)} (period ended ${day(r.period_end, true)}). ` +
    `Source: ${r.citation.form} filed ${day(r.citation.filed, true)}, ${r.citation.accession_no}; ${src}`;
}

/** Rows (one metric per row, its values by period) for the metrics given; metrics without any
 * value are left out. Columns newest last, as statements read. */
export function metricTable(
  rows: FinancialRow[],
  metrics: [string, string, Fmt][],
  period: Period,
  basis: Basis = "fiscal",
  /** the company's SIC description: a bank's empty operating margin / FCF / net debt reads n/a */
  industry: string | null = null,
): MetricTable {
  const periodOf = (r: FinancialRow) => (basis === "calendar" ? r.calendar_period : r.fiscal_period) ?? null;
  const kept = period === "a" ? rows.filter((r) => periodOf(r)?.endsWith("Q4")) : rows;
  let ends = [...new Set(kept.map((r) => r.period_end))].sort();
  ends = ends.slice(-(period === "a" ? ANNUAL_YEARS : 8));
  const label = (end: string) => {
    const fp = kept.map(periodOf).find((p, i) => p && kept[i]!.period_end === end) ?? null;
    if (period === "a") return fp ? fp.replace(/ Q4$/, "") : `${basis === "calendar" ? "CY" : "FY"} ending ${end.slice(0, 7)}`;
    if (period === "ttm") return `TTM ${fp ?? calendarLabel(end)}`;
    return fp ?? calendarLabel(end);
  };
  const columns = ends.map((end) => ({ periodEnd: end, label: label(end), sub: day(end, true) }));
  const by = new Map(kept.map((r) => [`${r.metric}|${r.period_end}`, r]));
  return {
    columns,
    rows: metrics
      .map(([metric, name, kind]) => ({
        metric,
        label: name,
        cells: ends.map((end): TableCell | null => {
          const r = by.get(`${metric}|${end}`);
          const v = r ? num(r.value) : null;
          if (!r || v === null) return null;
          return {
            text: fmt(kind, v),
            raw: String(r.value),
            title: cellTitle(name, r),
            derived: r.value_source === "derived",
            url: r.citation.filing_url,
          };
        }),
        bank: notForBanks(industry, metric) || undefined,
      }))
      // a bank's operating margin / FCF / net debt stays, as n/a, so its absence is explained
      .filter((r) => r.cells.some(Boolean) || (r.bank && ends.length > 0)),
  };
}

/** Long-format CSV of the rows behind a table: one line per value with its source. */
export function financialsCsv(rows: FinancialRow[], table: MetricTable, view: string): string {
  const want = new Set(table.rows.map((r) => r.metric));
  const ends = new Set(table.columns.map((c) => c.periodEnd));
  const header = ["metric", "view", "fiscal_period", "calendar_period", "period_start", "period_end", "value",
    "unit", "value_source", "accession_no", "form", "filed", "xbrl_tag", "source_accessions"];
  const lines = rows
    .filter((r) => want.has(r.metric) && ends.has(r.period_end))
    .sort((a, b) => a.metric.localeCompare(b.metric) || a.period_end.localeCompare(b.period_end))
    .map((r) => [r.metric, view, r.fiscal_period ?? "", r.calendar_period ?? "", r.period_start ?? "", r.period_end,
      String(r.value), r.unit, r.value_source, r.citation.accession_no, r.citation.form, r.citation.filed,
      r.citation.xbrl_tag, (r.citation.source_accessions ?? []).join(" ")]);
  return [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

// --- recap -------------------------------------------------------------------------------------

const RECAP_LABELS: Record<string, [string, Fmt]> = {
  revenue: ["Revenue", "usd"],
  gross_profit: ["Gross profit", "usd"],
  operating_income: ["Operating income", "usd"],
  net_income: ["Net income", "usd"],
  eps_diluted: ["EPS (diluted)", "eps"],
  operating_cash_flow: ["Operating cash flow", "usd"],
  free_cash_flow: ["Free cash flow", "usd"],
  gross_margin: ["Gross margin", "pct"],
  operating_margin: ["Operating margin", "pct"],
  net_margin: ["Net margin", "pct"],
  cash: ["Cash", "usd"],
  total_assets: ["Total assets", "usd"],
  long_term_debt: ["Long-term debt", "usd"],
  stockholders_equity: ["Stockholders' equity", "usd"],
};

export interface RecapRow {
  label: string;
  current: string;
  prior: string;
  qoq: { text: string; sign: number } | null;
  yearAgo: string;
  yoy: { text: string; sign: number } | null;
  title: string;
}

function delta(v: RecapValue | null, unit: string): RecapRow["qoq"] {
  if (!v || v.change === undefined) return null;
  if (unit === "ratio") {
    const bp = num(v.change);
    return bp === null ? null : { text: `${bp >= 0 ? "+" : "-"}${Math.abs(bp * 1e4).toFixed(0)} bp`, sign: Math.sign(bp) };
  }
  const p = num(v.pct_change ?? null);
  return p === null ? null : { text: change(p), sign: Math.sign(p) };
}

function src(v: RecapValue | null): string {
  return v ? `${v.fiscal_period ?? day(v.period_end, true)}: ${v.citation.form} filed ${day(v.citation.filed, true)}, ${v.citation.accession_no}` : "";
}

/** Quarter values (TTM ones are for ratios the quarter lacks) vs the prior and year-ago
 * quarter; column labels from the values' fiscal periods. */
export function recapRows(recap: Recap): { labels: [string, string, string]; rows: RecapRow[] } {
  const quarter = recap.metrics.filter((m) => m.span === "quarter" && RECAP_LABELS[m.metric]);
  const first = quarter.find((m) => m.prior_quarter && m.year_ago) ?? quarter[0];
  const lab = (v: RecapValue | null | undefined, fallback: string) => v?.fiscal_period ?? (v ? calendarLabel(v.period_end) : fallback);
  const labels: [string, string, string] = [
    lab(first?.current, "This quarter"),
    lab(first?.prior_quarter, "Prior quarter"),
    lab(first?.year_ago, "Year ago"),
  ];
  const order = Object.keys(RECAP_LABELS);
  const rows = quarter
    .sort((a, b) => order.indexOf(a.metric) - order.indexOf(b.metric))
    .map((m) => {
      const [label, kind] = RECAP_LABELS[m.metric]!;
      const show = (v: RecapValue | null) => {
        const n = v ? num(v.value) : null;
        return n === null ? "—" : fmt(kind, n);
      };
      return {
        label,
        current: show(m.current),
        prior: show(m.prior_quarter),
        qoq: delta(m.prior_quarter, m.unit),
        yearAgo: show(m.year_ago),
        yoy: delta(m.year_ago, m.unit),
        title: [src(m.current), src(m.prior_quarter), src(m.year_ago)].filter(Boolean).join("\n"),
      };
    });
  return { labels, rows };
}

// --- segments ----------------------------------------------------------------------------------

export const AXIS_LABELS: Record<string, string> = {
  business_segment: "Business segments",
  geography: "Geography",
  product_service: "Products and services",
};
const SEGMENT_METRICS: Record<string, string> = { revenue: "Revenue", operating_income: "Operating income" };

export interface SegmentTable {
  axis: string;
  metric: string;
  title: string;
  columns: { periodEnd: string; label: string }[];
  rows: { member: string; label: string; cells: (TableCell | null)[]; share: number | null; yoy: number | null }[];
}

/** One table per axis and metric (revenue, operating income), members by period, newest
 * last; share of the latest period's total and YoY vs four periods earlier. Reconciliation
 * members (corporate, eliminations) are left out. */
export function segmentTables(rows: SegmentRow[]): SegmentTable[] {
  const groups = new Map<string, SegmentRow[]>();
  for (const r of rows) {
    if (!AXIS_LABELS[r.axis] || !SEGMENT_METRICS[r.metric]) continue;
    const k = `${r.axis}|${r.metric}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  const order = (k: string) => {
    const [axis, metric] = k.split("|");
    return Object.keys(AXIS_LABELS).indexOf(axis!) * 10 + Object.keys(SEGMENT_METRICS).indexOf(metric!);
  };
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b)).map(([k, rs]) => {
    const [axis, metric] = k.split("|") as [string, string];
    const ends = [...new Set(rs.map((r) => r.period_end))].sort().slice(-8);
    const columns = ends.map((e) => ({ periodEnd: e, label: rs.find((r) => r.period_end === e && r.fiscal_period)?.fiscal_period ?? calendarLabel(e) }));
    const members = [...new Map(rs.map((r) => [r.member, r.member_label])).entries()];
    const latest = ends.at(-1);
    const total = rs.filter((r) => r.period_end === latest).reduce((s, r) => s + (num(r.value) ?? 0), 0);
    const at = (member: string, end: string | undefined) => rs.find((r) => r.member === member && r.period_end === end);
    const out = members.map(([member, label]) => {
      const cells = ends.map((end): TableCell | null => {
        const r = at(member, end);
        const v = r ? num(r.value) : null;
        if (!r || v === null) return null;
        return {
          text: fmt("usd", v),
          raw: r.value,
          title: `${label}, ${r.fiscal_period ?? day(end, true)}. Source: ${r.citation.form} filed ${day(r.citation.filed, true)}, ` +
            `${r.citation.accession_no}; ${r.value_source}${r.citation.xbrl_axis ? `, ${r.citation.xbrl_axis}` : ""}`,
          derived: r.value_source === "derived",
          url: r.citation.filing_url,
        };
      });
      const last = num(at(member, latest)?.value);
      const yearAgo = ends.length >= 5 ? num(at(member, ends[ends.length - 5])?.value) : null;
      return {
        member,
        label,
        cells,
        share: last !== null && total > 0 && metric === "revenue" ? last / total : null,
        yoy: last !== null && yearAgo ? last / yearAgo - 1 : null,
      };
    });
    out.sort((a, b) => (num(at(b.member, latest)?.value) ?? -Infinity) - (num(at(a.member, latest)?.value) ?? -Infinity));
    return { axis, metric, title: `${SEGMENT_METRICS[metric]} by ${AXIS_LABELS[axis]!.toLowerCase()}`, columns, rows: out };
  });
}

// --- filings -------------------------------------------------------------------------------------

/** 8-K items (Form 8-K General Instructions B), short labels. */
export const EIGHT_K_ITEMS: Record<string, string> = {
  "1.01": "Material agreement",
  "1.02": "Agreement terminated",
  "1.03": "Bankruptcy",
  "1.05": "Cybersecurity incident",
  "2.01": "Acquisition or disposition",
  "2.02": "Results of operations",
  "2.03": "New debt obligation",
  "2.04": "Debt acceleration",
  "2.05": "Exit or restructuring costs",
  "2.06": "Impairment",
  "3.01": "Delisting notice",
  "3.02": "Unregistered equity sale",
  "3.03": "Change to holders' rights",
  "4.01": "Auditor change",
  "4.02": "Non-reliance on prior financials",
  "5.01": "Change in control",
  "5.02": "Officer or director change",
  "5.03": "Bylaws or fiscal year change",
  "5.05": "Code of ethics",
  "5.07": "Shareholder vote",
  "7.01": "Regulation FD disclosure",
  "8.01": "Other events",
  "9.01": "Financial statements and exhibits",
};

/** An 8-K's items from its section keys ("2.02", "EX-99.1"), labelled; 9.01 and exhibits
 * are left out (every 8-K has them). */
export function eightKItems(sectionKeys: string[]): string[] {
  return sectionKeys
    .filter((k) => /^\d\.\d\d$/.test(k) && k !== "9.01")
    .map((k) => `${k} ${EIGHT_K_ITEMS[k] ?? "Item"}`);
}

export type FormFilter = "all" | "10-K" | "10-Q" | "8-K" | "proxy" | "insider";

/** The filing index's form chips: each is a list of exact SEC form types (sent to the API). */
export const FORM_FILTERS: [FormFilter, string, string[]][] = [
  ["all", "All", []],
  ["10-K", "10-K", ["10-K", "10-K/A", "10-KT"]],
  ["10-Q", "10-Q", ["10-Q", "10-Q/A", "10-QT"]],
  ["8-K", "8-K", ["8-K", "8-K/A"]],
  ["proxy", "Proxy", ["DEF 14A", "DEFA14A", "DEFM14A", "PRE 14A"]],
  ["insider", "Insider", ["3", "4", "5", "3/A", "4/A", "5/A"]],
];

export function formsOf(f: FormFilter): string[] {
  return FORM_FILTERS.find(([k]) => k === f)?.[2] ?? [];
}

export function formMatches(form: string, f: FormFilter): boolean {
  return f === "all" || formsOf(f).includes(form);
}

/** The tag class a form gets in tables: k (annual), q (quarterly) or none. */
export function formTag(form: string): string {
  return form.startsWith("10-K") ? "k" : form.startsWith("10-Q") ? "q" : "";
}
