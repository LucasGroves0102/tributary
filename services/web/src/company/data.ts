/** The company page's data shaping: header facts, key stats with their periods, statement and
 * ratio tables (quarterly / TTM / annual) with a source per cell, segment tables, the latest
 * quarter recap, 8-K item labels and CSVs. Pure, tested without rendering. */

import { csvCell } from "../chart/series";
import { calendarLabel } from "../compare/state";
import { FILER_LABELS, fyeMonth, type FilerCategory } from "../companies/screen";
import { BANK_NA, NO_DATA, NO_DATA_TITLE, NOT_APPLICABLE, NOT_MEANINGFUL, NOT_REPORTED, readFigure } from "../blanks";
import { change, day, money, moneyRow, pct } from "../format";
import { num } from "../home/data";
import { EIGHT_K_ITEMS } from "../reader/sections";
import { separateMembers } from "../chart/segments";
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

/** The header's listing line: the main ticker, then how many other classes and notes it has (their
 * tickers in the hover). A bank can have a dozen preferreds. */
export function listingLine(tickers: string[], exchanges: string[]): { text: string; more: string | null } {
  const rest = tickers.slice(1);
  const main = [tickers[0], rest.length ? `+${rest.length} more` : null].filter(Boolean).join(" ");
  return {
    text: [main, exchanges.join(", ")].filter(Boolean).join(" · "),
    more: rest.length ? `Also listed: ${rest.join(", ")}` : null,
  };
}

export interface KeyStat {
  label: string;
  text: string;
  /** the period the value covers, e.g. "TTM to Jul 26, 2026" */
  period: string;
  title: string;
  /** years older than the company's latest period (dimmed) */
  old?: boolean;
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
    const src = row.sources[key] ?? null;
    const f = readFigure({
      label,
      columnKey: key,
      raw: row.values[key],
      reading: row.readings?.[key],
      source: src,
      latestPeriod: row.latest_period,
      format: (v) => fmt(kind, v),
      sourceTitle: src
        ? `${label}, ${periodText(key, src)} (${calendarLabel(src.period_end)}). Source: ${src.form} filed ` +
          `${day(src.filed, true)}, ${src.accession_no}.`
        : label,
    });
    const period = f.status === "not_applicable"
      ? "not applicable"
      : f.status === "not_meaningful"
        ? "not meaningful"
        : !src || f.text === NOT_REPORTED
          ? "not reported"
          : f.old
            ? `old · ${periodText(key, src)}`
            : periodText(key, src);
    return { label, text: f.text, period, title: f.title, old: f.old };
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
/** How far back Quarterly and TTM statements go (StatementView in route.ts's address). */
export type StatementRange = "8q" | "5y" | "10y" | "max";
export const RANGE_QUARTERS: Record<StatementRange, number> = { "8q": 8, "5y": 20, "10y": 40, max: 120 };

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
export function statementRequest(period: Period, range: StatementRange = "8q"): { view: "quarter" | "ttm"; quarters: number } {
  return period === "a" ? { view: "ttm", quarters: 40 } : { view: period === "q" ? "quarter" : "ttm", quarters: RANGE_QUARTERS[range] };
}

export const ANNUAL_YEARS = 10;

export interface TableCell {
  text: string;
  raw: string;
  title: string;
  derived: boolean;
  url: string | null;
}

/** What an empty cell reads (blanks.ts). */
export interface BlankCell {
  text: string;
  title: string;
}

export interface MetricTable {
  columns: { periodEnd: string; label: string; sub: string }[];
  /** `blanks[i]` is what `cells[i]` reads when it's null */
  rows: { metric: string; label: string; cells: (TableCell | null)[]; blanks: BlankCell[] }[];
}

/** Per-share figures: quarters state them, but they don't add (the share count changes), so gold
 * derives neither a TTM nor a fiscal Q4. Annual comes from the 10-K itself (annualPerShare). */
const PER_SHARE = new Set(["eps_diluted", "eps_basic", "weighted_shares_diluted", "weighted_shares_basic"]);
const Q4_PER_SHARE = "not reported. A 10-K gives the year's figure, not its fourth quarter's, and the year less nine " +
  "months isn't the quarter when the share count changes, so it isn't derived as revenue is. Annual shows the year's.";
const TTM_PER_SHARE = "not applicable. Per-share figures don't add across quarters, because the share count changes. " +
  "Annual shows the 10-K's figure for each fiscal year.";
const BANK_TITLE = "not applicable. This is a bank: deposits are its debt and lending its operations, so the figure has no comparable meaning.";
const EQUITY_RATIOS = new Set(["debt_to_equity", "roe"]);

function blankCell(metric: string, name: string, period: Period, column: string, notApplicable: string[]): BlankCell {
  if (notApplicable.includes(metric)) return { text: BANK_NA, title: `${name}: ${BANK_TITLE}` };
  if (PER_SHARE.has(metric) && period === "ttm") return { text: NOT_APPLICABLE, title: `${name}: ${TTM_PER_SHARE}` };
  if (PER_SHARE.has(metric) && period === "q" && column.endsWith("Q4")) return { text: NOT_REPORTED, title: `${name}: ${Q4_PER_SHARE}` };
  return { text: NO_DATA, title: `${name}: ${NO_DATA_TITLE.toLowerCase()}` };
}

/** The 10-K's per-share rows (annualPerShare) as rows of the annual table: each takes the fiscal
 * period of the year it ends, so it lands in that year's column. A year-end within a week counts:
 * gold's Q4 and the 10-K can differ by a day. */
export function withAnnualPerShare(rows: FinancialRow[], annual: FinancialRow[], basis: Basis): FinancialRow[] {
  const ends = rows.filter((r) => ((basis === "calendar" ? r.calendar_period : r.fiscal_period) ?? "").endsWith("Q4"));
  const placed = annual.flatMap((a) => {
    const at = Date.parse(a.period_end);
    const year = ends.find((r) => Math.abs(Date.parse(r.period_end) - at) <= 7 * 86_400_000);
    return year ? [{ ...a, period_end: year.period_end, fiscal_period: year.fiscal_period, calendar_period: year.calendar_period }] : [];
  });
  return [...rows, ...placed];
}

function cellTitle(label: string, r: FinancialRow): string {
  const src = r.citation.xbrl_tag?.startsWith("derived:")
    ? "computed: revenue less cost of revenue, because the filing prints no gross profit"
    : r.value_source === "derived"
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
  /** metrics that don't describe this company, whatever it tags (ScreenerRow.not_applicable) */
  notApplicable: string[] = [],
  /** how many quarterly or TTM columns to keep (annual always shows ANNUAL_YEARS) */
  columnsWanted = RANGE_QUARTERS["8q"],
): MetricTable {
  const periodOf = (r: FinancialRow) => (basis === "calendar" ? r.calendar_period : r.fiscal_period) ?? null;
  const kept = period === "a" ? rows.filter((r) => periodOf(r)?.endsWith("Q4")) : rows;
  let ends = [...new Set(kept.map((r) => r.period_end))].sort();
  ends = ends.slice(-(period === "a" ? ANNUAL_YEARS : columnsWanted));
  const label = (end: string) => {
    const fp = kept.map(periodOf).find((p, i) => p && kept[i]!.period_end === end) ?? null;
    if (period === "a") return fp ? fp.replace(/ Q4$/, "") : `${basis === "calendar" ? "CY" : "FY"} ending ${end.slice(0, 7)}`;
    if (period === "ttm") return `TTM ${fp ?? calendarLabel(end)}`;
    return fp ?? calendarLabel(end);
  };
  const columns = ends.map((end) => ({ periodEnd: end, label: label(end), sub: day(end, true) }));
  const by = new Map(kept.map((r) => [`${r.metric}|${r.period_end}`, r]));
  const equityAt = (end: string) => {
    const r = by.get(`stockholders_equity|${end}`);
    return r ? num(r.value) : null;
  };
  return {
    columns,
    rows: metrics
      .map(([metric, name, kind]) => {
        // dollars read in one unit across a row; the hover has the exact figure
        const row = kind === "usd"
          ? moneyRow(ends.map((end) => {
              const r = by.get(`${metric}|${end}`);
              return r ? num(r.value) : null;
            }))
          : null;
        return {
          metric,
          label: name,
          blanks: ends.map((_, i) => blankCell(metric, name, period, columns[i]!.label, notApplicable)),
          cells: ends.map((end, i): TableCell | null => {
            const r = by.get(`${metric}|${end}`);
            const v = r ? num(r.value) : null;
            // gold has no debt/equity or ROE on negative equity: it isn't a gap, it means nothing
            if (EQUITY_RATIOS.has(metric) && (equityAt(end) ?? 1) <= 0) {
              return {
                text: NOT_MEANINGFUL, raw: v === null ? "" : String(r!.value), derived: false, url: null,
                title: `${name}: not meaningful, because stockholders' equity is negative at ${day(end, true)}.`,
              };
            }
            if (!r || v === null) return null;
            const figure = row?.[i] ?? fmt(kind, v);
            const source = {
              raw: String(r.value),
              derived: r.value_source === "derived",
              url: r.citation.filing_url,
            };
            if (notApplicable.includes(metric)) {
              return { ...source, derived: false, text: BANK_NA, title: `${name}: ${BANK_TITLE} The figure on file: ${figure}. ${cellTitle(name, r)}` };
            }
            if (r.not_meaningful) {
              return { ...source, derived: false, text: NOT_MEANINGFUL, title: `${name}: not meaningful, because ${r.not_meaningful}. The figure on file: ${figure}. ${cellTitle(name, r)}` };
            }
            return { ...source, text: figure, title: cellTitle(name, r) };
          }),
        };
      })
      // an n/a row stays, so its absence is explained: a bank's FCF, EPS in a TTM view
      .filter((r) => r.cells.some(Boolean) || (ends.length > 0 && r.blanks.every((b) => b.text === BANK_NA || b.text === NOT_APPLICABLE))),
  };
}

/** Revenue already net of interest expense: a bank's net revenue tag, or its net interest income plus
 * noninterest income (migration 038). */
function revenueIsNetOfInterest(rows: FinancialRow[]): boolean {
  return rows.some((r) => r.metric === "revenue" && /RevenuesNetOfInterestExpense|InterestIncomeExpenseNet/.test(r.citation.xbrl_tag ?? ""));
}

const valueAt = (end: string, by: Map<string, FinancialRow>, metric: string): number | null => {
  const r = by.get(`${metric}|${end}`);
  return r ? num(r.value) : null;
};

function computedRow(metric: string, label: string, table: MetricTable, value: (end: string) => number | null, title: string): MetricTable["rows"][number] | null {
  const ends = table.columns.map((c) => c.periodEnd);
  const values = ends.map(value);
  if (!values.some((v) => v !== null)) return null;
  const figures = moneyRow(values);
  return {
    metric,
    label,
    blanks: ends.map(() => ({ text: "", title: "" })),
    cells: values.map((v, i) => (v === null ? null : {
      text: figures[i] ?? "", raw: String(v), derived: true, url: null,
      title: `${label}, ${table.columns[i]!.label}: ${title}`,
    })),
  };
}

/** What a statement's own lines can't say, added as computed rows (never a stored figure, so never in
 * the CSV, a chart or the assistant's data). A bank's interest expense says it's already in revenue.
 * An income statement with operating income and net income but no pretax line gets the bridge between
 * them. A balance sheet where assets aren't liabilities plus equity gets the difference. */
export function withBridges(table: MetricTable, rows: FinancialRow[], kind: Statement): MetricTable {
  const by = new Map(rows.map((r) => [`${r.metric}|${r.period_end}`, r]));
  const out = { ...table, rows: [...table.rows] };
  const insertAfter = (metric: string, row: MetricTable["rows"][number] | null) => {
    if (!row) return;
    const i = out.rows.findIndex((r) => r.metric === metric);
    out.rows.splice(i < 0 ? out.rows.length : i + 1, 0, row);
  };
  if (kind === "is") {
    if (revenueIsNetOfInterest(rows)) {
      out.rows = out.rows.map((r) => r.metric === "interest_expense"
        ? { ...r, label: "Interest expense (already taken out of revenue)" } : r);
    }
    insertAfter("interest_expense", computedRow("other_items", "Other items, net", table, (end) => {
      const op = valueAt(end, by, "operating_income");
      const net = valueAt(end, by, "net_income");
      return op !== null && net !== null && valueAt(end, by, "pretax_income") === null ? net - op : null;
    }, "net income less operating income, where the filing tags no pretax income: interest, other income and expense, and any tax, not itemised in its XBRL."));
  }
  if (kind === "bs") {
    insertAfter("stockholders_equity", computedRow("other_equity", "Not itemised", table, (end) => {
      const assets = valueAt(end, by, "total_assets");
      const liabilities = valueAt(end, by, "total_liabilities");
      const equity = valueAt(end, by, "stockholders_equity");
      if (assets === null || liabilities === null || equity === null) return null;
      const gap = assets - liabilities - equity;
      return Math.abs(gap) > 0.01 * Math.abs(assets) ? gap : null;
    }, "assets less liabilities less stockholders' equity. Usually redeemable or mezzanine equity, or noncontrolling interests, which the filing reports outside these lines."));
  }
  return out;
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
        return n === null ? NO_DATA : v?.not_meaningful ? NOT_MEANINGFUL : fmt(kind, n);
      };
      const unusable = m.current?.not_meaningful;
      return {
        label,
        current: show(m.current),
        prior: show(m.prior_quarter),
        qoq: unusable || m.prior_quarter?.not_meaningful ? null : delta(m.prior_quarter, m.unit),
        yearAgo: show(m.year_ago),
        yoy: unusable || m.year_ago?.not_meaningful ? null : delta(m.year_ago, m.unit),
        title: [unusable ? `${label}: not meaningful, because ${unusable}. The figure on file: ${fmt(kind, num(m.current!.value)!)}.` : "",
          src(m.current), src(m.prior_quarter), src(m.year_ago)].filter(Boolean).join("\n"),
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
const SHARE_TOLERANCE = 0.02;

export interface SegmentTable {
  axis: string;
  metric: string;
  title: string;
  columns: { periodEnd: string; label: string }[];
  rows: {
    member: string;
    label: string;
    cells: (TableCell | null)[];
    /** of consolidated revenue when it's known, else of the members' sum (`shareOf` says) */
    share: number | null;
    /** the member is bigger than the consolidated figure: not on the same basis */
    shareTooBig: boolean;
    yoy: number | null;
    /** part of another member, or another cut of the same revenue */
    overlaps: boolean;
  }[];
  /** consolidated: reported revenue. members: their sum, because revenue isn't known for the period.
   * none: neither can be trusted, so no share is printed. */
  shareOf: "consolidated" | "members" | "none";
  /** what the table says about members that overlap, when they do */
  overlapNote: string | null;
}

const listOf = (names: string[], most = 4) =>
  names.length <= most ? names.join(", ") : `${names.slice(0, most).join(", ")} and ${names.length - most} more`;

/** One table per axis and metric (revenue, operating income), members by period, newest last; YoY
 * vs four periods earlier; reconciliation members (corporate, eliminations) left out. A revenue
 * share is the member over consolidated revenue for that period, never over the members' sum: a
 * parent reported beside its children (NVIDIA's Data Center and Hyperscale) or two cuts of the same
 * revenue (Vertiv's Products and Product excluding Spares) would halve everything. Without revenue
 * for the period, it's over the members' sum only if nothing shows they overlap. */
export function segmentTables(rows: SegmentRow[], revenue: FinancialRow[] = []): SegmentTable[] {
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
  // the same quarter: gold's period end and the segment fact's can be a day or two apart
  const revenueRows = revenue.filter((r) => r.metric === "revenue");
  const consolidatedAt = (end: string): number | null => {
    const at = Date.parse(end);
    const near = revenueRows.find((r) => Math.abs(Date.parse(r.period_end) - at) <= 7 * 86_400_000);
    return near ? num(near.value) : null;
  };
  return [...groups.entries()].sort(([a], [b]) => order(a) - order(b)).map(([k, rs]) => {
    const [axis, metric] = k.split("|") as [string, string];
    const ends = [...new Set(rs.map((r) => r.period_end))].sort().slice(-8);
    const columns = ends.map((e) => ({ periodEnd: e, label: rs.find((r) => r.period_end === e && r.fiscal_period)?.fiscal_period ?? calendarLabel(e) }));
    const members = [...new Map(rs.map((r) => [r.member, r.member_label])).entries()];
    const latest = ends.at(-1);
    const at = (member: string, end: string | undefined) => rs.find((r) => r.member === member && r.period_end === end);
    // which members overlap, over the table's periods
    const periods = new Map(ends.map((e, i) => [i, new Map(rs.filter((r) => r.period_end === e && num(r.value) !== null).map((r) => [r.member, num(r.value)!]))]));
    const totals = new Map(ends.flatMap((e, i): [number, number][] => {
      const v = consolidatedAt(e);
      return v ? [[i, v]] : [];
    }));
    const separated = metric === "revenue" ? separateMembers(periods, totals) : null;
    const latestIndex = ends.length - 1;
    const total = latest === undefined ? null : consolidatedAt(latest);
    const memberSum = rs.filter((r) => r.period_end === latest).reduce((s, r) => s + (num(r.value) ?? 0), 0);
    const shareOf: SegmentTable["shareOf"] = metric !== "revenue"
      ? "none"
      : total !== null && total > 0
        ? "consolidated"
        : separated && separated.drop.size === 0 && !separated.unreliable.has(latestIndex)
          ? "members"
          : "none";
    const base = shareOf === "consolidated" ? total! : memberSum;
    const out = members.map(([member, label]) => {
      const texts = moneyRow(ends.map((end) => {
        const r = at(member, end);
        return r ? num(r.value) : null;
      }));
      const cells = ends.map((end, i): TableCell | null => {
        const r = at(member, end);
        const v = r ? num(r.value) : null;
        if (!r || v === null) return null;
        return {
          text: texts[i] ?? fmt("usd", v),
          raw: r.value,
          title: `${label}, ${r.fiscal_period ?? day(end, true)}. Source: ${r.citation.form} filed ${day(r.citation.filed, true)}, ` +
            `${r.citation.accession_no}; ${r.value_source}${r.citation.xbrl_axis ? `, ${r.citation.xbrl_axis}` : ""}`,
          derived: r.value_source === "derived",
          url: r.citation.filing_url,
        };
      });
      const last = num(at(member, latest)?.value);
      const yearAgo = ends.length >= 5 ? num(at(member, ends[ends.length - 5])?.value) : null;
      const share = last !== null && shareOf !== "none" && base > 0 ? last / base : null;
      return {
        member,
        label,
        cells,
        share,
        shareTooBig: share !== null && share > 1 + SHARE_TOLERANCE,
        yoy: last !== null && yearAgo ? last / yearAgo - 1 : null,
        overlaps: separated?.drop.has(member) ?? false,
      };
    });
    out.sort((a, b) => (num(at(b.member, latest)?.value) ?? -Infinity) - (num(at(a.member, latest)?.value) ?? -Infinity));
    const parts = out.filter((r) => r.overlaps).map((r) => r.label);
    const overlapNote = parts.length
      ? `${listOf(parts)} ${parts.length === 1 ? "is" : "are"} part of another line here, or another cut of the same revenue, so the lines add up to more than revenue. Each share is of consolidated revenue.`
      : separated?.unreliable.has(latestIndex)
        ? "These lines add up to more than revenue and can't be separated: some overlap, or include sales between segments. Each share is of consolidated revenue, so don't add them up."
        : null;
    return { axis, metric, title: `${SEGMENT_METRICS[metric]} by ${AXIS_LABELS[axis]!.toLowerCase()}`, columns, rows: out, shareOf, overlapNote };
  });
}

// --- filings -------------------------------------------------------------------------------------

/** An 8-K's items from its section keys ("2.02", "EX-99.1"), labelled; 9.01 and exhibits
 * are left out (every 8-K has them). */
export function eightKItems(sectionKeys: string[]): string[] {
  return sectionKeys
    .filter((k) => /^\d\.\d\d$/.test(k) && k !== "9.01")
    .map((k) => `${k} ${EIGHT_K_ITEMS[k] ?? "Item"}`);
}

export type FormFilter = "reports" | "all" | "10-K" | "10-Q" | "8-K" | "proxy" | "insider";

/** The filing index's form chips: each is a list of exact SEC form types (sent to the API).
 * Reports first: 57 of a company's 1,233 filings can be 10-K/Qs among the insider forms. */
export const FORM_FILTERS: [FormFilter, string, string[]][] = [
  ["reports", "Reports", ["10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT", "8-K", "8-K/A"]],
  ["10-K", "10-K", ["10-K", "10-K/A", "10-KT"]],
  ["10-Q", "10-Q", ["10-Q", "10-Q/A", "10-QT"]],
  ["8-K", "8-K", ["8-K", "8-K/A"]],
  ["proxy", "Proxy", ["DEF 14A", "DEFA14A", "DEFM14A", "PRE 14A"]],
  ["insider", "Insider", ["3", "4", "5", "3/A", "4/A", "5/A"]],
  ["all", "All", []],
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

/** gold.fiscal_year_end's rule (migration 014) and gold's label (the year of that date less 7
 * days), so a filing's year here reads like the FY2027 Q2 on every other tab. */
export function fiscalYear(periodEnd: string, mmdd: string): number {
  const shifted = new Date(`${periodEnd}T00:00:00Z`);
  shifted.setUTCDate(shifted.getUTCDate() - 10);
  const month = Number(mmdd.slice(0, 2));
  const dayOf = Number(mmdd.slice(2));
  const nominal = (year: number) => Date.UTC(year, month - 1, 1) + (dayOf - 1) * 86_400_000;
  const year = shifted.getUTCFullYear();
  const end = nominal(year) >= shifted.getTime() ? nominal(year) : nominal(year + 1);
  return new Date(end - 7 * 86_400_000).getUTCFullYear();
}

/** The fiscal year a filing belongs to, or null without a known fiscal year end. A report goes by
 * its period; a proxy's or Form 4's "period" isn't a reporting period, so those go by the date filed. */
function filingFiscalYear(f: { form: string; filed: string; period_of_report: string | null }, fye: string | null): number | null {
  if (!fye || !/^\d{4}$/.test(fye)) return null;
  const reports = /^(10-[KQ]|8-K)/.test(f.form);
  return fiscalYear(reports && f.period_of_report ? f.period_of_report : f.filed, fye);
}

/** The heading each row of a newest-first page starts, or null when it continues the one above:
 * fiscal years, or the year filed without a fiscal year end. Filing dates and fiscal years don't
 * run in step (a 10-K for FY2026 is filed in FY2027, beside that quarter's 8-Ks), so the heading
 * only ever moves back in time: the page reads FY2027, FY2026, never FY2027 again. */
export function filingHeadings(rows: { form: string; filed: string; period_of_report: string | null }[], fye: string | null): (string | null)[] {
  let current: number | null = null;
  return rows.map((f) => {
    const year = filingFiscalYear(f, fye);
    const y = year ?? Number(f.filed.slice(0, 4));
    if (current !== null && y >= current) return null;
    current = y;
    return year === null ? `Filed in ${y}` : `FY${y}`;
  });
}

/** Whether the parsed-text note applies: only reports are ever parsed, so saying "not parsed" on
 * every Form 4 or 144 is noise, while a 10-Q not parsed yet is worth saying. */
export function parsedNote(f: { form: string; sections: number | null | undefined }): string | null {
  if (f.sections) return `${f.sections} section${f.sections === 1 ? "" : "s"}`;
  if (!/^(10-[KQ]|8-K)/.test(f.form)) return null;
  return f.sections === 0 ? "not parsed yet" : "index only";
}
