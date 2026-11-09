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

import { day } from "../format";
import { EIGHT_K_ITEMS } from "../reader/sections";
import type { EarningsQuarter, EventAnnouncement, EventItem, EventsResponse, FilingEvent, ReleasePassage } from "../types";

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

/** The flag for a key stat whose figure is as of `periodEnd`, if an event after it may have changed it. */
export function keyStatFlag(key: string, periodEnd: string | null, flags: StaleFlag[]): StaleFlag | null {
  const metric = KEY_STAT_METRIC[key];
  const flag = (metric ? flags.find((f) => f.metric === metric) : undefined) ?? flags.find((f) => f.metric === "*") ?? null;
  if (!flag || !periodEnd || !flag.asOf || periodEnd > flag.asOf) return null;
  return flag;
}

export const quarterName = (q: Pick<EarningsQuarter, "period_end" | "estimated">): string =>
  `Quarter ended ${day(q.period_end, true)}${q.estimated ? " (expected)" : ""}`;

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
export function windowLine(r: EventsResponse): string {
  const rep = r.latest_report;
  if (r.window === "since_report" && rep) {
    const word = rep.form.startsWith("10-K") ? "fiscal year" : "quarter";
    return `8-Ks filed after the ${word} the latest ${rep.form} covers (ended ${day(rep.period_end, true)}; filed ${day(rep.filed, true)})`;
  }
  return `8-Ks filed since ${day(r.since, true)}: no 10-Q or 10-K in the last 400 days to count from`;
}
