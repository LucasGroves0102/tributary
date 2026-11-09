/** What a table shows where there's no figure to read. One mark, one meaning, everywhere:
 *
 *   —      not reported: the company's filings don't give this figure, or don't give what it is
 *          computed from. Says something about the company.
 *   ·      no data in this period: the row has values, just none for this column's period. Says
 *          something about the period. Only period tables have it.
 *   n/a    not applicable: the figure doesn't describe this kind of company or this kind of
 *          number ("n/a (bank)"; a balance has no trailing twelve months).
 *   n/m    not meaningful: a figure exists, but what it divides by is close to zero, negative or on
 *          another basis, so it says nothing. The exact figure and the reason stay in the hover.
 *   $0     a real zero: the filing says zero. Never used for a blank, and a blank never reads zero.
 *
 * Whether a value is n/m, n/a or old is decided once, in the database (migration 035), so sorts,
 * filters and the agent's data can't disagree with the screen: the API sends `readings` and this
 * module only words them. A value marked old also shows, dimmed, with its date, and leaves sorts,
 * range filters and medians; one merely behind shows its date and still counts. */

import { day, quarter } from "./format";
import type { Num, ValueReading, ValueSource } from "./types";

export const NOT_REPORTED = "—";
export const NO_DATA = "·";
export const NOT_APPLICABLE = "n/a";
export const NOT_MEANINGFUL = "n/m";
export const BANK_NA = "n/a (bank)";

export const NO_DATA_TITLE = "No data in this period";

/** What a computed figure is made of, for a blank's hover: "not reported" isn't quite true of them. */
const NEEDS: Record<string, string> = {
  gross_margin: "gross profit and revenue",
  operating_margin: "operating income and revenue",
  net_margin: "net income and revenue",
  free_cash_flow: "operating cash flow and capital expenditure",
  fcf_margin: "operating cash flow, capital expenditure and revenue",
  revenue_growth: "revenue now and a year earlier",
  net_income_growth: "net income now and a year earlier",
  debt_to_equity: "long-term debt and stockholders' equity",
  net_debt: "long-term debt and cash",
  current_ratio: "current assets and current liabilities",
  roe: "net income and stockholders' equity",
  payout_ratio: "dividends and net income",
  efficiency_ratio: "noninterest expense, net interest income and noninterest income",
};

/** Why a bank line is often blank (migration 048 measured every bank's tags). */
const TAGGING: Record<string, string> = {
  cet1_ratio: " Most banks tag their CET1 ratio only in the 10-K, and the largest (JPMorgan, Bank of America, Wells Fargo) only under their own XBRL tags, which Tributary doesn't read.",
  loans: " Some banks (Wells Fargo) tag their loans only under their own XBRL tags.",
  provision_for_credit_losses: "",
};

/** The hover of a — under a label like "Free cash flow". */
export function notReportedTitle(label: string, key: string): string {
  const metric = key.replace(/_(ttm|q)$/, "");
  const needs = NEEDS[metric];
  return needs
    ? `${label}: can't be computed. It needs ${needs}, and this company's filings don't give all of them.`
    : `${label}: not reported. This company's filings don't give this figure.${TAGGING[metric] ?? ""}`;
}

/** One figure as tables show it. `value` is what it ranks by: null unless it can be compared. */
export interface Figure {
  value: number | null;
  text: string;
  title: string;
  /** "Dec '20", when the figure isn't from the company's latest period */
  asOf: string | null;
  old: boolean;
  /** a reading other than the plain figure, to leave out of a median and say why */
  status: ValueReading["status"] | null;
}

function num(v: Num | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** How a figure reads: the plain value and its source, or one of the marks above. */
export function readFigure({ label, columnKey, raw, reading, source, latestPeriod, format, sourceTitle }: {
  label: string;
  columnKey: string;
  raw: Num | null | undefined;
  reading?: ValueReading | null;
  source: ValueSource | null;
  latestPeriod?: string | null;
  format: (v: number) => string;
  /** "Net debt, at the quarter end, Jun 30, 2026. Source: ..." */
  sourceTitle: string;
}): Figure {
  const exact = num(raw);
  const status = reading?.status ?? null;
  const shown = exact === null ? "" : ` The figure on file: ${format(exact)}.`;
  const source_ = exact === null ? "" : ` ${sourceTitle}`;
  switch (status) {
    case "not_applicable":
      return {
        value: null, text: BANK_NA, asOf: null, old: false, status,
        title: `${label}: not applicable. ${sentence(reading?.reason)}${shown}${source_}`,
      };
    case "not_meaningful":
      return {
        value: null, text: NOT_MEANINGFUL, asOf: null, old: false, status,
        title: `${label}: not meaningful, because ${reading?.reason ?? "its denominator says nothing"}.${shown}${source_}`,
      };
    case "old":
      return {
        value: null, text: exact === null ? NOT_REPORTED : format(exact), old: true, status,
        asOf: source ? quarter(source.period_end) : null,
        title: `${sourceTitle} Old: this company's latest period is ${latestPeriod ? day(latestPeriod, true) : "much later"}, so it isn't a current figure and stays out of sorts, filters and medians.`,
      };
    case "behind":
      return {
        value: exact, text: exact === null ? NOT_REPORTED : format(exact), old: false, status,
        asOf: source ? quarter(source.period_end) : null,
        title: `${sourceTitle} This company's latest period is ${latestPeriod ? day(latestPeriod, true) : "later"}.`,
      };
    default:
      return exact === null
        ? { value: null, text: NOT_REPORTED, title: notReportedTitle(label, columnKey), asOf: null, old: false, status: null }
        : { value: exact, text: format(exact), title: sourceTitle, asOf: null, old: false, status: null };
  }
}

const sentence = (s: string | null | undefined): string =>
  s ? `${s[0]!.toUpperCase()}${s.slice(1)}.` : "";
