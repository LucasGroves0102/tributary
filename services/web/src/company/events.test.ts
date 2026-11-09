import { describe, expect, it } from "vitest";

import type { EarningsResponse, EventsResponse } from "../types";
import { eventLabels, firstQuarter, keyStatFlag, passageLines, quarterName, splitEvents, staleFlags, windowLine } from "./events";
import DECK_EARNINGS from "./fixtures/deck-earnings.json";
import VRT_EARNINGS from "./fixtures/vrt-earnings.json";
import VRT_EVENTS from "./fixtures/vrt-events.json";

// real answers of the branch's Query API over Vertiv's and Deckers' filings (hermes, 2026-11-08)
const events = VRT_EVENTS as EventsResponse;
const vrt = VRT_EARNINGS as EarningsResponse;
const deck = DECK_EARNINGS as EarningsResponse;
const UIG = "0001628280-26-067393";
const KES = "0001628280-26-067127";

describe("events since the last report", () => {
  it("words the API's stale list: Vertiv's balances at Sep 30 are from before the UIG acquisition (tester 1)", () => {
    const flags = staleFlags(events);
    expect(flags.map((f) => f.label)).toEqual(["Cash", "Debt", "Net debt", "Total assets"]);
    expect(flags.every((f) => f.asOf === "2026-09-30" && f.events.map((e) => e.accession_no).join() === `${UIG},${KES}`)).toBe(true);
    expect(flags[2]!.short).toBe("the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K");
    expect(flags[2]!.text).toContain("Net debt at Sep 30, 2026 is from before the Oct 27, 2026 8-K (Acquisition or disposition) and " +
      "the Oct 26, 2026 8-K (a completed acquisition or disposition, read from its 7.01 announcement)");
    expect(flags[2]!.text).toContain("doesn't compute a figure after the event");
    expect(staleFlags(null)).toEqual([]);
  });

  it("names an event flagged from its 7.01 announcement by what it announced (F1b: Vertiv's Oct 26 8-K, KES)", () => {
    const kes = events.events.find((e) => e.accession_no === KES)!;
    expect(kes.affects).toEqual(["cash", "long_term_debt", "net_debt", "total_assets"]);
    expect(kes.announced?.map((a) => [a.kind, a.item, a.words])).toEqual([["deal", "7.01", "Completes Acquisition"]]);
    const only = staleFlags({ ...events, stale: [{ metric: "cash", as_of: "2026-09-30", events: [KES] }] });
    expect(only[0]!.short).toBe("the Oct 26, 2026 8-K (a completed acquisition or disposition, read from its 7.01 announcement)");
    // an older API without `announced` words the event by its items, as before
    const older = { ...events, events: events.events.map(({ announced: _, ...e }) => e), stale: [{ metric: "cash", as_of: "2026-09-30", events: [KES] }] };
    expect(staleFlags(older)[0]!.short).toBe("the Oct 26, 2026 8-K (Regulation FD disclosure)");
  });

  it("flags a key stat only for its metric, and only a figure as of the report's period end or earlier", () => {
    const flags = staleFlags(events);
    expect(keyStatFlag("net_debt_q", "2026-09-30", flags)?.label).toBe("Net debt");
    expect(keyStatFlag("cash_q", "2026-09-30", flags)?.label).toBe("Cash");
    expect(keyStatFlag("net_debt_q", "2026-12-31", flags)).toBeNull(); // a later balance: after the event
    expect(keyStatFlag("net_debt_q", null, flags)).toBeNull();
    expect(keyStatFlag("revenue_ttm", "2026-09-30", flags)).toBeNull(); // not a figure the event changes
    expect(keyStatFlag("debt_to_equity_q", "2026-09-30", flags)).toBeNull(); // 2.01 doesn't list it
    const every = staleFlags({ ...events, stale: [{ metric: "*", as_of: "2026-09-30", events: [UIG] }] });
    expect(keyStatFlag("revenue_ttm", "2026-09-30", every)?.label).toBe("Every figure");
  });

  it("labels events with the reader's item names, exhibits left out, and sets routine filings apart", () => {
    expect(events.events.map(eventLabels)).toEqual([
      ["Results of operations", "Regulation FD disclosure"],
      ["Acquisition or disposition", "Regulation FD disclosure"],
      ["Regulation FD disclosure"],
    ]);
    const vote = { ...events.events[2]!, accession_no: "x", material: false, items: [{ item: "5.07", title: "Submission of Matters to a Vote of Security Holders", kind: "vote" }] };
    const split = splitEvents({ ...events, events: [...events.events, vote] });
    expect(split.material).toHaveLength(3);
    expect(split.routine.map((e) => e.accession_no)).toEqual(["x"]);
    expect(windowLine(events)).toBe("8-Ks filed after the quarter the latest 10-Q covers (ended Sep 30, 2026; filed Oct 28, 2026)");
    expect(windowLine({ ...events, window: "recent", since: "2025-10-04", latest_report: null }))
      .toBe("8-Ks filed since Oct 4, 2025: no 10-Q or 10-K in the last 400 days to count from");
  });
});

describe("earnings releases", () => {
  it("opens on the newest quarter with a release, named by its end", () => {
    const q = firstQuarter(vrt.quarters)!;
    expect(q.period_end).toBe("2026-09-30");
    expect(quarterName(q)).toBe("Quarter ended Sep 30, 2026");
    expect(quarterName({ ...q, estimated: true })).toBe("Quarter ended Sep 30, 2026 (expected)");
    expect(firstQuarter([{ ...q, release: null }, { ...q, period_end: "2026-06-30" }])!.period_end).toBe("2026-06-30");
    expect(firstQuarter([])).toBeNull();
  });

  it("reads a release's outlook as lines: heading, bullets, sub-bullets, text", () => {
    const [outlook] = deck.quarters[0]!.outlook;
    const lines = passageLines(outlook!);
    expect(lines[0]).toEqual({ kind: "heading", text: "Full Fiscal Year 2027 Outlook(2) for the Twelve Month Period Ending March 31, 2027" });
    expect(lines[1]!.kind).toBe("text"); // the release's own caveat introduces the list
    expect(lines.find((l) => l.text.startsWith("Diluted earnings per share is now expected"))?.kind).toBe("bullet");
    expect(lines.find((l) => l.text.startsWith("HOKA is now expected"))?.kind).toBe("sub");
    expect(lines.at(-1)!.text).toMatch(/^\(2\) Through October 8, 2026/);
  });

  it("carries what the testers needed: Vertiv's Q4 and FY guidance tables, Deckers' refund note", () => {
    const q = vrt.quarters[0]!;
    expect(q.release?.section_key).toBe("EX-99.1");
    expect(q.outlook.map((o) => o.heading)).toContain("Full Year 2026 Guidance");
    expect(q.outlook.some((o) => o.text.includes("Net sales $14,450M - $14,850M"))).toBe(true);
    expect(q.adjustments.map((a) => a.kind)).toEqual(Array(6).fill("reconciliation"));
    const [note] = deck.quarters[0]!.adjustments;
    expect(note!.kind).toBe("note");
    expect(note!.text).toContain("$46.0 million");
  });
});
