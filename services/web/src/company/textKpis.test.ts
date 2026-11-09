import { expect, it } from "vitest";

import type { TextKpis } from "../types";
import NVDA from "./fixtures/nvda-text-kpis.json";
import VRT from "./fixtures/vrt-text-kpis.json";
import {
  conflictNote, excerpt, figure, growthBasis, hasFigures, quarterOf, textKpiSeries, when, writtenIn,
} from "./textKpis";

// The branch's Query API over Vertiv's and NVIDIA's production sections (2026-11-09)
const vrt = VRT as unknown as TextKpis;
const nvda = NVDA as unknown as TextKpis;

it("keys a date by its calendar quarter, like the chart builder's periods", () => {
  expect(quarterOf("2025-09-30")).toEqual({ key: 2025 * 4 + 2, period: "CY2025 Q3" });
  expect(quarterOf("2023-09-24")).toEqual({ key: 2023 * 4 + 2, period: "CY2023 Q3" });
  expect(quarterOf("2025-12-31").period).toBe("CY2025 Q4");
});

it("draws backlog as bars and order growth as a line, one basis", () => {
  const [backlog, growth] = textKpiSeries(vrt, "0001674101");
  expect(backlog!.name).toBe("Backlog (from the filing's text)");
  expect(backlog!.type).toBe("bar");
  expect(backlog!.points.map((p) => p.period)).toEqual(
    ["CY2021 Q3", "CY2021 Q4", "CY2022 Q4", "CY2023 Q4", "CY2024 Q4", "CY2025 Q1", "CY2025 Q3", "CY2025 Q4"]);
  expect(backlog!.points.at(-1)!.y).toBe(15e9);
  expect(backlog!.points.at(-1)!.sources[0]).toMatchObject({ form: "10-K", companyId: "0001674101" });
  expect(growthBasis(vrt.metrics.orders_growth)).toBe("organic");
  expect(growth!.name).toBe("Organic order growth, year on year (from the filing's text)");
  expect(growth!.unit).toBe("pct");
  expect(growth!.points.at(-1)!.y).toBeCloseTo(2.52);
  // the reported-basis quarters aren't mixed into the organic line
  const reported = vrt.metrics.orders_growth.points.filter((p) => p.basis === "reported").length;
  expect(growth!.points.length).toBe(vrt.metrics.orders_growth.points.length - reported);
});

it("words a figure as written and where", () => {
  const [latest] = vrt.metrics.backlog.points;
  expect(figure(latest!)).toBe("$15.00B");
  expect(when(latest!)).toBe("Dec 31, 2025");
  expect(writtenIn(latest!.sources[0]!)).toBe("10-K filed Feb 13, 2026 · Business");
  const q3 = vrt.metrics.orders_growth.points.find((p) => p.period_end === "2025-09-30")!;
  expect([figure(q3), when(q3), q3.basis]).toEqual(["60%", "quarter to Sep 30, 2025", "organic"]);
  expect(writtenIn(q3.sources[0]!)).toMatch(/^8-K filed Oct 22, 2025 · Press release$/);
  const fourth = vrt.metrics.orders_growth.points.find((p) => p.period_end === "2021-12-31")!;
  expect(figure(fourth)).toBe("51.2%");
});

it("quotes the passage around its figure", () => {
  for (const p of vrt.metrics.backlog.points) {
    const s = p.sources[0]!;
    const quoted = excerpt(s, 120);
    expect(quoted).toContain(s.written);
    expect(quoted.replace(/^… | …$/g, "").length).toBeLessThanOrEqual(120);
  }
});

it("says when a company states none of it", () => {
  expect(hasFigures(vrt)).toBe(true);
  expect(hasFigures(nvda)).toBe(false);
  expect(textKpiSeries(nvda, "0001045810")).toEqual([]);
  expect(conflictNote(vrt)).toBeNull();
  const settled = structuredClone(vrt);
  settled.metrics.backlog.conflicts = [
    { period_end: "2021-12-31", values: [59828e6, 377499e6], kept: 377499e6 },
    { period_end: "2025-09-30", values: [2.0e9, 2.9e9], kept: null }];
  expect(conflictNote(settled)).toBe("1 date left out where the filings state different figures; 1 date where a "
    + "segment's or another figure disagreed: the table's total, the company-wide wording or the figure more filings state was kept.");
});
