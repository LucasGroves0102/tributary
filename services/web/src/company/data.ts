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
import type { Callout, FinancialRow, NotMeaningfulPeriod, Recap, RecapValue, ScreenerRow, SegmentRow, ValueSource } from "../types";

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

/** A bank's key stats (F4, #103; migration 048): what a bank is compared on. Net interest margin,
 * tangible book value per share and ROTCE are left out: no standard tag carries their inputs
 * quarterly (048's header; BANK_NOT_SHOWN says so on the page). */
export const BANK_KEY_STATS: { key: string; label: string; kind: Fmt }[] = [
  { key: "revenue_ttm", label: "Net revenue", kind: "usd" },
  { key: "net_interest_income_ttm", label: "Net interest income", kind: "usd" },
  { key: "efficiency_ratio_ttm", label: "Efficiency ratio", kind: "pct" },
  { key: "provision_for_credit_losses_ttm", label: "Provision for credit losses", kind: "usd" },
  { key: "net_income_ttm", label: "Net income", kind: "usd" },
  { key: "eps_diluted_q", label: "EPS (diluted)", kind: "eps" },
  { key: "loans_q", label: "Loans, net", kind: "usd" },
  { key: "deposits_q", label: "Deposits", kind: "usd" },
  { key: "cet1_ratio_q", label: "CET1 ratio", kind: "pct" },
  { key: "roe_ttm", label: "Return on equity", kind: "pct" },
  { key: "revenue_growth_ttm", label: "Revenue growth", kind: "growth" },
  { key: "net_margin_ttm", label: "Net margin", kind: "pct" },
];

/** What a bank's page says about the bank figures it doesn't show. */
export const BANK_NOT_SHOWN =
  "Not shown: net interest margin (it needs average interest-earning assets, which banks don't tag " +
  "in standard XBRL), and tangible book value per share and ROTCE (the largest banks tag goodwill " +
  "every quarter but other intangible assets and preferred stock only in the 10-K or under their own " +
  "tags, so a quarterly figure would mix definitions). Efficiency ratio = noninterest expense / (net " +
  "interest income + noninterest income), lower is leaner.";

/** A bank by migration 035's rule: the API marks the metrics that don't describe it. */
export const isBankRow = (row: Pick<ScreenerRow, "not_applicable">): boolean => !!row.not_applicable?.length;

/** Asked for with the key stats: the latest quarter's revenue names the filing to recap. */
export const RECAP_COLUMN = "revenue_q";
export const HEADER_COLUMNS = [...KEY_STATS.map((s) => s.key), RECAP_COLUMN];
/** The header's columns with a bank's own (an older API refuses them: ask again without). */
export const HEADER_COLUMNS_WITH_BANK = [...new Set([...HEADER_COLUMNS, ...BANK_KEY_STATS.map((s) => s.key)])];

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
  /** the screener column (KEY_STATS) */
  key: string;
  /** the period the figure is as of (its source's period end), when it has one */
  periodEnd: string | null;
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
  // a bank's own set when the answer has its columns (an older API didn't send them)
  const stats = isBankRow(row) && "net_interest_income_ttm" in row.values ? BANK_KEY_STATS : KEY_STATS;
  return stats.map(({ key, label, kind }) => {
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
    return { key, periodEnd: src?.period_end ?? null, label, text: f.text, period, title: f.title, old: f.old };
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

/** How a statement line reads (F5): `sub` an item of the subtotal it sits with (indented), `total` a
 * subtotal, `outflow` a payment the filing tags as the positive amount paid, shown negative as the cash
 * flow statement prints it (the CSV too). `define` is the label's hover: what the line is. */
export interface LineOpts {
  sub?: boolean;
  total?: boolean;
  outflow?: boolean;
  define?: string;
}
export type Line = [string, string, Fmt, LineOpts?];

/** What the lines migration 049 added are (their hovers; README "Statements"). */
export const DEFINITIONS: Record<string, string> = {
  cash_and_investments:
    "Cash and short-term investments, read as net debt reads them: investments already inside cash aren't added twice, and there's no figure when they're missing at this date but were reported lately. Open it to see the tags behind it.",
  accounts_payable: "What the company owes its suppliers, due within a year.",
  contract_liabilities_current:
    "Customer advances and billings ahead of revenue, due within a year (contract liabilities; filers that kept the older name tag it as deferred revenue).",
  contract_liabilities: "All contract liabilities, current and noncurrent: the filing's total, or its two lines added up.",
  remaining_performance_obligations:
    "Revenue under contract not yet recognised (remaining performance obligations, from the revenue note): the nearest tagged figure to a backlog. Not a balance-sheet line. Many companies don't tag it, and backlog in MD&A text isn't here.",
  investing_cash_flow: "Net cash from investing activities, as the filing totals it: capex, acquisitions, and buying and selling investments.",
  financing_cash_flow: "Net cash from financing activities, as the filing totals it: borrowing and repayment, dividends, buybacks, shares issued.",
  change_in_cash: "Net change in cash, cash equivalents and restricted cash, after exchange-rate effects, as the cash flow statement totals it.",
  cash_and_restricted_cash:
    "Cash, cash equivalents and restricted cash at the period's end: where the cash flow statement ends. The balance sheet's cash can leave restricted cash out.",
};

const OUTFLOW = "outflow: shown negative, as the cash flow statement prints it";

export const STATEMENTS: Record<Statement, { label: string; rows: Line[] }> = {
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
      ["cash_and_investments", "Cash and short-term investments", "usd", { total: true, define: DEFINITIONS.cash_and_investments }],
      ["receivables", "Receivables", "usd"],
      ["inventory", "Inventory", "usd"],
      ["current_assets", "Current assets", "usd"],
      ["total_assets", "Total assets", "usd"],
      ["current_liabilities", "Current liabilities", "usd", { total: true }],
      ["accounts_payable", "Accounts payable", "usd", { sub: true, define: DEFINITIONS.accounts_payable }],
      ["contract_liabilities_current", "Contract liabilities (customer advances)", "usd", { sub: true, define: DEFINITIONS.contract_liabilities_current }],
      ["long_term_debt_current", "Debt due within a year", "usd", { sub: true }],
      ["long_term_debt", "Long-term debt", "usd"],
      ["contract_liabilities", "Contract liabilities, current and noncurrent", "usd", { define: DEFINITIONS.contract_liabilities }],
      ["total_liabilities", "Total liabilities", "usd"],
      ["stockholders_equity", "Stockholders' equity", "usd"],
      ["net_debt", "Net debt", "usd"],
      ["remaining_performance_obligations", "Remaining performance obligations (backlog; not on the balance sheet)", "usd", { define: DEFINITIONS.remaining_performance_obligations }],
    ],
  },
  cf: {
    label: "Cash flow",
    rows: [
      ["depreciation_amortization", "Depreciation and amortization (added back)", "usd", { sub: true }],
      ["stock_based_compensation", "Stock-based compensation (added back)", "usd", { sub: true }],
      ["operating_cash_flow", "Net cash from operating activities", "usd", { total: true }],
      ["capex", "Capital expenditure", "usd", { sub: true, outflow: true }],
      ["investing_cash_flow", "Net cash from investing activities", "usd", { total: true, define: DEFINITIONS.investing_cash_flow }],
      ["dividends_paid", "Dividends paid", "usd", { sub: true, outflow: true }],
      ["share_buybacks", "Share buybacks", "usd", { sub: true, outflow: true }],
      ["financing_cash_flow", "Net cash from financing activities", "usd", { total: true, define: DEFINITIONS.financing_cash_flow }],
      ["change_in_cash", "Change in cash", "usd", { total: true, define: DEFINITIONS.change_in_cash }],
      ["cash_and_restricted_cash", "Cash at end of period", "usd", { define: DEFINITIONS.cash_and_restricted_cash }],
      ["free_cash_flow", "Free cash flow (operating cash flow less capex)", "usd"],
    ],
  },
};

/** The metrics migration 049 added: an older API refuses them (422), so a statement asks again without
 * them (the Production fallback, services/web README). */
export const F5_METRICS = new Set(["cash_and_investments", "accounts_payable", "contract_liabilities_current",
  "contract_liabilities", "remaining_performance_obligations", "investing_cash_flow", "financing_cash_flow",
  "change_in_cash", "cash_and_restricted_cash"]);

/** A bank's statements (F4, migration 048): its own lines instead of the industrial ones that read
 * n/a for a bank. Cash flow is the same statement. Revenue is net revenue (net interest income plus
 * noninterest income: a bank's net revenue tag, or 038's sum). */
export const BANK_STATEMENTS: Record<Statement, { label: string; rows: Line[] }> = {
  is: {
    label: "Income statement",
    rows: [
      ["net_interest_income", "Net interest income", "usd"],
      ["noninterest_income", "Noninterest income", "usd"],
      ["revenue", "Net revenue", "usd"],
      ["noninterest_expense", "Noninterest expense", "usd"],
      ["provision_for_credit_losses", "Provision for credit losses", "usd"],
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
      ["cash", "Cash and due from banks", "usd"],
      ["loans", "Loans, net of the allowance", "usd"],
      ["total_assets", "Total assets", "usd"],
      ["deposits", "Deposits", "usd"],
      ["total_liabilities", "Total liabilities", "usd"],
      ["stockholders_equity", "Stockholders' equity", "usd"],
      ["cet1_ratio", "CET1 capital ratio (where tagged)", "pct"],
    ],
  },
  cf: STATEMENTS.cf,
};

export const RATIOS: Line[] = [
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
export function statementRequest(period: Period, range: StatementRange = "8q", kind: Statement = "is"): { view: "quarter" | "ttm"; quarters: number } {
  const req: { view: "quarter" | "ttm"; quarters: number } = period === "a"
    ? { view: "ttm", quarters: 40 }
    : { view: period === "q" ? "quarter" : "ttm", quarters: RANGE_QUARTERS[range] };
  // the cash flow statement's first column starts with the cash at the end of the period before it
  return kind === "cf" ? { ...req, quarters: Math.min(120, req.quarters + 4) } : req;
}

export const ANNUAL_YEARS = 10;

export interface TableCell {
  text: string;
  raw: string;
  title: string;
  derived: boolean;
  /** a quarter called out by the API (company/callouts.ts words it) */
  callout?: Callout;
  /** what the figure is made of can be opened (F5): a derived figure, a computed one, a sum of tags */
  opens?: boolean;
  /** shown by a style: a reconciliation that ties, or doesn't */
  tone?: "ok" | "warn";
  /** a derived figure's inputs (gold's `inputs`): the opened row's lines read them */
  inputs?: Record<string, number>;
  /** the filing on EDGAR (the secondary link) */
  url: string | null;
  /** the filing the value comes from, opened in the reader (the value's own link) */
  source?: { accessionNo: string; form: string };
}

/** A 10-K or 10-Q in the filing index whose period isn't in the figures yet (H9). */
export interface FiguresBehind {
  form: string;
  accessionNo: string;
  filed: string;
  /** the filing's period */
  period: string;
  /** the newest period the figures have */
  through: string;
  /** whole days since it was filed */
  days: number;
  /** quarters between the figures' newest period and the filing's */
  quarters: number;
}

/** The figures are behind when the newest 10-K/10-Q in the index reports a period more than a week
 * after their newest one and was filed after every filing they cite. Says how far, never when it
 * will catch up: the SEC's XBRL data has no deadline (Humana's July 10-Q stayed out for months). */
export function figuresBehind(
  rows: FinancialRow[],
  newest: { form: string; accession_no: string; filed: string; period_of_report?: string | null } | null,
  now: number = Date.now(),
): FiguresBehind | null {
  if (!newest?.period_of_report || !/^10-[KQ]$/.test(newest.form) || !rows.length) return null;
  const through = rows.reduce((m, r) => (r.period_end > m ? r.period_end : m), "");
  const cited = rows.reduce((m, r) => (r.citation.filed > m ? r.citation.filed : m), "");
  if (rows.some((r) => r.citation.accession_no === newest.accession_no)) return null;
  if (Date.parse(newest.period_of_report) - Date.parse(through) <= 7 * 86_400_000 || newest.filed < cited) return null;
  return {
    form: newest.form, accessionNo: newest.accession_no, filed: newest.filed, period: newest.period_of_report, through,
    days: Math.max(0, Math.floor((now - Date.parse(newest.filed.slice(0, 10))) / 86_400_000)),
    quarters: Math.max(1, Math.round((Date.parse(newest.period_of_report) - Date.parse(through)) / (91.3 * 86_400_000))),
  };
}

/** Where a statement's XBRL value is printed: a 10-K's Item 8, a 10-Q's Part I Item 1 (the
 * reader's section keys); other forms open at their first section. */
export function statementSection(form: string): string | null {
  if (/^10-K/.test(form)) return "8";
  if (/^10-Q/.test(form)) return "I-1";
  return null;
}

/** What an empty cell reads (blanks.ts). */
export interface BlankCell {
  text: string;
  title: string;
}

export interface MetricTable {
  columns: { periodEnd: string; label: string; sub: string }[];
  /** `blanks[i]` is what `cells[i]` reads when it's null */
  rows: { metric: string; label: string; cells: (TableCell | null)[]; blanks: BlankCell[]; opts?: LineOpts }[];
}

/** Per-share figures: quarters state them, but they don't add (the share count changes), so gold
 * derives neither a TTM nor a fiscal Q4. Annual comes from the 10-K itself (annualPerShare). */
const PER_SHARE = new Set(["eps_diluted", "eps_basic", "weighted_shares_diluted", "weighted_shares_basic"]);
const Q4_PER_SHARE = "not reported. A 10-K gives the year's figure, not its fourth quarter's, and the year less nine " +
  "months isn't the quarter when the share count changes, so it isn't derived as revenue is. Annual shows the year's.";
const TTM_PER_SHARE = "not applicable. Per-share figures don't add across quarters, because the share count changes. " +
  "Annual shows the 10-K's figure for each fiscal year.";
const BANK_TITLE = "not applicable. This is a bank: deposits are its debt and lending its operations, so the figure has no comparable meaning.";

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

/** Whether a row's figure opens into its parts (F5, /components): a derived figure (its `inputs`), gross
 * profit computed as revenue less cost, or a line the filing tags as parts (a sum). A TTM's quarters
 * don't count: every TTM is that. */
export function opensIntoParts(r: FinancialRow): boolean {
  const tag = r.citation.xbrl_tag ?? "";
  return (!!r.inputs && !("quarters" in r.inputs)) || tag.startsWith("derived:") || tag.includes("+");
}

function numericInputs(inputs: NonNullable<FinancialRow["inputs"]>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(inputs)) {
    const n = typeof v === "number" || typeof v === "string" ? Number(v) : NaN;
    if (Number.isFinite(n)) out[k] = n;
  }
  return out;
}

/** "-6047000000" for "6047000000": an outflow's figure as the statement shows it (exact, as text). */
export const negated = (raw: string): string => (raw.startsWith("-") ? raw.slice(1) : /^0(\.0*)?$/.test(raw) ? raw : `-${raw}`);

/** Rows (one metric per row, its values by period) for the metrics given; metrics without any
 * value are left out. Columns newest last, as statements read. */
export function metricTable(
  rows: FinancialRow[],
  metrics: Line[],
  period: Period,
  basis: Basis = "fiscal",
  /** metrics that don't describe this company, whatever it tags (ScreenerRow.not_applicable) */
  notApplicable: string[] = [],
  /** how many quarterly or TTM columns to keep (annual always shows ANNUAL_YEARS) */
  columnsWanted = RANGE_QUARTERS["8q"],
  /** the API's `not_meaningful_periods`: why a period a metric has no row for reads n/m (debt/equity
   * and ROE on negative equity, migration 035). Never worked out here; an older API sends none. */
  meaningless: NotMeaningfulPeriod[] = [],
  /** the API's callouts (F5): a cell they name carries one; quarterly tables only */
  callouts: Callout[] = [],
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
  const why = new Map(meaningless.map((p) => [`${p.metric}|${p.period_end}`, p.reason]));
  const called = new Map(period === "q" ? callouts.map((c) => [`${c.metric}|${c.period_end}`, c]) : []);
  return {
    columns,
    rows: metrics
      .map(([metric, name, kind, opts]) => {
        const sign = opts?.outflow ? -1 : 1;
        // dollars read in one unit across a row; the hover has the exact figure
        const row = kind === "usd"
          ? moneyRow(ends.map((end) => {
              const r = by.get(`${metric}|${end}`);
              const v = r ? num(r.value) : null;
              return v === null ? null : sign * v;
            }))
          : null;
        return {
          metric,
          label: name,
          ...(opts ? { opts } : {}),
          blanks: ends.map((_, i) => blankCell(metric, name, period, columns[i]!.label, notApplicable)),
          cells: ends.map((end, i): TableCell | null => {
            const r = by.get(`${metric}|${end}`);
            const v = r ? num(r.value) : null;
            // no row because the figure means nothing there (the API says why): not a gap
            const reason = why.get(`${metric}|${end}`);
            if (reason && (!r || v === null)) {
              return {
                text: NOT_MEANINGFUL, raw: "", derived: false, url: null,
                title: `${name}: not meaningful, because ${reason} at ${day(end, true)}.`,
              };
            }
            if (!r || v === null) return null;
            const figure = row?.[i] ?? fmt(kind, sign * v);
            const source = {
              raw: sign < 0 ? negated(String(r.value)) : String(r.value),
              derived: r.value_source === "derived",
              url: r.citation.filing_url,
              source: { accessionNo: r.citation.accession_no, form: r.citation.form },
              ...(opensIntoParts(r) ? { opens: true } : {}),
              ...(r.inputs && !("quarters" in r.inputs) ? { inputs: numericInputs(r.inputs) } : {}),
            };
            if (notApplicable.includes(metric)) {
              return { ...source, derived: false, text: BANK_NA, title: `${name}: ${BANK_TITLE} The figure on file: ${figure}. ${cellTitle(name, r)}` };
            }
            if (r.not_meaningful) {
              return { ...source, derived: false, text: NOT_MEANINGFUL, title: `${name}: not meaningful, because ${r.not_meaningful}. The figure on file: ${figure}. ${cellTitle(name, r)}` };
            }
            const callout = called.get(`${metric}|${end}`);
            const outflow = sign < 0 ? ` Shown negative: an ${OUTFLOW}; the filing tags the amount paid, ${fmt(kind, v)}.` : "";
            return { ...source, text: figure, title: cellTitle(name, r) + outflow, ...(callout ? { callout } : {}) };
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
  if (kind === "cf") cashBridges(table, rows, by, insertAfter);
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

/** The cash flow statement's reconciliation (F5, #111): beginning cash + change in cash = ending cash, within
 * TIE_SHARE of the larger cash figure or TIE_FLOOR dollars (filers round each year to date). Measured on 613
 * companies rebuilt with migration 049: 15,620 of 16,062 quarters since 2019 tie, 442 at 112 companies don't
 * (a scope change: cash held for sale or restricted cash on one side only, an exchange-rate line tagged
 * apart, a restated quarter). */
export const TIE_SHARE = 0.005;
export const TIE_FLOOR = 100_000;
const DAY_MS = 86_400_000;

export function ties(beginning: number, change: number, ending: number): boolean {
  return Math.abs(beginning + change - ending) <= Math.max(TIE_SHARE * Math.max(Math.abs(beginning), Math.abs(ending)), TIE_FLOOR);
}

/** What the cash flow statement's own lines can't say, as computed rows: the subtotals' remainder
 * ("exchange rates and other"), the cash at the start of each period (the ending cash of the period
 * before), and whether start + change = end. Never stored figures: not in the CSV, charts or the
 * assistant's data. */
function cashBridges(table: MetricTable, rows: FinancialRow[], by: Map<string, FinancialRow>,
  insertAfter: (metric: string, row: MetricTable["rows"][number] | null) => void) {
  const ending = rows.filter((r) => r.metric === "cash_and_restricted_cash");
  const startOf = (end: string) => (by.get(`change_in_cash|${end}`) ?? by.get(`operating_cash_flow|${end}`))?.period_start ?? null;
  const beginning = (end: string): number | null => {
    const start = startOf(end);
    if (!start) return null;
    const before = Date.parse(start) - DAY_MS;
    const r = ending.find((x) => Math.abs(Date.parse(x.period_end) - before) <= 7 * DAY_MS);
    return r ? num(r.value) : null;
  };
  const remainder = (end: string): number | null => {
    const [change, op, inv, fin] = ["change_in_cash", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow"]
      .map((m) => valueAt(end, by, m));
    return change === null || op === null || inv === null || fin === null ? null : change - op - inv - fin;
  };
  const ends = table.columns.map((c) => c.periodEnd);
  const material = ends.some((end) => {
    const x = remainder(end);
    const change = valueAt(end, by, "change_in_cash");
    return x !== null && change !== null && Math.abs(x) > Math.max(TIE_SHARE * Math.abs(change), TIE_FLOOR);
  });
  if (material) {
    insertAfter("financing_cash_flow", computedRow("fx_and_other", "Exchange rates and other", table, remainder,
      "the change in cash less the three subtotals: the effect of exchange rates on cash, and any cash flows the filing reports apart from the three (discontinued operations)."));
  }
  insertAfter("change_in_cash", computedRow("cash_beginning", "Cash at beginning of period", table, beginning,
    "the cash at the end of the period before (with restricted cash), where the cash flow statement starts."));
  const checks = ends.map((end) => {
    const b = beginning(end);
    const c = valueAt(end, by, "change_in_cash");
    const e = valueAt(end, by, "cash_and_restricted_cash");
    return b === null || c === null || e === null ? null : { b, c, e, ok: ties(b, c, e) };
  });
  if (!checks.some(Boolean)) return;
  const label = "Beginning + change = end?";
  insertAfter("cash_and_restricted_cash", {
    metric: "cash_reconciliation",
    label,
    opts: { define: "Whether the cash at the start of the period plus the change in cash equals the cash at its end, as a cash flow statement must. Where it doesn't, the figures are as filed: one side usually covers more (cash held for sale, restricted cash) or a quarter was restated." },
    blanks: ends.map(() => ({ text: "", title: `${label}: needs the beginning and ending cash and the change in cash for the period.` })),
    cells: checks.map((x, i): TableCell | null => {
      if (!x) return null;
      const period = table.columns[i]!.label;
      const sum = `${money(x.b, "USD")} + ${money(x.c, "USD")} = ${money(x.b + x.c, "USD")}`;
      if (x.ok) {
        return { text: "✓ ties", raw: "", derived: true, url: null, tone: "ok",
          title: `${period}: beginning cash ${sum}, the ending cash (${money(x.e, "USD")}). It ties.` };
      }
      const gap = x.b + x.c - x.e;
      return { text: `off ${money(Math.abs(gap), "USD")}`, raw: String(gap), derived: true, url: null, tone: "warn",
        title: `${period}: beginning cash ${sum}, but the ending cash is ${money(x.e, "USD")}, ${money(Math.abs(gap), "USD")} ${gap > 0 ? "less" : "more"}. ` +
          "The figures are as filed: usually one side covers more than the other (cash held for sale, restricted cash) or a quarter was restated. The filing's cash flow statement shows its own reconciliation." };
    }),
  });
}

/** Long-format CSV of the rows behind a table: one line per value with its source. */
export function financialsCsv(rows: FinancialRow[], table: MetricTable, view: string): string {
  const want = new Set(table.rows.map((r) => r.metric));
  // the table's sign convention: an outflow line is negative here too
  const outflows = new Set(table.rows.filter((r) => r.opts?.outflow).map((r) => r.metric));
  const ends = new Set(table.columns.map((c) => c.periodEnd));
  const header = ["metric", "view", "fiscal_period", "calendar_period", "period_start", "period_end", "value",
    "unit", "value_source", "accession_no", "form", "filed", "xbrl_tag", "source_accessions"];
  const lines = rows
    .filter((r) => want.has(r.metric) && ends.has(r.period_end))
    .sort((a, b) => a.metric.localeCompare(b.metric) || a.period_end.localeCompare(b.period_end))
    .map((r) => [r.metric, view, r.fiscal_period ?? "", r.calendar_period ?? "", r.period_start ?? "", r.period_end,
      outflows.has(r.metric) ? negated(String(r.value)) : String(r.value), r.unit, r.value_source, r.citation.accession_no, r.citation.form, r.citation.filed,
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
 * quarter; column labels from the values' fiscal periods. `only`: these metrics, in RECAP_LABELS
 * order (the Earnings view's income-statement lines). */
export function recapRows(recap: Recap, only?: readonly string[]): { labels: [string, string, string]; rows: RecapRow[] } {
  const quarter = recap.metrics.filter((m) => m.span === "quarter" && RECAP_LABELS[m.metric] && (!only || only.includes(m.metric)));
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
const SEGMENT_METRICS: Record<string, string> = { revenue: "Revenue", gross_profit: "Gross profit", operating_income: "Operating income" };
const SHARE_TOLERANCE = 0.02;
const DAY = 86_400_000;
/** a table whose newest quarter is this far behind the company's newest segment figure is stale */
const STALE_DAYS = 100;
/** a year-ago quarter is the one ending within this many days of a year before */
const YEAR_AGO_SLACK_DAYS = 20;

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
    /** the year-ago quarter was a loss or zero: no growth rate (n/m) */
    yoyNotMeaningful: boolean;
    /** part of another member, or another cut of the same revenue */
    overlaps: boolean;
    /** the member's last period, when it stopped before the table's newest ("FY2025 Q3"): an old
     * segmentation, listed after the current members */
    until: string | null;
  }[];
  /** consolidated: reported revenue. members: their sum, because revenue isn't known for the period.
   * none: neither can be trusted, so no share is printed. */
  shareOf: "consolidated" | "members" | "none";
  /** what the table says about members that overlap, when they do */
  overlapNote: string | null;
  /** the table ends well before the company's newest segment figures: it isn't current */
  staleNote: string | null;
  /** members that stopped reporting, said once under the table */
  retiredNote: string | null;
}

const listOf = (names: string[], most = 4) =>
  names.length <= most ? names.join(", ") : `${names.slice(0, most).join(", ")} and ${names.length - most} more`;

/** One table per axis and metric (revenue, gross profit, operating income), members by period, newest
 * last; reconciliation members (corporate, eliminations) left out. A revenue share is the member over
 * consolidated revenue for that period, never over the members' sum: a parent reported beside its
 * children (NVIDIA's Data Center and Hyperscale) or two cuts of the same revenue (Vertiv's Products
 * and Product excluding Spares) would halve everything. Without revenue for the period, it's over the
 * members' sum only if nothing shows they overlap.
 *
 * What's current (M8): YoY is the newest quarter against the one ending a year earlier (by date, so a
 * missing quarter in between doesn't hide it). A member with no value in the table's quarters isn't
 * listed (no empty rows); one that stopped before the newest quarter (a company that reorganised its
 * segments: Deckers' UGG Wholesale became UGG Brand in 2025) reads "until FY2025 Q3" and is listed
 * after the current ones. A table whose newest quarter is over STALE_DAYS behind the company's newest
 * segment figure says so: Deckers has reported gross profit, not operating income, by segment since
 * 2025, so its operating income table ends at December 2024. */
export function segmentTables(rows: SegmentRow[], revenue: FinancialRow[] = []): SegmentTable[] {
  const groups = new Map<string, SegmentRow[]>();
  let newest = "";
  for (const r of rows) {
    if (!AXIS_LABELS[r.axis] || !SEGMENT_METRICS[r.metric]) continue;
    const k = `${r.axis}|${r.metric}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
    if (r.period_end > newest) newest = r.period_end;
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
    const latest = ends.at(-1);
    const at = (member: string, end: string | undefined) => rs.find((r) => r.member === member && r.period_end === end);
    const valued = (member: string, end: string) => num(at(member, end)?.value) !== null;
    // only members with a value in the table's quarters: an earlier segmentation's are out of the window
    const members = [...new Map(rs.map((r) => [r.member, r.member_label])).entries()]
      .filter(([member]) => ends.some((e) => valued(member, e)));
    const lastOf = (member: string) => ends.filter((e) => valued(member, e)).at(-1)!;
    const yearAgoOf = (member: string): number | null => {
      if (!latest) return null;
      const want = Date.parse(latest) - 365 * DAY;
      const r = rs.find((x) => x.member === member && Math.abs(Date.parse(x.period_end) - want) <= YEAR_AGO_SLACK_DAYS * DAY);
      return r ? num(r.value) : null;
    };
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
          source: { accessionNo: r.citation.accession_no, form: r.citation.form },
        };
      });
      const last = num(at(member, latest)?.value);
      const yearAgo = yearAgoOf(member);
      const share = last !== null && shareOf !== "none" && base > 0 ? last / base : null;
      const lastEnd = lastOf(member);
      return {
        member,
        label,
        cells,
        share,
        shareTooBig: share !== null && share > 1 + SHARE_TOLERANCE,
        // a change from a loss (or zero) a year earlier isn't a growth rate
        yoy: last !== null && yearAgo !== null && yearAgo > 0 ? last / yearAgo - 1 : null,
        yoyNotMeaningful: last !== null && yearAgo !== null && yearAgo <= 0,
        overlaps: separated?.drop.has(member) ?? false,
        until: lastEnd === latest ? null : columns[ends.indexOf(lastEnd)]!.label,
      };
    });
    // current members by their newest value, then the ones that stopped, by their last
    const lastValue = (r: (typeof out)[number]) => num(at(r.member, lastOf(r.member))?.value) ?? -Infinity;
    out.sort((a, b) => Number(a.until !== null) - Number(b.until !== null) || lastValue(b) - lastValue(a));
    // a member that stopped is said once, below; it stays out of sums either way
    const parts = out.filter((r) => r.overlaps && r.until === null).map((r) => r.label);
    const overlapNote = parts.length
      ? `${listOf(parts)} ${parts.length === 1 ? "is" : "are"} part of another line here, or another cut of the same revenue, so the lines add up to more than revenue. Each share is of consolidated revenue.`
      : separated?.unreliable.has(latestIndex)
        ? "These lines add up to more than revenue and can't be separated: some overlap, or include sales between segments. Each share is of consolidated revenue, so don't add them up."
        : null;
    const what = SEGMENT_METRICS[metric]!.toLowerCase();
    const staleNote = latest && Date.parse(newest) - Date.parse(latest) > STALE_DAYS * DAY
      ? `Not current: the last ${what} by ${AXIS_LABELS[axis]!.toLowerCase()} is for ${columns.at(-1)!.label} (the quarter ending ${day(latest, true)}). ` +
        `The company's filings since report other segment figures (to ${day(newest, true)}), not this one.`
      : null;
    const stopped = out.filter((r) => r.until !== null);
    const retired = stopped.map((r) => r.label);
    const one = new Set(stopped.map((r) => r.until)).size === 1;
    const retiredNote = retired.length
      ? `${listOf(retired)} ${retired.length === 1 ? "was" : "were"} last reported ${one ? `for ${stopped[0]!.until}` : `before ${columns.at(-1)!.label}`}: ` +
        `usually the company reorganised its segments or stopped disclosing ${retired.length === 1 ? "it" : "them"}, so ${retired.length === 1 ? "it's" : "they're"} listed after the current ones, not as if current.`
      : null;
    return { axis, metric, title: `${SEGMENT_METRICS[metric]} by ${AXIS_LABELS[axis]!.toLowerCase()}`, columns, rows: out, shareOf, overlapNote, staleNote, retiredNote };
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
