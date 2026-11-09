import { describe, expect, it } from "vitest";

import { ago, change, companyName, day, displayNames, duration, money, moneyRow, pct, quarter } from "./format";

describe("moneyRow", () => {
  it("reads a row in the largest value's unit", () => {
    expect(moneyRow([54740, 963570, 2430000])).toEqual(["$0.05M", "$0.96M", "$2.43M"]);
    expect(moneyRow([897e6, 1.04e9, null])).toEqual(["$0.90B", "$1.04B", null]);
    expect(moneyRow([-198360, 2430000])).toEqual(["-$0.20M", "$2.43M"]);
  });

  it("never rounds a real figure to nothing", () => {
    expect(moneyRow([302e9, 12e6])).toEqual(["$302.00B", "$0.01B"]);
    expect(moneyRow([302e9, 4e6])).toEqual(["$302.00B", "$4.00M"]);
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
    expect(money(812400000, "USD")).toBe("$812.40M");
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
