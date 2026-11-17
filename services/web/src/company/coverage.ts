/** Coverage notes in plain language: what the nightly coverage sweep (migration 076) found missing
 * from a company's figures or documents, and why. The Query API sends each gap's kind, cause,
 * reason and detail (dates, forms, metric names, counts); the words live here, once. A reason says
 * whose gap it is: Tributary's to fix, not in the SEC's structured data, or outside what Tributary
 * reads. Nothing here is a value. */

import { metricLabel } from "../chart/metrics";
import { day } from "../format";
import type { CoverageGap, CoverageReason } from "../types";

export interface CoverageWords {
  /** short heading, e.g. "Revenue starts in 2017" */
  title: string;
  /** one or two sentences: what is missing and why */
  text: string;
}

/** Each kind of gap in a phrase, in the order a page lists them (the status page's rows). */
export const GAP_KINDS: [string, string][] = [
  ["no_figures", "No figures at all"],
  ["re_registration", "Figures start at a re-registration whose earlier registration isn't linked"],
  ["history_short", "History cut short: figures start years after the first structured reports"],
  ["metric_missing", "A core figure (revenue, net income, assets, equity, operating cash flow) missing lately"],
  ["figures_behind", "Figures behind the latest 10-K, 10-Q, 20-F or 40-F"],
  ["statements_exhibit", "10-K statements outside the stored document"],
  ["documents_missing", "10-K and 10-Q documents not stored for reading and search"],
];

/** Whose gap it is, said once beside every coverage note. */
export const REASON_WORDS: Record<CoverageReason, string> = {
  fixable: "Tributary's gap: on the list to fix",
  not_in_sec_data: "Not in the SEC's structured data",
  out_of_scope: "Outside what Tributary reads",
};

const year = (iso: string | undefined): string => (iso ? iso.slice(0, 4) : "its first filings");
const on = (iso: string | undefined): string => (iso ? day(iso, true) : "an unknown date");

/** The gaps a page shows: a history cut short by a re-registration is said by the
 * re-registration's own note, not twice. */
export function shownGaps(gaps: CoverageGap[]): CoverageGap[] {
  const moved = gaps.some((g) => g.kind === "re_registration");
  return gaps.filter((g) => !(moved && g.kind === "history_short" && g.cause === "re_registration"));
}

/** What one gap means for a reader of `name`'s figures. `nameOf` turns a company id (a parent's)
 * into its name. */
export function coverageWords(g: CoverageGap, name: string, nameOf: (id: string) => string = (id) => id): CoverageWords {
  const d = g.detail ?? {};
  switch (g.kind) {
    case "no_figures":
      switch (g.cause) {
        case "fund":
          return { title: "No figures: an investment fund", text: `${name} files as a fund (N-CSR, N-PORT), not the 10-K and 10-Q financial statements Tributary reads.` };
        case "abs":
          return { title: "No figures: an asset-backed issuer", text: `${name} files distribution reports for its assets (10-D), not financial statements.` };
        case "filed_with_parent":
          return { title: "Figures filed with its parent", text: `${name}'s reports are part of ${d.parent ? nameOf(String(d.parent)) : "its parent"}'s combined filing, whose figures Tributary reads as the parent's.` };
        case "foreign":
          return { title: "Annual reports on Form 20-F or 40-F", text: `${name} files annual reports as a foreign issuer${d.last_foreign ? ` (latest ${on(d.last_foreign)})` : ""}, and Tributary doesn't read their figures for this company yet.` };
        case "no_xbrl":
          return { title: "No structured data in its reports", text: `${name}'s 10-K and 10-Q filings carry no XBRL, so there are no figures to read; the filings are on EDGAR.` };
        case "stopped":
          return { title: "No recent reports", text: `${name} hasn't filed a 10-K or 10-Q since ${on(d.last_10x)}.` };
        case "awaiting":
          return { title: "Figures on their way", text: `${name} filed its first reports recently; their figures usually reach Tributary within days.` };
        case "bank_regulator":
          return { title: "Reports to its bank regulator", text: `${name} files its annual and quarterly reports with its banking regulator, not the SEC, so the SEC has no figures for it.` };
        case "no_reports_yet":
          return { title: "No reports yet", text: `${name} hasn't filed a 10-K, 10-Q, 20-F or 40-F with the SEC yet; figures appear after its first one.` };
        case "no_sec_reports":
          return { title: "No periodic reports with the SEC", text: `${name} files no annual or quarterly reports with the SEC.` };
        default:
          return { title: "No figures yet", text: `${name} files reports with structured data, but none of its figures have reached Tributary yet.` };
      }
    case "re_registration":
      return {
        title: `Figures start at its ${year(d.moved_on)} re-registration`,
        text: `${name} re-registered with the SEC on ${on(d.moved_on)}${d.form ? ` (${d.form})` : ""}. Its earlier reports are under its previous registration, which isn't linked here yet, so figures start in ${year(d.figures_from)}.`,
      };
    case "history_short":
      if (g.cause === "revenue_tag") {
        return {
          title: `Revenue starts in ${year(d.revenue_from)}`,
          text: `Revenue before ${year(d.revenue_from)} is filed under a tag Tributary doesn't read as revenue yet (a sales line for goods or services alone, or ${name}'s own tag); the filings have it. Its other figures go back to ${year(d.figures_from)}.`,
        };
      }
      return {
        title: `Figures start in ${year(d.figures_from)}`,
        text: `${name} has filed reports with structured data since ${year(d.first_xbrl_report)}, but its figures before ${year(d.figures_from)} haven't reached Tributary yet; the filings are on EDGAR.`,
      };
    case "metric_missing": {
      const ms = (d.metrics ?? []).map((m) => `${metricLabel(m.metric).toLowerCase()} after ${on(m.last)}`);
      return {
        title: ms.length === 1 ? `${metricLabel(d.metrics![0]!.metric)} missing lately` : "Figures missing lately",
        text: `${capital(list(ms))} ${ms.length === 1 ? "isn't" : "aren't"} in the figures Tributary reads from ${name}'s filings, though its other figures go on.`,
      };
    }
    case "figures_behind":
      return {
        title: "Figures behind its latest report",
        text: `Figures run to ${on(d.figures_to)}; ${name}'s ${d.form ?? "report"} for the period ending ${on(d.period_end)}, filed ${on(d.filed)}, ${g.cause === "no_xbrl" ? "has no structured data to read" : "isn't in them yet"}.`,
      };
    case "statements_exhibit":
      return g.cause === "ex13"
        ? { title: "Statements in an exhibit", text: `${name}'s 10-K prints its financial statements in its annual report to shareholders (Exhibit 13), which Tributary doesn't store yet; the figures come from the filing's structured data.` }
        : { title: "Statements outside Item 8", text: `${name}'s latest 10-K keeps its financial statements outside Item 8, where Tributary looks for them; the figures come from the filing's structured data.` };
    case "documents_missing":
      return {
        title: g.cause === "none" ? "Documents not stored yet" : "Some documents not stored yet",
        text: `${d.stored ?? 0} of ${name}'s ${d.expected ?? "recent"} 10-K and 10-Q documents since 2016 are stored for reading and search; the figures don't depend on them.`,
      };
    default:
      return { title: "A gap in the data", text: `Something is missing from ${name}'s data here.` };
  }
}

function capital(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
