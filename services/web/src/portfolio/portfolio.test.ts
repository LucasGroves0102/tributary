import { expect, it } from "vitest";

import { bellLabel, SOURCE_WORDS } from "../alerts/alerts";
import { href, parse } from "../route";
import type { JournalEntry } from "../types";
import {
  amountOf,
  cash,
  editWords,
  exportDateFromName,
  gainWords,
  heldSince,
  holdingName,
  perShare,
  priceSource,
  priceWords,
  reviewWords,
  shares,
  signedPct,
  weightTitle,
  withCorrections,
} from "./portfolio";

it("shows the user's own money to the cent, negatives in parentheses", () => {
  expect(cash("1234.5")).toBe("$1,234.50");
  expect(cash("-12.3")).toBe("($12.30)");
  expect(cash("0.00")).toBe("$0.00");
  expect(cash(null)).toBe("—");
  expect(cash("10", "EUR")).toBe("EUR 10.00");
  expect(perShare("186.26")).toBe("$186.26");
  expect(perShare("0.51255")).toBe("$0.5126");
  expect(perShare(null)).toBe("—");
  expect(shares("1250")).toBe("1,250");
  expect(shares("0.312")).toBe("0.312");
});

it("a price always says where it came from and its day, and when it's old", () => {
  const p = { value: "186.26", on: "2026-11-16", source: "fidelity" as const, provider: null, stale: false };
  expect(priceSource(p)).toBe("Your Fidelity export of Nov 16, 2026");
  expect(priceSource({ ...p, source: "typed" })).toBe("Typed by you for Nov 16, 2026");
  expect(priceSource({ ...p, source: "provider", provider: "tiingo" })).toBe("Your Tiingo close of Nov 16, 2026");
  expect(priceWords({ ...p, stale: true }, 7)).toBe("Your Fidelity export of Nov 16, 2026: more than 7 days old");
});

it("words gains with their sign and a review with its report", () => {
  expect(gainWords("33.1", "0.0667")).toBe("up $33.10 (+6.7%)");
  expect(gainWords("-5", "-0.125")).toBe("down $5.00 (−12.5%)");
  expect(gainWords("0")).toBe("flat");
  expect(gainWords(null)).toBeNull();
  expect(signedPct(null)).toBeNull();
  expect(reviewWords({ accession_no: "a", form: "10-Q", filed: "2026-11-19", period_end: "2026-10-26", cik: null }))
    .toBe("Review due: the 10-Q filed Nov 19, 2026 for the quarter ended Oct 26, 2026");
  expect(reviewWords({ accession_no: "a", form: "10-K", filed: "2027-02-25", period_end: null, cik: null }))
    .toBe("Review due: the 10-K filed Feb 25, 2027");
  // results day: an 8-K with item 2.02 (Riley, test 10, finding 13)
  expect(reviewWords({ accession_no: "a", form: "8-K", filed: "2026-11-17", period_end: null, cik: null }))
    .toBe("Review due: results released Nov 17, 2026 (8-K)");
  expect(weightTitle("cost")).toContain("not by value");
});

it("names a holding by the directory, a fund by the broker's name or its symbol", () => {
  const label = () => "Nvidia (NVDA)";
  expect(holdingName({ company_id: "0001045810", name: null, symbol: "NVDA" }, label)).toBe("Nvidia (NVDA)");
  expect(holdingName({ company_id: null, name: "Vanguard S&P 500 ETF", symbol: "VOO" }, label)).toBe("Vanguard S&P 500 ETF");
  expect(holdingName({ company_id: null, name: null, symbol: "VOO" }, label)).toBe("VOO");
  expect(heldSince("2026-11-02", "2026-11-16T12:00:00Z")).toBe("Nov 2, 2026");
});

it("reads Fidelity's file name for the export's day, and typed amounts", () => {
  expect(exportDateFromName("Portfolio_Positions_Nov-16-2026.csv")).toBe("2026-11-16");
  expect(exportDateFromName("Portfolio_Positions_Feb-30-2026.csv")).toBeNull();
  expect(exportDateFromName("positions.csv")).toBeNull();
  expect(amountOf(" $1,250.50 ")).toBe("1250.50");
  expect(amountOf("0")).toBeNull();
  expect(amountOf("0", true)).toBe("0");
  expect(amountOf("-3")).toBeNull();
  expect(amountOf("1e3")).toBeNull();
  expect(amountOf(".5")).toBe(".5");
});

const entry = (id: string, created: string, corrects: string | null = null): JournalEntry => ({
  id, company_id: "0001045810", symbol: null, kind: "bought", decided_on: "2026-11-02", why: id, sell_rules: null,
  red_lines: [], confidence: null, corrects, created_at: created, edited_at: null,
  editable_until: "2026-11-03T12:00:00Z", kept: true,
});

it("puts corrections under the entry they correct, oldest first", () => {
  const list = [entry("c2", "2026-11-20T00:00:00Z", "a"), entry("b", "2026-11-19T00:00:00Z"),
    entry("c1", "2026-11-18T00:00:00Z", "a"), entry("a", "2026-11-02T00:00:00Z"), entry("orphan", "2026-11-01T00:00:00Z", "gone")];
  expect(withCorrections(list).map((x) => [x.entry.id, x.corrections.map((c) => c.id)])).toEqual([
    ["b", []], ["a", ["c1", "c2"]], ["orphan", []],
  ]);
  expect(editWords(entry("a", "x"))).toBe("Kept as written: change it with a correction");
  expect(editWords({ kept: false, editable_until: "2026-11-03T12:00:00Z" })).toMatch(/^You can edit or delete it until /);
});

it("routes the portfolio, a holding and the import", () => {
  expect(parse("#/portfolio")).toEqual({ view: "portfolio" });
  expect(parse("#/portfolio/import")).toEqual({ view: "portfolio", import: true });
  expect(parse("#/portfolio/3f2b8c1e-7a4d-4e2b-9c1a-0d5e6f7a8b9c")).toEqual({ view: "portfolio", holdingId: "3f2b8c1e-7a4d-4e2b-9c1a-0d5e6f7a8b9c" });
  expect(parse("#/portfolio/<script>")).toEqual({ view: "portfolio" });
  for (const r of [{ view: "portfolio" as const }, { view: "portfolio" as const, import: true }, { view: "portfolio" as const, holdingId: "abc-1" }]) {
    expect(parse(href(r))).toEqual(r);
  }
});

it("the bell counts reviews due beside new filings", () => {
  expect(bellLabel(2, 1)).toBe("Alerts: 2 new filings, 1 review due");
  expect(bellLabel(0, 3)).toBe("Alerts: 3 reviews due");
  expect(bellLabel(1)).toBe("Alerts: 1 new filing");
  expect(bellLabel(0, 0)).toBe("Alerts: nothing new");
  expect(SOURCE_WORDS.portfolio).toBe("you hold it");
});
