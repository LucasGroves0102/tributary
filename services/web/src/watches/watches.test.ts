import { expect, it } from "vitest";

import type { ScreenAlert, ScreenWatch } from "../types";
import type { ColumnSpec } from "../views/model";
import { alertHeadline, crossingFigure, fromThreshold, lastCheck, lineLabel, lineWords, toThreshold, typedAs } from "./watches";

const OM: ColumnSpec = { key: "operating_margin_ttm", label: "Op mgn", long: "Operating margin", period: "TTM", kind: "pct" };

it("types a ratio as a percent, an amount in millions and the rest as shown, and reads them back", () => {
  expect([typedAs("pct"), typedAs("growth"), typedAs("usd"), typedAs("x"), typedAs("eps")]).toEqual(["percent", "percent", "millions", "plain", "plain"]);
  expect(toThreshold("pct", "25")).toBe("0.25");
  expect(toThreshold("growth", "-12.5")).toBe("-0.125");
  expect(toThreshold("usd", "1,500")).toBe("1500000000");
  expect(toThreshold("usd", "0.1")).toBe("100000");
  expect(toThreshold("x", "1.5")).toBe("1.5");
  expect(toThreshold("pct", "")).toBeNull();
  expect(toThreshold("pct", "abc")).toBeNull();
  for (const [kind, typed] of [["pct", "25"], ["usd", "1500"], ["x", "2.25"], ["growth", "33.3"]] as const) {
    expect(fromThreshold(kind, toThreshold(kind, typed)!)).toBe(typed);
  }
});

it("words a line and an alert's figures the way the screener shows them", () => {
  expect(lineLabel(OM)).toBe("Operating margin (TTM)");
  expect(lineWords({ label: "Operating margin (TTM)", direction: "above", threshold: "0.25" }, "pct")).toBe("Operating margin (TTM) above 25.0%");
  expect(crossingFigure({ unit: "ratio", display: "percent" }, "0.312")).toBe("31.2%");
  expect(crossingFigure({ unit: "ratio", display: "multiple" }, "1.5")).toBe("1.50x");
  expect(crossingFigure({ unit: "USD", display: null }, "2500000000")).toMatch(/^\$2\.5\d?B$/);
});

it("says what the last check found, the first check being only a record", () => {
  const w = { outcome: null, companies: null } as unknown as ScreenWatch;
  expect(lastCheck(null, 500)).toBeNull();
  expect(lastCheck(w, 500)).toMatch(/^Not checked yet/);
  expect(lastCheck({ ...w, outcome: "baseline", companies: 37 }, 500)).toBe("First check done: 37 companies in it now. Alerts start with the next night's figures.");
  expect(lastCheck({ ...w, outcome: "ok", companies: 1 }, 500)).toBe("1 company in it at the last check.");
  expect(lastCheck({ ...w, outcome: "too_broad", companies: 900 }, 500)).toMatch(/More than 500 companies/);
  expect(lastCheck({ ...w, outcome: "failed" }, 500)).toMatch(/couldn't read it/);
});

it("heads an alert with what happened, counting the companies it didn't list", () => {
  const a = { hidden: 0, entered: { companies: ["1", "2"], more: 3 }, left: { companies: [], more: 1 },
    crossed: [{ companies: [{ company_id: "1", value: "1" }], more: 0 }] } as unknown as ScreenAlert;
  expect(alertHeadline(a)).toBe("1 crossed a line, 5 entered, 1 left");
  expect(alertHeadline({ hidden: 2 } as ScreenAlert)).toBe("Changes to companies you can no longer read");
});
