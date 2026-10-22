import { describe, expect, it } from "vitest";

import { NVDA } from "../fixtures";
import type { Filing, FormulaSeries, IndexedFiling } from "../types";
import { dateX, layout, nearestSeries, spans } from "./layout";
import { filingMarkers } from "./markers";
import { metricLabel } from "./metrics";
import {
  applyTemplate,
  autoName,
  emptyChart,
  fromDate,
  layerColor,
  layerName,
  moveLayer,
  request,
  restoreChart,
  TEMPLATES,
  type ChartState,
  type Layer,
} from "./model";
import { toSeries, xAxis } from "./series";

const MSFT = "0000789019";
const TODAY = new Date("2026-10-21T12:00:00Z");
const ttm = (m: string) => !m.startsWith("eps") && !m.startsWith("weighted");
const metric = (over: Partial<Layer> = {}): Layer => ({
  id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "q", type: "line", axis: "L", ...over,
});
const chart = (layers: Layer[], over: Partial<ChartState> = {}): ChartState => ({ ...emptyChart(), layers, ...over });

function data(values: (string | null)[], over: Partial<FormulaSeries> = {}): FormulaSeries {
  const points = values.flatMap((v, i) => (v === null ? [] : [{
    period: `CY2026 Q${i + 1}`, period_key: 8104 + i, period_end: `2026-0${3 * i + 3}-30`, value: v, source_accessions: [],
    inputs: [{
      input: 0, period_start: null, period_end: `2026-0${3 * i + 3}-30`, value: v, value_source: "reported" as const,
      citation: { accession_no: `0001045810-26-00001${i}`, form: "10-Q", filed: `2026-0${3 * i + 4}-20`, source_accessions: null, filing_url: null },
      freshness: { filed: "", ingested_at: "" },
    }],
  }]));
  return {
    formula: "revenue", unit: "USD", basis: "calendar", view: "quarter", alignment: "calendar", as_of: "",
    inputs: [{ input: 0, ref: "revenue", ticker: null, company_id: NVDA, metric: "revenue", span: "quarter", lag_quarters: 0, unit: "USD" }],
    points, ...over,
  };
}

describe("transforms", () => {
  it("QoQ is quarter over quarter on quarterly values", () => {
    expect(request(metric({ transform: "qoq" }), chart([]), ttm, TODAY)).toMatchObject({
      formula: "(revenue) / (revenue)[-1q] - 1", view: "quarter",
    });
    expect(toSeries(data(["1", "2"]), metric({ transform: "qoq" }), "x", 0).unit).toBe("pct");
  });

  it("per share divides TTM dollars by the quarter's diluted shares", () => {
    expect(request(metric({ transform: "ps", metric: "free_cash_flow" }), chart([]), ttm, TODAY)).toMatchObject({
      formula: "ttm(free_cash_flow) / weighted_shares_diluted", view: "quarter", default_company: NVDA,
    });
    const f: Layer = { ...metric(), kind: "formula", formula: "dividends_paid + share_buybacks", companyId: undefined, defaultCompany: NVDA, transform: "ps" };
    expect(request(f, chart([]), ttm, TODAY)?.formula).toBe("ttm(dividends_paid + share_buybacks) / weighted_shares_diluted");
  });

  it("max asks for every quarter; 1y and 10y go back that far", () => {
    expect(request(metric(), chart([], { range: "max" }), ttm, TODAY)).not.toHaveProperty("from");
    expect(fromDate("max", TODAY)).toBeNull();
    expect(fromDate(1, TODAY)).toBe("2025-07-21");
    expect(fromDate(10, TODAY)).toBe("2016-07-21");
  });
});

describe("layers", () => {
  const short = (id: string) => (id === NVDA ? "NVDA" : "MSFT");

  it("take a custom legend name, falling back to the automatic one", () => {
    expect(layerName(metric({ name: "  Nvidia sales " }), short, metricLabel)).toBe("Nvidia sales");
    expect(layerName(metric({ name: " " }), short, metricLabel)).toBe("NVDA revenue");
    expect(autoName(metric({ transform: "ps" }), short, metricLabel)).toBe("NVDA revenue per share");
    expect(autoName(metric({ transform: "qoq" }), short, metricLabel)).toBe("NVDA revenue QoQ");
  });

  it("keep their colours when reordered", () => {
    const s = chart([metric(), metric({ id: 2, companyId: MSFT }), metric({ id: 3, color: 6 })]);
    const moved = moveLayer(s, 2, -1);
    expect(moved.layers.map((l) => l.id)).toEqual([2, 1, 3]);
    expect(moved.layers.map((l, i) => layerColor(l, i))).toEqual([1, 0, 6]);
    expect(moveLayer(s, 1, -1)).toBe(s);
    expect(moveLayer(s, 3, 1)).toBe(s);
  });

  it("restore names, colours, new types, transforms, ranges and markers; drop bad ones", () => {
    const s = restoreChart({
      range: "max", markers: true,
      layers: [
        { kind: "metric", companyId: NVDA, metric: "revenue", transform: "qoq", type: "sbar", name: "a\u0000b", color: 3 },
        { kind: "metric", companyId: NVDA, metric: "revenue", transform: "ps", type: "sarea", color: 9, name: 5 },
      ],
    });
    expect(s.range).toBe("max");
    expect(s.markers).toBe(true);
    expect(s.layers[0]).toMatchObject({ transform: "qoq", type: "sbar", name: "ab", color: 3 });
    expect(s.layers[1]).toMatchObject({ transform: "ps", type: "sarea" });
    expect(s.layers[1]).not.toHaveProperty("color");
    expect(s.layers[1]).not.toHaveProperty("name");
    expect(restoreChart({ range: 2 }).range).toBe(2); // saved before 1/10/max
    expect(restoreChart({ range: 4 }).range).toBe(3);
  });
});

describe("templates", () => {
  it("use only metrics in the formula catalog", () => {
    const catalog = new Set(["revenue", "operating_margin", "free_cash_flow", "debt_to_equity", "operating_cash_flow",
      "net_income", "cash", "long_term_debt", "current_ratio", "dividends_paid", "share_buybacks"]);
    for (const t of TEMPLATES) {
      for (const l of t.layers) {
        const names = l.metric ? [l.metric] : [...(l.formula ?? "").matchAll(/[a-z_]+/g)].map((m) => m[0]);
        for (const n of names) expect(catalog.has(n), `${t.label}: ${n}`).toBe(true);
      }
    }
  });

  it("single-company templates take the chart's first company, formula layers included", () => {
    const cfq = TEMPLATES.find((t) => t.label === "Cash-flow quality")!;
    const s = applyTemplate(chart([metric({ companyId: MSFT }), metric({ id: 2 })], { nextId: 3 }), cfq, [NVDA]);
    expect(s.layers.map((l) => [l.kind, l.companyId ?? l.defaultCompany, l.metric ?? l.formula])).toEqual([
      ["metric", MSFT, "operating_cash_flow"],
      ["metric", MSFT, "net_income"],
      ["formula", MSFT, "operating_cash_flow / net_income"],
    ]);
    expect(s.layers[2]!.name).toBe("Cash conversion (OCF / net income)");
    const capital = applyTemplate(emptyChart(), TEMPLATES.find((t) => t.label === "Capital return")!, [NVDA, MSFT]);
    expect(capital.layers.map((l) => l.type)).toEqual(["sbar", "sbar", "line"]);
    expect(new Set(capital.layers.map((l) => l.companyId))).toEqual(new Set([NVDA]));
  });
});

describe("stacks", () => {
  const a = toSeries(data(["10", "20", "-5"]), metric({ type: "sbar" }), "a", 0);
  const b = toSeries(data(["5", null, "-5"]), metric({ id: 2, type: "sbar" }), "b", 1);
  const line = toSeries(data(["40", "40", "40"]), metric({ id: 3 }), "c", 2);

  it("stack same-type layers on one axis: positives up, negatives down", () => {
    const sp = spans([a, b, line], xAxis([a, b, line]));
    expect(sp[0]).toEqual([[0, 10], [0, 20], [-5, 0]]);
    expect(sp[1]).toEqual([[10, 15], null, [-10, -5]]);
    expect(sp[2]).toEqual([[0, 40], [0, 40], [0, 40]]);
  });

  it("draw stacked bars in one slot, each segment on top of the one below", () => {
    const lay = layout([a, b], false);
    const [da, db] = lay.drawn;
    expect(da!.bars![0]!.x).toBe(db!.bars![0]!.x);
    expect(db!.bars![0]!.y + db!.bars![0]!.h).toBeCloseTo(da!.bars![0]!.y);
    expect(lay.left!.hi).toBeGreaterThanOrEqual(20);
    expect(lay.left!.lo).toBeLessThanOrEqual(-10);
  });

  it("close stacked areas over the layer below, not the zero line", () => {
    const sa = toSeries(data(["10", "20"]), metric({ type: "sarea" }), "a", 0);
    const sb = toSeries(data(["5", "5"]), metric({ id: 2, type: "sarea" }), "b", 1);
    const lay = layout([sa, sb], false);
    // b's area runs along a's top on the way back
    const aTop = lay.drawn[0]!.ys;
    expect(lay.drawn[1]!.area).toContain(`L${lay.x[1]!.toFixed(1)},${aTop[1]!.toFixed(1)}`);
  });

  it("finds the layer nearest a click", () => {
    const lay = layout([line, a], false);
    expect(nearestSeries(lay, 0, lay.drawn[0]!.ys[0]! + 2)).toBe(0);
    expect(nearestSeries(lay, 0, lay.drawn[1]!.ys[0]! - 2)).toBe(1);
  });
});

describe("filing markers", () => {
  const idx = (accession_no: string, form: string, filed: string, period: string | null = null): IndexedFiling => ({
    accession_no, company_id: NVDA, name: "Nvidia", form, filed, accepted_at: null, period_of_report: period,
    filing_url: null, document_url: null, sections: null, sections_available: false,
  });
  const parsed = (accession_no: string, form: string, filed: string, keys: string[]): Filing => ({
    accession_no, form, filed, period_of_report: null, document_url: null,
    sections: keys.map((section_key) => ({ section_key, title: null, chars: 1 })),
  });

  it("come from 10-K/10-Q index rows and 8-Ks with item 2.02", () => {
    const m = filingMarkers(NVDA, "NVDA", 2, [
      idx("0001045810-26-000001", "10-Q", "2026-05-28", "2026-04-26"),
      idx("0001045810-26-000002", "10-K", "2026-03-10", "2026-01-25"),
      idx("0001045810-26-000003", "4", "2026-03-12"),
      idx("0001045810-25-000004", "10-Q", "2025-01-01"),
    ], [
      parsed("0001045810-26-000005", "8-K", "2026-05-28", ["2.02", "9.01", "EX-99.1"]),
      parsed("0001045810-26-000006", "8-K", "2026-06-01", ["5.07"]),
    ], "2026-01-01");
    expect(m.map((x) => [x.glyph, x.filed])).toEqual([["K", "2026-03-10"], ["Q", "2026-05-28"], ["8", "2026-05-28"]]);
    expect(m[1]!.label).toBe("NVDA 10-Q filed May 28, 2026, period Apr 26, 2026 (0001045810-26-000001)");
    expect(m[2]!.label).toContain("8-K results (item 2.02)");
    expect(m.every((x) => x.color === 2 && x.companyId === NVDA)).toBe(true);
  });

  it("sit on the time axis by filing date, a little past the last quarter at most", () => {
    const ends = ["2026-03-31", "2026-06-30"];
    const xs = [100, 200];
    expect(dateX("2026-03-31", ends, xs, 300)).toBe(100);
    expect(dateX("2026-05-15", ends, xs, 300)).toBeCloseTo(100 + (45 / 91) * 100, 0);
    expect(dateX("2026-07-30", ends, xs, 300)).toBeCloseTo(200 + (30 / 91) * 100, 0);
    expect(dateX("2026-01-01", ends, xs, 300)).toBeNull();
    expect(dateX("2027-06-30", ends, xs, 300)).toBeNull();
    const s = toSeries(data(["1", "2"]), metric(), "x", 0);
    const lay = layout([s], false, filingMarkers(NVDA, "NVDA", 0, [idx("0001045810-26-000001", "10-Q", "2026-04-20")], [], null));
    expect(lay.markers).toHaveLength(1);
    expect(lay.markers[0]!.x).toBeGreaterThan(lay.x[0]!);
    expect(lay.foot).toBeGreaterThan(layout([s], false).foot);
  });
});

it("cites the filing company on every point source", () => {
  const s = toSeries(data(["1"]), metric(), "x", 0);
  expect(s.points[0]!.sources[0]!.companyId).toBe(NVDA);
});
