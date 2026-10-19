import { describe, expect, it } from "vitest";

import { ago, money, quarter } from "./format";

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
