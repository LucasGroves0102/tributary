import { describe, expect, it } from "vitest";

import { ago, change, companyName, day, displayNames, duration, inWords, localClock, localTime, money, moneyRow, paren, pct, quarter } from "./format";

describe("moneyRow", () => {
  it("reads a row in one unit: the largest value's, or the next smaller one when a value would read under one", () => {
    expect(moneyRow([2.1e6, 4.1e6])).toEqual(["$2.10M", "$4.10M"]);
    expect(moneyRow([46.7e9, 215.9e9])).toEqual(["$46.7B", "$215.9B"]);
    // Riley, test 10, finding 80: CrowdStrike's early years read $0.12B beside $3.96B
    expect(moneyRow([119e6, 249.8e6, 3.955e9])).toEqual(["$119M", "$250M", "$3,955M"]);
    expect(moneyRow([54740, 963570, 2430000])).toEqual(["$55K", "$964K", "$2,430K"]);
    expect(moneyRow([897e6, 1.04e9, null])).toEqual(["$897M", "$1,040M", null]);
    expect(moneyRow([-198360, 2430000])).toEqual(["-$198K", "$2,430K"]);
    // the largest wouldn't fit four digits of the smaller unit: the larger stays
    expect(moneyRow([0.5e9, 12.3e9])).toEqual(["$0.50B", "$12.30B"]);
  });

  it("groups thousands (Riley, test 10, finding 85)", () => {
    expect(money(1743.84, "USD/shares")).toBe("$1,743.84");
    expect(money(4.2e15, "USD")).toBe("$4,200T");
  });

  it("never rounds a real figure to nothing", () => {
    expect(moneyRow([302e9, 12e6])).toEqual(["$302.0B", "$12.00M"]);
    expect(moneyRow([302e9, 4e6])).toEqual(["$302.0B", "$4.00M"]);
    expect(moneyRow([0, 2e6])).toEqual(["$0", "$2.00M"]);
    expect(moneyRow([640, 12])).toEqual(["$640", "$12"]);
  });
});

describe("duration", () => {
  it("reads in the unit that suits it", () => {
    expect([duration(43), duration(108), duration(7200), duration(null)]).toEqual(["43 s", "1.8 min", "2.0 h", "—"]);
  });
});

describe("money", () => {
  it("scales USD", () => {
    expect(money(96221000000, "USD")).toBe("$96.22B");
    expect(money(812400000, "USD")).toBe("$812.4M");
    expect(money(4.2e12, "USD")).toBe("$4.20T");
    expect(money(-1300000000, "USD")).toBe("-$1.30B");
  });

  it("shows per-share values in dollars", () => {
    expect(money(1.05, "USD/shares")).toBe("$1.05");
    expect(money(-0.4, "USD/shares")).toBe("-$0.40");
  });

  it("shows another currency as its symbol or its code, never as dollars (migration 056)", () => {
    expect(money(2097429000, "EUR")).toBe("€2.10B");
    expect(money(1.87, "EUR/shares")).toBe("€1.87");
    expect(money(-1.2e9, "CAD")).toBe("-CAD 1.20B");
    expect(moneyRow([4.94e9, 1.1e9], "EUR")).toEqual(["€4.94B", "€1.10B"]);
    expect(moneyRow([4.94e9, null], "BRL")).toEqual(["BRL 4.94B", null]);
    expect(money(3, "shares")).toBe("$3.00"); // as before: not a currency
  });
});

it("formats ratios, growth and dates", () => {
  expect(pct(0.21345)).toBe("21.3%");
  expect(change(0.123)).toBe("+12.3%");
  expect(change(-0.04)).toBe("-4.0%");
  expect(day("2026-10-28")).toBe("Oct 28");
  expect(day("2026-01-05", true)).toBe("Jan 5, 2026");
});

it("labels quarters by their end month", () => {
  expect(quarter("2026-07-26")).toBe("Jul '26");
  expect(quarter("2025-12-31")).toBe("Dec '25");
});

it("describes ages", () => {
  const now = Date.parse("2026-10-19T12:00:00Z");
  expect(ago("2026-10-19T11:59:30Z", now)).toBe("just now");
  expect(ago("2026-10-19T11:57:00Z", now)).toBe("3 min ago");
  expect(ago("2026-10-19T07:00:00Z", now)).toBe("5 h ago");
  expect(ago("2026-10-17T12:00:00Z", now)).toBe("2 d ago");
});

describe("companyName", () => {
  it.each([
    ["COCA COLA CO", "Coca-Cola"],
    ["COSTCO WHOLESALE CORP /NEW", "Costco Wholesale"],
    ["JPMORGAN CHASE & CO", "JPMorgan Chase"],
    ["McDONALD’S CORPORATION", "McDonald's"],
    ["HOME DEPOT, INC.", "Home Depot"],
    ["The Home Depot, Inc.", "Home Depot"],
    ["Alphabet Inc.", "Alphabet"],
    ["PEPSICO INC", "PepsiCo"],
    ["NVIDIA CORP", "Nvidia"],
    ["AMAZON COM INC", "Amazon"],
    ["UNITEDHEALTH GROUP INC", "UnitedHealth Group"],
    ["KEURIG DR PEPPER INC.", "Keurig Dr Pepper"],
    ["JOHNSON & JOHNSON", "Johnson & Johnson"],
    ["EXXON MOBIL CORP", "Exxon Mobil"],
    ["VISA INC.", "Visa"],
    ["MICROSOFT CORP", "Microsoft"],
    ["META PLATFORMS, INC.", "Meta Platforms"],
    ["TESLA, INC.", "Tesla"],
    ["Nvidia", "Nvidia"],
  ])("%s → %s", (raw, want) => expect(companyName(raw)).toBe(want));

  it("rewrites names only on objects that carry a company_id", () => {
    expect(displayNames({ rows: [{ company_id: "1", name: "COCA COLA CO" }], name: "KEEP" })).toEqual({
      rows: [{ company_id: "1", name: "Coca-Cola" }], name: "KEEP" });
  });
});

describe("Seal: negatives in parentheses, amounts in words", () => {
  it("puts a negative amount in parentheses and leaves everything else alone", () => {
    expect(paren("-$6.05B")).toBe("($6.05B)");
    expect(paren("-€2.10B")).toBe("(€2.10B)");
    expect(paren("-CAD 1.20B")).toBe("(CAD 1.20B)");
    expect(paren("-$0.12")).toBe("($0.12)");
    expect(paren("$6.05B")).toBe("$6.05B");
    expect(paren("-4.0%")).toBe("-4.0%");
    expect(paren("-0.40x")).toBe("-0.40x");
    expect(paren("-1,022,239")).toBe("-1,022,239");
    expect(paren("—")).toBe("—");
    expect(paren("n/m")).toBe("n/m");
  });

  it("writes an amount's scale as a word", () => {
    expect(inWords("$303.0B")).toBe("$303.0 billion");
    expect(inWords("€2.10M")).toBe("€2.10 million");
    expect(inWords("$1.20T")).toBe("$1.20 trillion");
    expect(inWords("$950")).toBe("$950");
  });
});

describe("localTime (#23: one way to write a moment)", () => {
  const at = "2026-11-11T05:14:00Z";
  it("is the viewer's date and clock with their zone's abbreviation", () => {
    expect(localTime(at, false, "America/Phoenix")).toBe("Nov 10, 10:14 PM MST");
    expect(localTime(at, true, "America/Phoenix")).toBe("Nov 10, 2026, 10:14 PM MST");
    expect(localTime(at, true, "America/New_York")).toBe("Nov 11, 2026, 12:14 AM EST");
    expect(localTime(at, true, "UTC")).toBe("Nov 11, 2026, 5:14 AM UTC");
    expect(localClock(at, "America/Los_Angeles")).toBe("9:14 PM PST");
  });
  it("follows the viewer's own zone when none is given", () => {
    const before = process.env.TZ;
    try {
      process.env.TZ = "America/Phoenix";
      expect(localTime(at, true)).toBe("Nov 10, 2026, 10:14 PM MST");
      expect(localClock(new Date(at))).toBe("10:14 PM MST");
    } finally {
      if (before === undefined) delete process.env.TZ;
      else process.env.TZ = before;
    }
  });
  it("leaves what doesn't parse as written", () => {
    expect(localTime("earlier")).toBe("earlier");
  });
});

it("never claims more than four significant figures: $227.0M, not $227.00M (Riley 123)", () => {
  expect(money(227e6, "USD")).toBe("$227.0M");
  expect(money(46.74e9, "USD")).toBe("$46.74B");
  // a row takes its largest value's decimals, so its points line up
  expect(moneyRow([227e6, 46.7e6])).toEqual(["$227.0M", "$46.7M"]);
});
