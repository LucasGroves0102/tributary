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
 * module only words them. A value marked old (035's `period_status`: over 300 days before the
 * company's latest period, so the tag stopped) is not the company's current figure: it reads
 * "— since Sep '14" (not reported since then), the last figure on file and its date in the hover,
 * and leaves sorts, range filters and medians (analyst test 4: Deckers' 2014 net debt and Eaton's
 * 2019 operating margin read as current). One merely behind shows its date and still counts. An
 * amount in another currency (056: a 20-F filer's euros) shows in that currency and leaves them
 * too. Migration 069 adds two: `not_computed`, n/a with the API's reason (Tributary computes no gross
 * margin for a warehouse club or a telecom: Riley 3), and `stale`, the figure with its date, out of
 * sorts and medians, when the company's latest period is long past (Aphoenity's Sep 2024 margin
 * topped a Nov 2026 sort: Riley 23). A figure from an annual report (20-F, 40-F) says so beside
 * quarterly ones: "annual, period ended Dec '25" (owner decision 4), the full date in the hover. */

import { currencyOf, day, money, quarter } from "./format";
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
  // Morgan, test 11, finding 15: Airbnb and Veeva fold capex into other investing activities
  free_cash_flow: "operating cash flow and capital expenditure (a company can stop showing capital expenditure on its own line, folding it into other investing activities once it is small)",
  fcf_margin: "operating cash flow, capital expenditure and revenue (a company can stop showing capital expenditure on its own line, folding it into other investing activities once it is small)",
  revenue_growth: "revenue now and a year earlier",
  net_income_growth: "net income now and a year earlier",
  // 155: no debt reads as none only on the filings' evidence (no debt tag in the year, little
  // interest); a company whose debt sits in tags of its own has neither
  debt_to_equity: "long-term debt (or evidence there is none) and stockholders' equity",
  net_debt: "long-term debt (or evidence there is none) and cash",
  current_ratio: "current assets and current liabilities",
  roe: "net income and stockholders' equity",
  payout_ratio: "dividends and net income",
  efficiency_ratio: "noninterest expense, net interest income and noninterest income",
};

/** Why a bank line is often blank (migration 048 measured every bank's tags). */
const TAGGING: Record<string, string> = {
  cet1_ratio: " Most banks tag their CET1 ratio only in the 10-K; some tag it only for one regulatory approach or subsidiary, which Tributary reads for JPMorgan, Bank of America (10-K only) and Wells Fargo.",
  loans: " Some banks (Wells Fargo) tag their loans only under their own XBRL tags.",
  provision_for_credit_losses: "",
};

/** Gross margin's blank, as migration 039 decides it: a computed gross profit only where cost of revenue is the
 * cost of what revenue sells (Riley 3: Walmart, Costco, AT&T and Verizon read a bare "—"). Words only; the rule
 * and its industries stay in the database. */
const GROSS_MARGIN_BLANK = "can't be computed here. Either the filings don't give gross profit or cost of revenue, or the company " +
  "is one Tributary doesn't work it out for: a warehouse club or variety store, whose revenue includes membership fees, " +
  "or a telecom, whose network costs sit outside cost of revenue.";

/** The hover of a — under a label like "Free cash flow". */
export function notReportedTitle(label: string, key: string): string {
  const metric = key.replace(/_(ttm|q)$/, "");
  if (metric === "gross_margin") return `${label}: ${GROSS_MARGIN_BLANK}`;
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
  /** other_currency: the ISO code the figure is in */
  currency?: string;
}

function num(v: Num | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** A 20-F's or 40-F's figure is a fiscal year, not twelve months to a recent quarter (owner decision 4). */
export const ANNUAL_FORMS = /^(20-F|40-F)/;

/** "annual, period ended Dec '25" under a figure from an annual report, else the period's month. */
function dated(source: ValueSource | null, always: boolean): string | null {
  if (!source) return null;
  if (ANNUAL_FORMS.test(source.form ?? "")) return `annual, period ended ${quarter(source.period_end)}`;
  return always ? quarter(source.period_end) : null;
}

const annualNote = (source: ValueSource | null): string =>
  source && ANNUAL_FORMS.test(source.form ?? "")
    ? ` An annual figure: the ${source.form}'s fiscal year, period ended ${day(source.period_end, true)}; the company reports once a year.`
    : "";

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
      // not the company's current figure: the mark for "not reported", dated by the last one on file
      return {
        value: null, text: NOT_REPORTED, old: true, status,
        asOf: source ? quarter(source.period_end) : null,
        title: oldTitle(label, columnKey, exact === null ? null : format(exact), source, sourceTitle, latestPeriod),
      };
    case "behind":
      return {
        value: exact, text: exact === null ? NOT_REPORTED : format(exact), old: false, status,
        asOf: dated(source, true),
        title: `${sourceTitle} This company's latest period is ${latestPeriod ? day(latestPeriod, true) : "later"}.${annualNote(source)}`,
      };
    case "not_computed":
      // no figure because Tributary computes none for this kind of company (069): the API says why
      return {
        value: null, text: NOT_APPLICABLE, asOf: null, old: false, status,
        title: `${label}: not computed for this company. ${sentence(reading?.reason)}${shown}${source_}`,
      };
    case "stale":
      // the company's figure, dated, never ranked with current ones (069)
      return {
        value: null, text: exact === null ? NOT_REPORTED : format(exact), old: false, status,
        asOf: dated(source, true),
        title: `${label}: ${sentence(reading?.reason)}${annualNote(source)} ${sourceTitle} It stays out of sorts, filters and medians.`,
      };
    case "not_reported":
      // a user's own metric whose input has no figure for this company: the API names the input
      // (Riley, test 10, finding 26: blank cells said nothing)
      return {
        value: null, text: NOT_REPORTED, asOf: null, old: false, status,
        title: `${label}: no value for this company: an input has no figure (${reading?.reason ?? "not on file"}).`,
      };
    case "other_currency": {
      // an amount in the company's own currency (migration 056): shown in it, labelled, and never
      // ranked or averaged with dollars
      const unit = reading?.unit ?? "";
      const inIt = exact === null ? null : money(exact, unit);
      return {
        value: null, text: inIt ?? NOT_REPORTED, asOf: dated(source, false), old: false, status, currency: currencyOf(unit),
        title: `${label}: ${inIt ? `${inIt}, ` : ""}reported in ${currencyOf(unit)}, so it isn't sorted, filtered or averaged with US-dollar figures.${source_}`,
      };
    }
    default:
      return exact === null
        ? { value: null, text: NOT_REPORTED, title: notReportedTitle(label, columnKey), asOf: null, old: false, status: null }
        : { value: exact, text: format(exact), title: `${sourceTitle}${annualNote(source)}`, asOf: dated(source, false), old: false, status: null };
  }
}

/** The hover of an old figure: when it was last reported, the figure then and its source, and why it
 * isn't shown as the company's current one. */
function oldTitle(label: string, key: string, shown: string | null, source: ValueSource | null, sourceTitle: string,
  latestPeriod: string | null | undefined): string {
  const needs = NEEDS[key.replace(/_(ttm|q)$/, "")];
  const since = source ? day(source.period_end, true) : "an earlier period";
  const head = needs
    ? `${label}: not reported since ${since}. It needs ${needs}, and the filings since don't give all of them.`
    : `${label}: not reported since ${since}.`;
  const last = shown === null || !source ? "" : ` The last figure on file, ${shown}: ${sourceTitle}`;
  const latest = latestPeriod ? day(latestPeriod, true) : "much later";
  return `${head}${last} This company's latest period is ${latest}, so that figure isn't shown as a current one and stays out of sorts, filters and medians.`;
}

const sentence = (s: string | null | undefined): string =>
  s ? `${s[0]!.toUpperCase()}${s.slice(1)}.` : "";
