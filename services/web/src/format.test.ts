import { describe, expect, it } from "vitest";

import { ago, change, companyName, day, displayNames, money, notForBanks, pct, quarter } from "./format";

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

describe("notForBanks", () => {
  it("marks margin, FCF and net debt as n/a for banks only", () => {
    expect(notForBanks("National Commercial Banks", "operating_margin_ttm")).toBe(true);
    expect(notForBanks("National Commercial Banks", "net_debt_q")).toBe(true);
    expect(notForBanks("National Commercial Banks", "revenue_ttm")).toBe(false);
    expect(notForBanks("Beverages", "operating_margin_ttm")).toBe(false);
    expect(notForBanks(null, "net_debt_q")).toBe(false);
  });
});
