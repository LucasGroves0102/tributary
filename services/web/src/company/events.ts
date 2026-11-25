/** Earnings releases and 8-K events as the screens word them (Query API `/earnings`, `/events`;
 * the rules are query_api/events.py's and releases.py's). Pure. The API decides which item makes
 * which balance-sheet figure stale and what a release states; nothing here re-derives either:
 *
 * - Items read with the reader's short labels (`reader/sections.ts` EIGHT_K_ITEMS), the SEC's title
 *   otherwise. An event's label leaves out 9.01 (exhibits) when it has other items.
 * - A stale flag sits on a key stat when the API lists its metric (cash_q → cash, net_debt_q →
 *   net_debt, debt_to_equity_q → debt_to_equity; "*" every figure) and the figure is as of the
 *   report's period end or earlier: it is before the event. A flag, never a recomputed figure.
 * - An event flagged from its 7.01 / 8.01 announcement (the API's `announced`, F1b) is named by
 *   what was announced ("a completed acquisition or disposition", "a new financing") and says it
 *   was read from that announcement; its words are quoted as the API returned them.
 * - A quarter is named by its end ("Quarter ended Sep 30, 2026", "expected" until a 10-Q/10-K
 *   covers it): the release's fiscal label is the company's own words, quoted in its headline.
 * - A passage is read as lines: bullets ("•◦▪●") as list items, a reconciliation as a table (its
 *   lines kept), everything else as paragraphs. */

import { fyeMonth, MONTHS } from "../companies/screen";
import { day } from "../format";
import { EIGHT_K_ITEMS } from "../reader/sections";
import type { EarningsQuarter, EarningsResponse, EventAnnouncement, EventItem, EventsResponse, FilingEvent, ReleasePassage } from "../types";

/** what a stale flag calls each metric the API can list */
export const METRIC_LABEL: Record<string, string> = {
  cash: "Cash",
  long_term_debt: "Debt",
  net_debt: "Net debt",
  total_assets: "Total assets",
  stockholders_equity: "Equity",
  debt_to_equity: "Debt / equity",
  shares_outstanding: "Shares outstanding",
  "*": "Every figure",
};

/** key-stat columns (data.ts KEY_STATS) a stale metric applies to */
const KEY_STAT_METRIC: Record<string, string> = { cash_q: "cash", net_debt_q: "net_debt", debt_to_equity_q: "debt_to_equity" };

export const itemLabel = (i: EventItem): string => EIGHT_K_ITEMS[i.item] ?? i.title;

/** An event's items as labels, exhibits (9.01) left out when there's anything else. */
export function eventLabels(e: Pick<FilingEvent, "items">): string[] {
  const items = e.items.length > 1 ? e.items.filter((i) => i.item !== "9.01") : e.items;
  return items.map(itemLabel);
}

export interface StaleFlag {
  metric: string;
  label: string;
  /** the period end the figure is as of */
  asOf: string | null;
  events: FilingEvent[];
  /** "the Oct 27, 2026 8-K (Acquisition or disposition)", "... and 1 more 8-K" (the newest
   * event; `text` names every one) */
  short: string;
  /** the whole sentence, for a hover or a list */
  text: string;
}

/** what each kind of announcement is called */
export const ANNOUNCED_LABEL: Record<EventAnnouncement["kind"], string> = {
  deal: "a completed acquisition or disposition",
  financing: "a new financing",
};

/** the item that would have reported each kind */
export const ANNOUNCED_ITEM: Record<EventAnnouncement["kind"], string> = { deal: "2.01", financing: "2.03" };

/** "a completed acquisition or disposition, read from its 7.01 announcement" */
export const announcedPhrase = (a: EventAnnouncement): string =>
  `${ANNOUNCED_LABEL[a.kind] ?? a.kind}, read from its ${a.item} announcement`;

const eventPhrase = (e: FilingEvent) => {
  const a = e.announced?.[0];
  return `the ${day(e.event_date, true)} 8-K (${a ? announcedPhrase(a) : (eventLabels(e)[0] ?? "8-K")})`;
};

/** The API's stale list with its events, worded. */
export function staleFlags(r: EventsResponse | null): StaleFlag[] {
  if (!r) return [];
  const byAcc = new Map(r.events.map((e) => [e.accession_no, e]));
  return r.stale.map((s) => {
    const events = s.events.map((a) => byAcc.get(a)).filter((e): e is FilingEvent => e !== undefined);
    const label = METRIC_LABEL[s.metric] ?? s.metric;
    const after = events.map(eventPhrase).join(" and ") || "a later 8-K";
    // one phrase on a key stat or a list line; every event in the sentence (a hover)
    const more = events.length - 1;
    return {
      metric: s.metric,
      label,
      asOf: s.as_of,
      events,
      short: more > 0 ? `${eventPhrase(events[0]!)} and ${more} more 8-K${more === 1 ? "" : "s"}` : after,
      text: `${label}${s.as_of ? ` at ${day(s.as_of, true)}` : ""} is from before ${after}, which may have changed it. ` +
        "Tributary doesn't compute a figure after the event: the 8-K and the next 10-Q or 10-K say what changed.",
    };
  });
}

/** No pro forma: what the page says it doesn't do after an event. */
export const NO_PRO_FORMA =
  "Tributary doesn't compute a figure after the event (no pro forma): the 8-K says what happened, the next 10-Q or 10-K what the balance sheet became.";

/** The flags as one sentence per event (the Overview, under its ledger's "*"): "Debt, net debt, debt /
 * equity and cash are as of Jul 26, 2026; the Aug 17, 2026 8-K (Material agreement) may have changed
 * them." The hover (`title`) says there's no pro forma and words each flag in full. */
export function staleLine(flags: StaleFlag[]): { text: string; title: string } | null {
  if (!flags.length) return null;
  const groups = new Map<string, StaleFlag[]>();
  for (const f of flags) groups.set(`${f.short}|${f.asOf ?? ""}`, [...(groups.get(`${f.short}|${f.asOf ?? ""}`) ?? []), f]);
  const text = [...groups.values()].map((g) => {
    const labels = g.map((f, i) => (i === 0 ? f.label : f.label.toLowerCase()));
    const list = labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}` : labels[0]!;
    const many = g.length > 1 || g[0]!.metric === "*";
    const asOf = g[0]!.asOf ? ` as of ${day(g[0]!.asOf, true)}` : " from before it";
    return `${list} ${many && g[0]!.metric !== "*" ? "are" : "is"}${asOf}; ${g[0]!.short} may have changed ${many && g[0]!.metric !== "*" ? "them" : "it"}.`;
  }).join(" ");
  return { text, title: [NO_PRO_FORMA, ...flags.map((f) => f.text)].join("\n\n") };
}

/** The flag for a key stat whose figure is as of `periodEnd`, if an event after it may have changed it. */
export function keyStatFlag(key: string, periodEnd: string | null, flags: StaleFlag[]): StaleFlag | null {
  const metric = KEY_STAT_METRIC[key];
  const flag = (metric ? flags.find((f) => f.metric === metric) : undefined) ?? flags.find((f) => f.metric === "*") ?? null;
  if (!flag || !periodEnd || !flag.asOf || periodEnd > flag.asOf) return null;
  return flag;
}

/** A foreign filer's period (migration 095): its 20-F or 40-F year, or a period its 6-K release
 * reports (a quarter or a half: the release says which). */
export function foreignForm(form: string | null | undefined): boolean {
  return /^(20-F|40-F|6-K)/.test(form ?? "");
}

/** A quarter's name. One no 10-Q or 10-K covers yet is "expected", unless its results are out: then
 * its release's day says so (Riley, test 10, finding 31: "(expected)" sat beside a release filed that
 * day). */
export const quarterName = (q: Pick<EarningsQuarter, "period_end" | "estimated"> & Partial<Pick<EarningsQuarter, "report" | "release">>): string =>
  foreignForm(q.report?.form) ? `Fiscal year ended ${day(q.period_end, true)}`
    : foreignForm(q.release?.form) ? `Period ended ${day(q.period_end, true)}`
      : `Quarter ended ${day(q.period_end, true)}${!q.estimated ? "" : q.release ? ` (results out ${day(q.release.filed, true)}; its 10-Q or 10-K isn't filed yet)` : " (expected)"}`;

/** Results day (Riley, test 10, finding 21): the newest 8-K reporting results (item 2.02) filed
 * after the latest 10-Q or 10-K, so its figures are out before that report's are; else null. Words
 * the API's events only. */
export function resultsOut(r: EventsResponse | null | undefined): FilingEvent | null {
  const last = r?.latest_report;
  if (!r || !last) return null;
  return r.events.find((e) => !e.amendment && e.items.some((i) => i.item === "2.02") && e.filed > last.filed) ?? null;
}

/** Results announced, report not filed (NVIDIA, Nov 2026: its Oct 25 quarter's release on Nov 17, no
 * 10-Q yet, so the statements ended in July with no word why): the newest quarter of the API's
 * earnings answer when it has a release and no 10-Q or 10-K. `form` is the report it waits for: a
 * 10-K for the quarter ending in the fiscal year's last month (`fyeMonth`'s 7-day rule), else a 10-Q;
 * "10-Q or 10-K" without a fiscal year end. Words the API's answer only; null otherwise. */
export function resultsPending(r: EarningsResponse | null | undefined, fiscalYearEnd: string | null):
  { periodEnd: string; announced: string; accessionNo: string; sectionKey: string | null; form: string } | null {
  const q = r?.quarters[0];
  if (!q || q.report || !q.release) return null;
  const last = fyeMonth(fiscalYearEnd);
  const [, m, d] = q.period_end.slice(0, 10).split("-").map(Number);
  const month = MONTHS[(d! <= 7 ? m! + 10 : m! - 1) % 12];
  return { periodEnd: q.period_end, announced: q.release.filed, accessionNo: q.release.accession_no, sectionKey: q.release.section_key,
    form: last === null ? "10-Q or 10-K" : month === last ? "10-K" : "10-Q" };
}

/** The quarter a page opens on: the newest with a release, else the newest. */
export function firstQuarter(qs: EarningsQuarter[]): EarningsQuarter | null {
  return qs.find((q) => q.release) ?? qs[0] ?? null;
}

export type Line = { kind: "heading" | "bullet" | "sub" | "text"; text: string };

/** A passage's lines (the first is its heading when it has one), for display. */
export function passageLines(p: Pick<ReleasePassage, "text" | "heading">): Line[] {
  return p.text.split("\n").filter((t) => t.trim()).map((t, i) => {
    if (i === 0 && p.heading) return { kind: "heading", text: t };
    if (/^[◦▪]/.test(t)) return { kind: "sub", text: t.replace(/^[◦▪]\s*/, "") };
    if (/^[•●]/.test(t)) return { kind: "bullet", text: t.replace(/^[•●]\s*/, "") };
    return { kind: "text", text: t };
  });
}

/** Material events first is the API's order (newest first); routine ones (votes, exhibits) apart. */
export function splitEvents(r: EventsResponse): { material: FilingEvent[]; routine: FilingEvent[] } {
  return { material: r.events.filter((e) => e.material), routine: r.events.filter((e) => !e.material) };
}

/** What a window says it covers. */
/** The same window, short, for a margin rubric: [what it lists, the second line]. */
export function windowRubric(r: EventsResponse): [string, string] {
  const rep = r.latest_report;
  if (r.window === "since_report" && rep) {
    const word = rep.form.startsWith("10-K") ? "fiscal year" : "quarter";
    return [`8-Ks after the ${word} ended ${day(rep.period_end, true)}`, `The ${rep.form} for it was filed ${day(rep.filed, true)}`];
  }
  return [`8-Ks since ${day(r.since, true)}`, "No 10-Q or 10-K in the last 400 days to count from"];
}

export function windowLine(r: EventsResponse): string {
  const rep = r.latest_report;
  if (r.window === "since_report" && rep) {
    const word = rep.form.startsWith("10-K") ? "fiscal year" : "quarter";
    return `8-Ks filed after the ${word} the latest ${rep.form} covers (ended ${day(rep.period_end, true)}; filed ${day(rep.filed, true)})`;
  }
  return `8-Ks filed since ${day(r.since, true)}: no 10-Q or 10-K in the last 400 days to count from`;
}
