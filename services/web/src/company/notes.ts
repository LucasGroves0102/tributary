/** Data notes in plain language: what each quality finding (ops checks), derived-quarters note
 * and "companyfacts behind EDGAR" note means for someone reading the numbers. The Query API
 * sends kinds, dates, filings and relative sizes (never values); the words live here. */

import { metricLabel } from "../chart/metrics";
import { day } from "../format";
import type { DataNote, NoteFiling } from "../types";

export interface Explained {
  /** short heading, e.g. "Revenue restated" */
  title: string;
  /** one or two sentences: what happened and what it means for the figures shown */
  text: string;
}

/** Groups on the Data notes tab, in order, with the kinds each holds. */
export const NOTE_GROUPS: { key: string; label: string; kinds: (k: string) => boolean }[] = [
  { key: "freshness", label: "Freshness", kinds: (k) => k === "companyfacts_behind" },
  { key: "restated", label: "Restated figures", kinds: (k) => k === "restatement" || k === "annual_sum" },
  { key: "periods", label: "Gaps and overlapping periods", kinds: (k) => ["series_gap", "overlapping_quarters", "calendar_collision"].includes(k) },
  { key: "values", label: "Values to double-check", kinds: (k) => ["impossible_value", "sign_flip", "outlier"].includes(k) || k.startsWith("segment_sum_") },
  { key: "derived", label: "Derived quarters", kinds: (k) => k === "derived_quarters" },
];

const SEVERITY_LABEL = { error: "check", warning: "note", info: "info" } as const;

export function severityLabel(s: DataNote["severity"]): string {
  return SEVERITY_LABEL[s] ?? s;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const date = (v: unknown): string => (str(v) ? day(str(v)!, true) : "an unknown date");
const signedPct = (r: number): string => `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toFixed(Math.abs(r) < 0.1 ? 1 : 0)}%`;

/** "the 10-Q filed Feb 25, 2026", from the note's filing with that role. */
function filingPhrase(n: DataNote, role: NoteFiling["role"], fallbackDate?: unknown): string {
  const f = n.filings.find((x) => x.role === role);
  const form = f?.form ?? "filing";
  const filed = f?.filed ?? str(fallbackDate);
  return `the ${form}${filed ? ` filed ${day(filed, true)}` : ""}`;
}

const AXES: Record<string, string> = {
  business_segment: "business segments",
  geography: "geographic areas",
  product_service: "products and services",
};

/** A note in words. Every number keeps its unit (%, days, ×) and period. */
export function explain(n: DataNote): Explained {
  const d = n.detail;
  const m = n.metric ? metricLabel(n.metric) : "A figure";
  const pe = n.period_end ? `the period ending ${day(n.period_end, true)}` : "one period";
  switch (n.kind) {
    case "companyfacts_behind": {
      const count = num(d.missing_filings) ?? n.filings.length;
      const newest = n.filings[0];
      return {
        title: "A newer filing isn't in the figures yet",
        text:
          `EDGAR has ${count === 1 ? `a ${newest?.form ?? "10-Q/10-K"}` : `${count} 10-K/10-Q filings`} filed ${date(d.missing_filed)} ` +
          `that the SEC's XBRL data (companyfacts) doesn't include yet, so the financials here end with the filing of ${date(d.facts_latest_filed)}. ` +
          "They update once the SEC publishes that data, usually within a day or two.",
      };
    }
    case "restatement": {
      const change = num(d.change);
      return {
        title: `${m} restated`,
        text:
          `${m} for ${pe} was first reported in ${filingPhrase(n, "original", d.original_filed)} and ` +
          `changed${change !== null ? ` by ${signedPct(change)}` : ""} in ${filingPhrase(n, "restated", d.restated_filed)}. ` +
          "Tributary shows the latest figure; growth rates against the original would differ.",
      };
    }
    case "annual_sum": {
      const dev = num(d.deviation);
      return {
        title: `${m}: quarters don't add up to the year`,
        text:
          `The four quarters of ${m.toLowerCase()} in the fiscal year ending ${n.period_end ? day(n.period_end, true) : "?"} sum to ` +
          `${dev !== null ? `${Math.abs(dev * 100).toFixed(1)}% ${dev > 0 ? "more" : "less"} than` : "something other than"} the annual figure in the 10-K, ` +
          "usually because a quarter was later restated or reclassified. TTM uses the quarters.",
      };
    }
    case "series_gap":
      return {
        title: `${m}: missing quarter`,
        text:
          `No quarterly ${m.toLowerCase()} is reported or derivable from ${date(d.missing_from)} to ${date(d.missing_to)}. ` +
          "TTM, year-over-year growth and charts that need that quarter are left blank rather than guessed.",
      };
    case "overlapping_quarters": {
      const days = num(d.overlap_days);
      return {
        title: `${m}: overlapping quarters`,
        text:
          `The quarter ending ${n.period_end ? day(n.period_end, true) : "?"} starts ${days !== null ? `${days} day${days === 1 ? "" : "s"}` : ""} before the previous one ends, ` +
          "usually a change of fiscal calendar. Quarter-on-quarter comparisons across it may be off.",
      };
    }
    case "calendar_collision": {
      const ends = Array.isArray(d.period_ends) ? d.period_ends.filter((x): x is string => typeof x === "string") : [];
      return {
        title: `${m}: two quarters in one calendar quarter`,
        text:
          `Fiscal quarters ending ${ends.map((e) => day(e, true)).join(" and ") || "on two dates"} both fall in calendar ${str(d.calendar_quarter) ?? "?"}. ` +
          "The calendar view shows one of them; the fiscal view shows both.",
      };
    }
    case "impossible_value": {
      const rule = str(d.rule);
      if (rule && rule.includes(">")) {
        const [a, b] = rule.split(">").map((s) => metricLabel(s.trim()));
        return {
          title: `${a} exceeds ${b!.toLowerCase()}`,
          text: `At ${pe}, ${a!.toLowerCase()} is larger than ${b!.toLowerCase()}, which can't be right; one of them is likely tagged wrongly in the filing's XBRL. Check the filing before using either.`,
        };
      }
      return {
        title: `${m} is negative`,
        text: `${m} for ${pe} is below zero, which can't be right for this line; the filing's XBRL likely has a sign error. Check the filing before using it.`,
      };
    }
    case "sign_flip":
      return {
        title: `${m}: sign flips for one quarter`,
        text: `${m} for ${pe} has the opposite sign from the quarters around it, usually a sign convention slip in the filing's XBRL rather than a real change.`,
      };
    case "outlier": {
      const x = num(d.vs_neighbours);
      return {
        title: `${m}: one-quarter spike`,
        text:
          `${m} for ${pe} is ${x !== null ? `${Math.abs(x).toFixed(1)}×` : "many times"} the size of the quarters around it, ` +
          "typically a scale error (thousands entered as units) in the filing's XBRL.",
      };
    }
    case "derived_quarters": {
      const q = num(d.quarters) ?? 0;
      return {
        title: `${m}: ${q} derived quarter${q === 1 ? "" : "s"}`,
        text:
          `For ${q} quarter${q === 1 ? "" : "s"} between ${date(d.first_period_end)} and ${date(d.latest_period_end)} the filing reports only the year or year-to-date, ` +
          "so the quarter is the difference (fiscal Q4 = the 10-K's year minus the first nine months). Statements mark these values as derived.",
      };
    }
    default: {
      if (n.kind.startsWith("segment_sum_")) {
        const dev = num(d.deviation);
        const axis = AXES[str(d.axis) ?? ""] ?? "segments";
        return {
          title: `${m}: ${axis} don't add up`,
          text:
            `For ${pe}, ${num(d.members) ?? "the"} ${axis} sum to ${dev !== null ? `${Math.abs(dev * 100).toFixed(1)}% ${dev > 0 ? "more" : "less"} than` : "something other than"} ` +
            `consolidated ${m.toLowerCase()}. Segment data may overlap or omit a member; read segment shares with care.`,
        };
      }
      return { title: `${m}: ${n.kind.replace(/_/g, " ")}`, text: `A data check flagged ${m.toLowerCase()} for ${pe}.` };
    }
  }
}

/** Filing roles as link labels. */
export const ROLE_LABEL: Record<NoteFiling["role"], string> = {
  source: "source",
  annual: "annual figure",
  quarter: "quarter",
  original: "original",
  restated: "restated in",
  missing: "on EDGAR",
  total: "consolidated",
};

/** EDGAR links only (the URLs come from the SEC's own index). */
export function edgarUrl(f: NoteFiling): string | null {
  return f.filing_url?.startsWith("https://www.sec.gov/") ? f.filing_url : null;
}

/** Notes by group (NOTE_GROUPS order), dropping empty groups. Notes keep the API's order
 * (newest first). */
export function groupNotes(notes: DataNote[]): { key: string; label: string; notes: DataNote[] }[] {
  return NOTE_GROUPS.map((g) => ({ key: g.key, label: g.label, notes: notes.filter((n) => g.kinds(n.kind)) }))
    .concat([{ key: "other", label: "Other", notes: notes.filter((n) => !NOTE_GROUPS.some((g) => g.kinds(n.kind))) }])
    .filter((g) => g.notes.length > 0);
}
