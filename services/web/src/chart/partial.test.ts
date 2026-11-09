/** The year in progress on an annual chart (F5, T1's "no latest TTM or quarter on the annual charts"):
 * the newest quarter after the last fiscal year end stays, labelled as the quarter it is, marked and
 * drawn lighter, and said under the chart. */

import { describe, expect, it } from "vitest";

import type { FormulaSeries } from "../types";
import { layout } from "./layout";
import { chartNotes } from "./notes";
import { annualPoints, partialNote, toSeries, xAxis, type Point, type Series } from "./series";

const pt = (key: number, period: string, y = 1): Point =>
  ({ key, period, periodEnd: "2026-07-26", y, raw: String(y), sources: [] });
const key = (fy: number, q: number) => fy * 4 + q - 1;

function answer(view: "quarter" | "ttm", metric: string, quarters: [number, number, number][]): FormulaSeries {
  return {
    formula: metric, unit: "USD", basis: "fiscal", view, alignment: "fiscal", as_of: "2026-11-09T00:00:00Z",
    inputs: [{ input: 0, ref: metric, ticker: null, company_id: "0001045810", metric, span: view, lag_quarters: 0, unit: "USD" }],
    points: quarters.map(([fy, q, value]) => ({
      period: `FY${fy} Q${q}`, period_key: key(fy, q), period_end: "2026-07-26", value: String(value), source_accessions: [],
      inputs: [],
    })),
  };
}
const layer = { id: 1, transform: "ttm" as const, type: "bar" as const, axis: "L" as const };

describe("the year in progress on an annual chart", () => {
  it("keeps the newest quarter after the last year end, labelled as that quarter", () => {
    const pts = [pt(key(2025, 4), "FY2025 Q4"), pt(key(2026, 3), "FY2026 Q3"), pt(key(2026, 4), "FY2026 Q4"), pt(key(2027, 2), "FY2027 Q2")];
    expect(annualPoints(pts).map((p) => p.period)).toEqual(["FY2025", "FY2026"]);
    const out = annualPoints(pts, "ttm");
    expect(out.map((p) => [p.period, p.partial])).toEqual([["FY2025", undefined], ["FY2026", undefined], ["FY2027 Q2", "ttm"]]);
    // nothing after the last year end, or the newest a Q4: no partial point
    expect(annualPoints(pts.slice(0, 3), "ttm").some((p) => p.partial)).toBe(false);
  });

  it("is a TTM for a flow and a quarter end for a balance", () => {
    const flows = toSeries(answer("ttm", "revenue", [[2026, 4, 130], [2027, 1, 148], [2027, 2, 165]]), layer, "Revenue", 0, "annual");
    expect(flows.points.at(-1)).toMatchObject({ period: "FY2027 Q2", partial: "ttm" });
    const stock = toSeries(answer("ttm", "inventory", [[2026, 4, 487], [2027, 2, 861]]), layer, "Inventory", 0, "annual");
    expect(stock.points.at(-1)).toMatchObject({ period: "FY2027 Q2", partial: "quarter" });
    const quarterly = toSeries(answer("quarter", "revenue", [[2026, 4, 1], [2027, 2, 2]]), layer, "Revenue", 0);
    expect(quarterly.points.some((p) => p.partial)).toBe(false);
  });

  it("sits on the annual axis after the years, with no quarters filled in before it", () => {
    const s: Series = { id: 1, name: "Revenue", unit: "USD", color: 0, type: "bar", axis: "L",
      points: annualPoints([pt(key(2024, 4), "FY2024 Q4"), pt(key(2026, 4), "FY2026 Q4"), pt(key(2027, 2), "FY2027 Q2")], "ttm") };
    expect(xAxis([s])).toEqual([
      { key: key(2024, 4), period: "FY2024" },
      { key: key(2025, 4), period: "FY2025", gap: true },
      { key: key(2026, 4), period: "FY2026" },
      { key: key(2027, 2), period: "FY2027 Q2", partial: true },
    ]);
  });

  it("is marked * and drawn lighter, and the chart says what it is", () => {
    const bars: Series = { id: 1, name: "Revenue", unit: "USD", color: 0, type: "bar", axis: "L",
      points: annualPoints([pt(key(2025, 4), "FY2025 Q4", 100), pt(key(2026, 4), "FY2026 Q4", 130), pt(key(2027, 2), "FY2027 Q2", 165)], "ttm") };
    const line: Series = { ...bars, id: 2, name: "Margin", type: "line" };
    const lay = layout([bars, line], true);
    expect(lay.xLabels.at(-1)!.label).toBe("FY2027 Q2*");
    expect(lay.drawn[0]!.bars!.map((b) => !!b.partial)).toEqual([false, false, true]);
    expect(lay.drawn[1]!.path!.split(/[ML]/).filter(Boolean)).toHaveLength(2); // the full years
    expect(lay.drawn[1]!.tail).toBeDefined();
    expect(lay.drawn[1]!.tailDot).toBeDefined();
    expect(partialNote([bars])).toBe("* FY2027 Q2: the latest quarter, not a full fiscal year (the trailing twelve months to it).");
    const stock = { ...bars, points: bars.points.map((p) => (p.partial ? { ...p, partial: "quarter" as const } : p)) };
    expect(partialNote([bars, { ...stock, id: 3 }])).toContain("flows are the trailing twelve months to it, balances its quarter end");
    expect(chartNotes([bars])).toContain(partialNote([bars]));
  });

  it("isn't where a layer without it 'stops'", () => {
    const withIt: Series = { id: 1, name: "Revenue", unit: "USD", color: 0, type: "line", axis: "L",
      points: annualPoints([pt(key(2025, 4), "FY2025 Q4"), pt(key(2026, 4), "FY2026 Q4"), pt(key(2027, 2), "FY2027 Q2")], "ttm") };
    const without: Series = { ...withIt, id: 2, name: "Capex", points: withIt.points.filter((p) => !p.partial) };
    expect(chartNotes([withIt, without]).join(" ")).not.toContain("stops at");
  });
});
