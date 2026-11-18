/** Data notes in plain language: what each quality finding (ops checks), derived-quarters note
 * and "companyfacts behind EDGAR" note means for someone reading the numbers. The Query API
 * sends kinds, dates, filings and relative sizes; values only where a note needs them, read for
 * this reader (a tag disagreement's two figures, migration 075). The words live here. */

import { metricLabel } from "../chart/metrics";
import { asFiled, day } from "../format";
import type { DataNote, NoteFiling, NoteRank } from "../types";

export interface Explained {
  /** short heading, e.g. "Revenue restated" */
  title: string;
  /** one or two sentences: what happened and what it means for the figures shown */
  text: string;
  /** figures the filing states, each shown with a seal to it (a tag disagreement's two, 075) */
  figures?: { text: string; tag: string }[];
}

/** A tag disagreement's figures as the API read them for this reader (075), gold's tag first. */
function stated(d: Record<string, unknown>): { tag: string; text: string }[] | null {
  if (!Array.isArray(d.figures) || d.figures.length !== 2) return null;
  const out = d.figures.map((f) => {
    const x = f as Record<string, unknown>;
    const value = Number(x.value);
    return typeof x.tag === "string" && typeof x.unit === "string" && Number.isFinite(value)
      ? { tag: x.tag, text: asFiled(value, x.unit) }
      : null;
  });
  return out.every((x) => x !== null) ? (out as { tag: string; text: string }[]) : null;
}

/** Groups on the Data notes tab, in order, with the kinds each holds. */
export const NOTE_GROUPS: { key: string; label: string; kinds: (k: string) => boolean }[] = [
  { key: "freshness", label: "Freshness", kinds: (k) => k === "companyfacts_behind" },
  { key: "splits", label: "Stock splits", kinds: (k) => k === "stock_split" },
  { key: "restated", label: "Restated figures", kinds: (k) => k === "restatement" || k === "annual_sum" },
  { key: "periods", label: "Gaps and overlapping periods", kinds: (k) => ["series_gap", "overlapping_quarters", "calendar_collision", "net_debt_gap"].includes(k) },
  { key: "values", label: "Values to double-check", kinds: (k) => ["impossible_value", "tag_disagreement", "statement_mismatch", "sign_flip", "outlier", "mistagged_fact"].includes(k) || k.startsWith("segment_sum_") },
  { key: "derived", label: "Derived quarters", kinds: (k) => k === "derived_quarters" },
];

const SEVERITY_LABEL = { error: "check", warning: "note", info: "info" } as const;

export function severityLabel(s: DataNote["severity"]): string {
  return SEVERITY_LABEL[s] ?? s;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
/** A ratio in words: "1,000", "5.6", "1,076". */
const times = (r: number): string => r.toLocaleString("en-US", { maximumFractionDigits: r < 10 ? 1 : 0 });
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const date = (v: unknown): string => (str(v) ? day(str(v)!, true) : "an unknown date");
const signedPct = (r: number): string => `${r >= 0 ? "+" : "−"}${Math.abs(r * 100).toLocaleString("en-US", { maximumFractionDigits: Math.abs(r) < 0.1 ? 1 : 0, minimumFractionDigits: Math.abs(r) < 0.1 ? 1 : 0 })}%`;
/** A restatement this many times the original (1,000%) isn't a correction of the same figure. */
const BIG_CHANGE = 10;

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

/** The SEC's XBRL data usually follows a filing within this many days; past it the note says so
 * rather than promising the usual. */
const USUAL_LAG_DAYS = 3;

/** How much a note matters (the API's rank, M2); an older API sends none: a plain note. */
export function rankOf(n: DataNote): NoteRank {
  return n.rank === "attention" || n.rank === "minor" ? n.rank : "note";
}

const RANK_ORDER: Record<NoteRank, number> = { attention: 0, note: 1, minor: 2 };

/** When a stock split took effect, as far as the filings show (the API's `effective_after` and
 * `first_restated_period_end`): "between Apr 28, 2024 and Jul 28, 2024", or "by Jul 28, 2024";
 * null for an older API, which sent neither. Never a restating filing's date (analyst test 4). */
export function splitWhen(n: Pick<DataNote, "kind" | "detail">): string | null {
  if (n.kind !== "stock_split") return null;
  const after = str(n.detail.effective_after);
  const by = str(n.detail.first_restated_period_end);
  if (after && by) return `between ${date(after)} and ${date(by)}`;
  return by ? `by ${date(by)}` : null;
}

/** The date beside a note's title: a split's "took effect between ..." (splitWhen), else the
 * note's own date. A split from an older API (no effective dates) shows none: its date was the
 * last restating filing's, which reads as the split's. */
export function noteWhen(n: Pick<DataNote, "kind" | "detail" | "date">): string | null {
  if (n.kind === "stock_split") {
    const when = splitWhen(n);
    return when ? `took effect ${when}` : null;
  }
  return n.date ? day(n.date, true) : null;
}

/** A note in words, and, for a series the API grouped (`repeats`), where else it was found. */
export function explain(n: DataNote, now = Date.now()): Explained {
  const e = explainOne(n, now);
  const repeats = num(n.detail.repeats);
  if (repeats === null || repeats < 2) return e;
  const span = str(n.detail.first_period_end) && str(n.detail.last_period_end)
    ? `, from ${date(n.detail.first_period_end)} to ${date(n.detail.last_period_end)}`
    : "";
  return {
    title: `${e.title} (${repeats} periods)`,
    text: `${e.text} The same check found this in ${repeats} periods${span}; this is the newest.`,
  };
}

/** One finding in words. Every number keeps its unit (%, days, ×) and period. */
function explainOne(n: DataNote, now: number): Explained {
  const d = n.detail;
  const m = n.metric ? metricLabel(n.metric) : "A figure";
  const pe = n.period_end ? `the period ending ${day(n.period_end, true)}` : "one period";
  switch (n.kind) {
    case "companyfacts_behind": {
      const count = num(d.missing_filings) ?? n.filings.length;
      const newest = n.filings[0];
      // the filings come newest first, so the last is the one that has waited longest
      const oldestFiled = n.filings.at(-1)?.filed ?? str(d.missing_filed);
      const waited = oldestFiled ? Math.floor((now - Date.parse(oldestFiled.slice(0, 10))) / 86_400_000) : 0;
      const which = count === 1
        ? `a ${newest?.form ?? "10-Q/10-K"} filed ${date(d.missing_filed)}`
        : `${count} 10-K/10-Q filings, the newest filed ${date(d.missing_filed)} and the oldest ${date(oldestFiled)},`;
      return {
        title: count === 1 ? "A newer filing isn't in the figures yet" : "Newer filings aren't in the figures yet",
        text:
          `EDGAR has ${which} that the SEC's XBRL data (companyfacts) doesn't include yet, so the financials here end with the filing of ${date(d.facts_latest_filed)}. ` +
          (waited > USUAL_LAG_DAYS
            ? `${count === 1 ? "It has" : "The oldest has"} been waiting ${waited} days, longer than the usual day or two. The figures update only when the SEC publishes that data; until then, read the filing itself.`
            : "They update once the SEC publishes that data, usually within a day or two."),
      };
    }
    case "stock_split": {
      const split = str(d.split) ?? "A split";
      const periods = num(d.periods) ?? 0;
      const firstForm = str(d.first_restated_form) ?? "filing";
      const by = str(d.first_restated_period_end);
      const firstFiled = str(d.first_restated_filed);
      // when it took effect, as far as the filings show (the API's first filing on the new basis);
      // the restating filings are listed under the note, never given as the split's date
      const took = by
        ? `It took effect ${splitWhen(n)}, as far as the filings show: the ${firstForm} for the period ended ${date(by)}${firstFiled ? `, filed ${date(firstFiled)},` : ""} is the first on the new share basis. `
        : firstFiled
          ? `It was first reported in the ${firstForm} filed ${date(firstFiled)}. `
          : "";
      return {
        title: `${split} ${d.reverse ? "reverse " : ""}stock split`,
        text:
          took +
          `Later filings restated share counts and per-share figures for ${periods} ${periods === 1 ? "period" : "periods"} from ${date(d.first_period_end)} to ${date(d.last_period_end)} ` +
          "by the split's ratio, share counts and EPS moving opposite ways in the same filing (the restating filings are listed below). That isn't a correction. Periods no filing restated stay on the old share count, so read per-share figures across the split with care.",
      };
    }
    case "restatement": {
      const change = num(d.change);
      const unit = d.unit_change === true;
      const maybeSplit = str(d.may_be_split);
      const big = change !== null && Math.abs(change) >= BIG_CHANGE;
      return {
        title: unit ? `${m}: unit changed between filings` : `${m} restated`,
        text:
          `${m} for ${pe} was first reported in ${filingPhrase(n, "original", d.original_filed)} and ` +
          `changed${change !== null ? ` by ${signedPct(change)}` : ""} in ${filingPhrase(n, "restated", d.restated_filed)}. ` +
          (unit
            ? "That's the size of a unit change (thousands against ones) in the filing's XBRL, not a change in the figure."
            : maybeSplit
              ? `That's exactly what a ${maybeSplit} stock split does, so it may be a split; nothing else here confirms it, so check the filing.`
              : big
                ? "A change this large usually means the two filings describe different things (a shell company before a merger, say) or a scale slip in one, rather than a correction. Tributary shows the latest figure."
                : "Tributary shows the latest figure; growth rates against the original would differ."),
      };
    }
    case "annual_sum": {
      const dev = num(d.deviation);
      // 072: the check says when a restatement explains it (the year's figure, or one of its quarters)
      const restated = str(d.restated);
      return {
        title: restated ? `${m}: quarters differ from a restated year` : `${m}: quarters don't add up to the year`,
        text:
          `The four quarters of ${m.toLowerCase()} in the fiscal year ending ${n.period_end ? day(n.period_end, true) : "?"} sum to ` +
          `${dev !== null ? `${Math.abs(dev * 100).toFixed(1)}% ${dev > 0 ? "more" : "less"} than` : "something other than"} the annual figure in the 10-K` +
          (restated === "year"
            ? ". A later filing changed that year's figure and not every quarter with it, so the restatement is the difference, not an error. TTM uses the quarters."
            : restated === "quarter"
              ? ". One of the year's quarters was restated after it was first reported, which accounts for the difference. TTM uses the quarters."
              : ", usually because a quarter was later restated or reclassified. TTM uses the quarters."),
      };
    }
    case "series_gap": {
      const years = num(d.repeated);
      return {
        title: years ? `${m}: the same quarter missing in ${years} years` : `${m}: missing quarter`,
        text:
          `No quarterly ${m.toLowerCase()} is reported or derivable from ${date(d.missing_from)} to ${date(d.missing_to)}` +
          (years ? `, and the same quarter is missing in ${years} fiscal years: the 10-K tags the year in a way the 10-Qs don't, often with the company's own tag. ` : ". ") +
          "TTM, year-over-year growth and charts that need that quarter are left blank rather than guessed.",
      };
    }
    case "net_debt_gap":
      return {
        title: "Net debt: left out",
        text: d.investments_end
          ? `Debt and cash are on file for ${pe}, but net debt is left out rather than guessed: short-term investments were last tagged at ${date(d.investments_end)}, and this balance sheet has room for them under a line the filing doesn't tag in a standard way. Check the filing's balance sheet.`
          : `Debt and cash are on file for ${pe}, but net debt is left out: interest expense is more than half of the debt tagged, so the tagged debt can only be part of it. Check the filing's debt note.`,
      };
    case "mistagged_fact": {
      const filed = d.filed ? ` filed ${date(d.filed)}` : "";
      if (str(d.rule) === "debt_parts_scale") {
        // 050: Bank First's 2019 10-K, current and noncurrent debt 1,000 times its own total
        const factor = num(d.factor);
        return {
          title: "Debt: left out, parts in the wrong scale",
          text: `The ${str(d.form) ?? "filing"}${filed} tags current and noncurrent debt for ${pe} that add up to ${factor ? `${factor.toLocaleString("en-US")} times` : "many times"} the total debt it tags for the same date, a scale slip in its XBRL. The parts aren't used.`,
        };
      }
      return str(d.rule) === "debt_above_liabilities"
        ? {
            title: `${m}: left out, larger than total liabilities`,
            text: `The ${str(d.form) ?? "filing"}${filed} tags ${m.toLowerCase()} for ${pe} above the company's total liabilities at that date, which can't be right, usually a scale slip in its XBRL. The figure isn't used.`,
          }
        : {
            title: `${m}: left out, a period after its filing`,
            text: `The ${str(d.form) ?? "filing"}${filed} tags ${m.toLowerCase()} for ${pe}, a period that hadn't ended when it was filed, almost always a typing slip in its XBRL. The figure isn't used.`,
          };
    }
    case "tag_disagreement": {
      // 072: two tags of one line in one filing that can't both be right; the API sends the tags and
      // their ratio, never the figures
      const factor = num(d.factor);
      const tag = str(d.tag) ?? "one tag";
      const other = str(d.other_tag) ?? "another";
      const ratio = factor === null ? "many times" : factor >= 1 ? `${times(factor)} times` : `1/${times(1 / factor)} of`;
      const filing = `The ${str(d.form) ?? "filing"}${d.filed ? ` filed ${date(d.filed)}` : ""}`;
      const rule = str(d.rule);
      // 075: the two figures, read now for this reader; or the filing has been corrected since
      const figs = stated(d);
      if (d.corrected === true) {
        return {
          title: `${m}: two tags disagreed, since corrected`,
          text: `${filing} tagged ${m.toLowerCase()} for ${pe} as ${tag} and as ${other}, ${ratio} the first, when this check ran. Its figures have been corrected or reloaded since, so the two no longer stand side by side; the check clears at its next run.`,
        };
      }
      if (figs) {
        const [a, b] = figs as [{ tag: string; text: string }, { tag: string; text: string }];
        const apart = factor === null ? "" : ` (${factor >= 1 ? times(factor) : times(1 / factor)} times apart)`;
        const what =
          rule === "scale" ? "the same figure in two scales, thousands entered as units or the reverse"
            : rule === "same_line" ? "two names of one line that can't both be right"
              : `a total under its own part (${b.tag}), so one of the tags is likely on the wrong line`;
        return {
          title: rule === "scale" ? `${m}: one figure in two scales` : rule === "same_line" ? `${m}: two tags of one line disagree` : `${m}: a total smaller than its own part`,
          text: `${filing} gives ${m.toLowerCase()} for ${pe} as ${a.text} in one place and ${b.text} in another${apart}: ${what}. The figure shown here is the first; check the filing for which is right.`,
          figures: [{ text: a.text, tag: a.tag }, { text: b.text, tag: b.tag }],
        };
      }
      if (rule === "scale") {
        return {
          title: `${m}: one figure in two scales`,
          text: `${filing} tags ${m.toLowerCase()} for ${pe} as ${tag} and as ${other}, ${ratio} the first: the same figure in two scales (thousands entered as units, or the reverse). The figure shown is ${tag}'s; check the filing for which scale is right.`,
        };
      }
      if (rule === "same_line") {
        return {
          title: `${m}: two tags of one line disagree`,
          text: `${filing} tags ${m.toLowerCase()} for ${pe} under two names for the same line, ${tag} and ${other}, one ${ratio} the other. They can't both be right; the figure shown is ${tag}'s. Check the filing.`,
        };
      }
      return {
        title: `${m}: a total smaller than its own part`,
        text: `${filing} tags ${m.toLowerCase()} for ${pe} as ${tag}, and a part of that line (${other}) at ${ratio} it. A total can't be smaller than its part, so one of the tags is likely on the wrong line; the figure shown is the total as tagged. Check the filing.`,
      };
    }
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
      // 050: a negative stock-based compensation quarter is a benefit as filed
      if (str(d.rule) === "benefit") {
        return {
          title: `${m}: a benefit this quarter`,
          text: `${m} for ${pe} is negative as filed, unlike the quarters around it: forfeited awards reversing expense booked earlier, or cash-settled awards remeasured. The filing's own year-to-date figures agree, so it isn't a slip.`,
        };
      }
      return {
        title: `${m}: sign flips for one quarter`,
        text: `${m} for ${pe} has the opposite sign from the quarters around it, usually a sign convention slip in the filing's XBRL rather than a real change.`,
      };
    case "outlier": {
      const x = num(d.vs_neighbours);
      const size = `${x !== null ? `${Math.abs(x).toFixed(1)}×` : "many times"} the size of the quarters around it`;
      // 072: a line that swings by nature (a debt raise, an acquisition, a special dividend, a tax charge)
      if (str(d.rule) === "lumpy" && d.seasonal !== true) {
        return {
          title: `${m}: a large swing`,
          text: `${m} for ${pe} is ${size}. This line swings with one-off events (a debt raise or repayment, an acquisition, a special dividend, a tax charge or benefit), so a quarter like this is usually real; check the filing if it matters.`,
        };
      }
      return d.seasonal === true
        ? {
            title: `${m}: seasonal peak`,
            text: `${m} for ${pe} is ${size}, and the same quarter peaks in other years too: the business's season, not an error.`,
          }
        : {
            title: `${m}: one-quarter spike`,
            text: `${m} for ${pe} is ${size}, and other years' same quarter doesn't do this. That can be a one-off event, or a scale error (thousands entered as units) in the filing's XBRL: check the filing.`,
          };
    }
    case "derived_quarters": {
      const q = num(d.quarters) ?? 0;
      if (d.computed === true && d.net_sales === true) {
        // 155: Walmart and Costco, whose revenue includes membership fees and other income
        return {
          title: `${m}: net sales less cost of revenue`,
          text:
            `For ${q} quarter${q === 1 ? "" : "s"} between ${date(d.first_period_end)} and ${date(d.latest_period_end)} the filing prints no gross profit, so it's net sales less cost of revenue: ` +
            "revenue includes membership fees and other income that cost of sales doesn't carry, and the company states its gross margin on net sales. Ratios' gross margin is the same figure over net sales.",
        };
      }
      if (d.computed === true) {
        return {
          title: `${m}: revenue less cost of revenue`,
          text:
            `For ${q} quarter${q === 1 ? "" : "s"} between ${date(d.first_period_end)} and ${date(d.latest_period_end)} the filing prints no gross profit, so it's revenue less its cost of revenue. ` +
            "That only reads as a gross margin for some businesses, so it's left blank for banks, insurers, utilities, oil and gas, telecoms, restaurants and any quarter where cost is under 15% of revenue or the result looks out of line.",
        };
      }
      return {
        title: `${m}: ${q} derived quarter${q === 1 ? "" : "s"}`,
        text:
          `For ${q} quarter${q === 1 ? "" : "s"} between ${date(d.first_period_end)} and ${date(d.latest_period_end)} the filing reports only the year or year-to-date, ` +
          "so the quarter is the difference (fiscal Q4 = the 10-K's year minus the first nine months). Statements mark these values as derived.",
      };
    }
    case "statement_mismatch": {
      // 074: the filing's own statement table against the figure shown (which its XBRL states)
      const factor = num(d.factor);
      const line = str(d.line) === "liabilities_and_equity" ? "total liabilities and equity" : m.toLowerCase();
      const filing = `The ${str(d.form) ?? "filing"}${d.filed ? ` filed ${date(d.filed)}` : ""}`;
      const ratio = factor === null ? "" : factor >= 1 ? ` ${times(factor)} times the printed one` : ` 1/${times(1 / factor)} of the printed one`;
      return {
        title: str(d.rule) === "scale" ? `${m}: the statement prints another scale` : `${m}: the statement prints another figure`,
        text: `${filing} prints ${line} for ${pe} in its own statement as a different figure from the one shown here, which its XBRL states${ratio ? `; the figure shown is${ratio}` : ""}` +
          (str(d.rule) === "scale" ? ": the same figure in two scales. " : str(d.rule) === "sign" ? ": the same figure with the other sign. " : ". ") +
          "Either the XBRL is on the wrong line or Tributary misread the table; check the statement.",
      };
    }
    default: {
      if (n.kind.startsWith("segment_sum_")) {
        const dev = num(d.deviation);
        const axis = AXES[str(d.axis) ?? ""] ?? "segments";
        // a segment's profit leaves out what no segment carries, so these two differing is normal
        const unallocated = n.metric === "operating_income";
        return {
          title: `${m}: ${axis} don't add up`,
          text:
            `For ${pe}, ${num(d.members) ?? "the"} ${axis} sum to ${dev !== null ? `${Math.abs(dev * 100).toFixed(1)}% ${dev > 0 ? "more" : "less"} than` : "something other than"} ` +
            `consolidated ${m.toLowerCase()}. ` +
            (unallocated
              ? "Costs no segment carries, such as unallocated corporate costs, usually explain this; the 10-K's segment note reconciles it."
              // migration 070: members that overlap (a parent beside its children, two cuts) aren't flagged,
              // so what's left is a member the filings don't name, or one on another basis
              : "No combination of them makes the reported figure: a member isn't tagged, or they're on another basis (sales between segments). Each share on the Segments tab is of consolidated revenue, so don't add them up."),
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

/** Notes by group, dropping empty groups. Notes keep the API's order (what matters first, then
 * newest), and the groups run by their most important note, then their newest: a tab opening on
 * a 2021 restatement above 2026 gaps reads as if nothing newer happened. Derived quarters aren't
 * events, so they stay last whatever their date. */
export function groupNotes(notes: DataNote[]): { key: string; label: string; notes: DataNote[] }[] {
  const newest = (g: { notes: DataNote[] }) => g.notes.reduce((d, n) => ((n.date ?? "") > d ? n.date ?? "" : d), "");
  const top = (g: { notes: DataNote[] }) => Math.min(...g.notes.map((n) => RANK_ORDER[rankOf(n)]));
  return NOTE_GROUPS.map((g) => ({ key: g.key, label: g.label, notes: notes.filter((n) => g.kinds(n.kind)) }))
    .concat([{ key: "other", label: "Other", notes: notes.filter((n) => !NOTE_GROUPS.some((g) => g.kinds(n.kind))) }])
    .filter((g) => g.notes.length > 0)
    .sort((a, b) => Number(a.key === "derived") - Number(b.key === "derived") || top(a) - top(b) || newest(b).localeCompare(newest(a)));
}

/** The summary line over a company's notes (the API's `summary`), or null from an older API. */
export function summaryText(s: { attention: number; note: number; minor: number; findings: number } | undefined): string | null {
  if (!s) return null;
  const parts = [
    s.attention ? `${s.attention} need${s.attention === 1 ? "s" : ""} attention` : "Nothing needs attention",
    s.note ? `${s.note} ${s.note === 1 ? "is" : "are"} worth a look` : null,
    s.minor ? `${s.minor} ${s.minor === 1 ? "is" : "are"} minor or old` : null,
  ].filter((p): p is string => !!p);
  const said = parts.length === 1 ? parts[0]! : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  const notes = s.attention + s.note + s.minor;
  // what the second count is (Riley, test 10, finding 58: "from 84 check findings" was never explained)
  return `${said}. The ${notes.toLocaleString("en-US")} ${notes === 1 ? "note gathers" : "notes gather"} ` +
    `${s.findings.toLocaleString("en-US")} finding${s.findings === 1 ? "" : "s"} of the nightly data checks: a note is one check's findings across periods.`;
}
