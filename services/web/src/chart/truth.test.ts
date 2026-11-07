/** How charts tell the truth, wherever they draw: titles track the layers, gaps stay gaps, an axis
 * holds one unit, and what's left out is said under the chart. */

import { describe, expect, it } from "vitest";

import type { FormulaSeries } from "../types";
import { autoSubtitle } from "./deck";
import { drawnIds, planAxes } from "./axes";
import { dateX, footnoteLines, layout, niceScale } from "./layout";
import { markersFor, type Marker } from "./markers";
import { formatValue, metricLabel, unitLabel } from "./metrics";
import {
  applyTemplate,
  autoTitle,
  basis,
  basisReason,
  chartTitle,
  companiesChart,
  companyChart,
  emptyChart,
  formulaReads,
  layerCompanies,
  restoreChart,
  TEMPLATES,
  type ChartState,
  type Layer,
} from "./model";
import { chartNotes } from "./notes";
import { annualPoints, chartCsv, toSeries, xAxis, type Point, type Series } from "./series";

const NVDA = "0001045810";
const MSFT = "0000789019";
const short = (id: string) => (id === NVDA ? "NVDA" : id === MSFT ? "MSFT" : "?");
const metric = (id: number, companyId: string, name: string, o: Partial<Layer> = {}): Layer =>
  ({ id, kind: "metric", companyId, metric: name, transform: "ttm", type: "line", axis: "L", ...o });
const formula = (id: number, text: string, o: Partial<Layer> = {}): Layer =>
  ({ id, kind: "formula", formula: text, transform: "ttm", type: "line", axis: "L", ...o });
const chart = (layers: Layer[], o: Partial<ChartState> = {}): ChartState => ({ ...emptyChart(), layers, nextId: layers.length + 1, ...o });
const title = (s: ChartState) => autoTitle(s, short, metricLabel);

const point = (key: number, period: string, y: number | null = 1): Point =>
  ({ key, period, periodEnd: `${Math.floor(key / 4)}-0${(key % 4) * 3 + 1}-28`, y, raw: String(y), sources: [] });
/** Quarters since year 0: FY2020 Q1 is 8080. */
const quarter = (year: number, q: number, y: number | null = 1) => point(year * 4 + q - 1, `FY${year} Q${q}`, y);
const year = (y: number, v: number | null = 1) => point(y * 4 + 3, `FY${y}`, v);
const series = (id: number, points: Point[], o: Partial<Series> = {}): Series =>
  ({ id, name: `S${id}`, unit: "USD", color: id, type: "line", axis: "L", points, ...o });
const answer = (values: [number, string, string][], o: Partial<FormulaSeries> = {}): FormulaSeries => ({
  formula: "x", unit: "USD", basis: "fiscal", view: "ttm", alignment: "fiscal", as_of: "", inputs: [],
  points: values.map(([period_key, period, value]) => ({ period, period_key, period_end: "2026-01-01", value, inputs: [], source_accessions: [] })),
  ...o,
});

describe("a chart's title", () => {
  it("is written from the layers, and follows them", () => {
    const one = companyChart(emptyChart(), NVDA);
    expect(one.title).toBe("");
    expect(title(one)).toBe("NVDA: revenue and operating margin (TTM)");
    const moved = { ...one, layers: one.layers.map((l) => ({ ...l, companyId: MSFT })) };
    expect(title(moved)).toBe("MSFT: revenue and operating margin (TTM)"); // never the old company's
    expect(title({ ...one, layers: one.layers.slice(0, 1) })).toBe("NVDA: revenue");
  });

  it("names several companies of one metric, and a formula as its text", () => {
    expect(title(companiesChart(emptyChart(), [NVDA, MSFT], "operating_margin", "ttm"))).toBe("Operating margin (TTM): NVDA and MSFT");
    expect(title(chart([formula(1, "NVDA.revenue / MSFT.revenue", { defaultCompany: MSFT })]))).toBe("NVDA.revenue / MSFT.revenue (TTM)");
    expect(title(chart([metric(1, NVDA, "revenue"), formula(2, "net_income / revenue", { defaultCompany: NVDA })])))
      .toBe("NVDA: revenue (TTM) and net_income / revenue (TTM)");
    expect(title(chart([metric(1, NVDA, "revenue"), metric(2, MSFT, "net_income")]))).toBe("NVDA and MSFT: revenue (TTM) and net income (TTM)");
  });

  it("says annual with Annual on, and never TTM there", () => {
    const s = chart([metric(1, NVDA, "free_cash_flow")], { frequency: "annual" });
    expect(title(s)).toBe("NVDA: free cash flow, annual");
    expect(title(chart([metric(1, NVDA, "revenue", { transform: "yoy" })], { frequency: "annual" }))).toBe("NVDA: revenue YoY, annual");
  });

  it("leaves out what is hidden, unfinished or known to draw nothing, but is never empty for that", () => {
    const layers = [metric(1, NVDA, "revenue"), metric(2, NVDA, "dividends_paid"), metric(3, NVDA, "net_income", { hidden: true }), formula(4, " ")];
    expect(title(chart(layers))).toBe("NVDA: revenue (TTM) and dividends paid (TTM)");
    expect(autoTitle(chart(layers), short, metricLabel, (l) => l.id === 1)).toBe("NVDA: revenue (TTM)");
    expect(autoTitle(chart(layers), short, metricLabel, () => false)).toBe("NVDA: revenue (TTM) and dividends paid (TTM)");
    expect(title(chart([]))).toBe("");
  });

  it("uses a layer's own name, without repeating its company", () => {
    expect(title(chart([metric(1, NVDA, "revenue", { name: "Sales" })]))).toBe("NVDA: Sales");
    expect(title(chart([metric(1, NVDA, "revenue", { name: "NVDA sales" })]))).toBe("NVDA sales");
  });

  it("stays what the user typed, and templates leave it to the layers", () => {
    const typed = chart([metric(1, NVDA, "revenue")], { title: "My chart" });
    expect(chartTitle(typed, short, metricLabel)).toBe("My chart");
    expect(chartTitle({ ...typed, title: "  " }, short, metricLabel)).toBe("NVDA: revenue (TTM)");
    expect(applyTemplate(typed, TEMPLATES[0]!, []).title).toBe("");
  });

  it("a chart saved before keeps its title as custom, and loads whole", () => {
    const old = restoreChart({
      title: "Margins: PEP vs KO, quarterly", range: 3, align: "calendar", markers: false, deck: false,
      layers: [{ kind: "metric", companyId: NVDA, metric: "gross_margin", transform: "ttm", type: "line", axis: "L", hidden: false }],
    });
    expect(old.title).toBe("Margins: PEP vs KO, quarterly");
    expect(old.layers[0]).not.toHaveProperty("display");
    expect(old.layers).toHaveLength(1);
  });

  it("restores a layer's display only when it is one, and only on metric and formula layers", () => {
    const s = restoreChart({ layers: [
      { kind: "formula", formula: "a / b", display: "number" },
      { kind: "metric", companyId: NVDA, metric: "revenue", display: "days" },
      { kind: "price", companyId: NVDA, transform: "q", display: "number" },
    ] });
    expect(s.layers.map((l) => l.display)).toEqual(["number", undefined, undefined]);
  });
});

describe("what a formula reads", () => {
  it("is what its text names, not the company in the box", () => {
    expect(formulaReads("DECK.revenue / DECK.net_income")).toEqual({ bare: false, tickers: ["DECK"] });
    expect(formulaReads("net_income / revenue")).toEqual({ bare: true, tickers: [] });
    expect(formulaReads("ttm(KO.revenue) / abs(revenue[-4q])")).toEqual({ bare: true, tickers: ["KO"] });
    expect(formulaReads("KO.revenue[-1y] / 2")).toEqual({ bare: false, tickers: ["KO"] });
    expect(formulaReads("")).toEqual({ bare: false, tickers: [] });
    const unused = formula(1, "DECK.revenue / DECK.net_income", { defaultCompany: MSFT });
    expect(layerCompanies(unused)).toEqual(["ticker:DECK"]);
  });

  it("decides the basis: one company stays fiscal, and the reason is there when it can't", () => {
    const deck = chart([formula(1, "DECK.revenue / DECK.net_income", { defaultCompany: MSFT })], { align: "fiscal" });
    expect(basis(deck)).toBe("fiscal");
    expect(basisReason(deck)).toBeNull();
    const two = chart([metric(1, NVDA, "revenue"), metric(2, MSFT, "revenue")], { align: "fiscal" });
    expect(basis(two)).toBe("calendar");
    expect(basisReason(two)).toBe("Calendar quarters: fiscal years only line up within one company, and this chart reads 2.");
    expect(basisReason({ ...two, align: "calendar" })).toBeNull();
    const priced = chart([{ id: 1, kind: "price", companyId: NVDA, transform: "q", type: "line", axis: "L" }], { align: "fiscal" });
    expect(basisReason(priced)).toBe("Calendar quarters: share prices have no fiscal calendar.");
  });
});

describe("the time axis", () => {
  it("keeps a missing quarter as a tick no series has", () => {
    const ticks = xAxis([series(1, [quarter(2017, 1), quarter(2017, 4)])]);
    expect(ticks.map((t) => [t.period, !!t.gap])).toEqual([
      ["FY2017 Q1", false], ["FY2017 Q2", true], ["FY2017 Q3", true], ["FY2017 Q4", false],
    ]);
  });

  it("keeps a missing year: annual points tick every fourth key", () => {
    const pts = annualPoints([quarter(2016, 4), quarter(2018, 4), quarter(2019, 1)]);
    expect(pts.map((p) => p.period)).toEqual(["FY2016", "FY2018"]);
    expect(xAxis([series(1, pts)]).map((t) => [t.period, !!t.gap])).toEqual([["FY2016", false], ["FY2017", true], ["FY2018", false]]);
  });

  it("doesn't fill keys that aren't periods: a model's fiscal years and forecasts", () => {
    const model = [point(2025, "FY2025"), point(2028, "FY2028E")];
    expect(xAxis([series(1, model)]).map((t) => t.period)).toEqual(["FY2025", "FY2028E"]);
    expect(xAxis([series(1, [point(2025, "FY2025"), point(2028, "FY2028")])])).toHaveLength(2);
  });

  it("gives the CSV a row for the gap", () => {
    const csv = chartCsv([series(1, [year(2016), year(2018)])]);
    expect(csv.split("\r\n").slice(1, 4)).toEqual(["FY2016,1,", "FY2017,,", "FY2018,1,"]);
  });
});

describe("drawing a gap", () => {
  const holed = series(1, [quarter(2020, 1), quarter(2020, 2), quarter(2020, 4), quarter(2021, 1)]);

  it("breaks a line, and shades the period", () => {
    const lay = layout([holed], false);
    const path = lay.drawn[0]!.path!;
    expect(path.match(/M/g)).toHaveLength(2);
    expect(lay.drawn[0]!.ys[2]).toBeNull();
    expect(lay.gaps).toHaveLength(1);
    expect(lay.gaps[0]!.x).toBeLessThan(lay.x[2]!);
    expect(lay.gaps[0]!.x + lay.gaps[0]!.w).toBeGreaterThan(lay.x[2]!);
  });

  it("merges neighbouring empty periods into one shaded stretch", () => {
    const lay = layout([series(1, [quarter(2020, 1), quarter(2021, 2)])], false);
    expect(lay.gaps).toHaveLength(1);
    expect(lay.gaps[0]!.x + lay.gaps[0]!.w).toBeGreaterThan(lay.x[4]!);
  });

  it("breaks an area into one shape per run", () => {
    const lay = layout([{ ...holed, type: "area" }], false);
    expect(lay.drawn[0]!.area!.match(/Z/g)).toHaveLength(2);
  });

  it("leaves no bar in a missing year", () => {
    const lay = layout([series(1, [year(2016), year(2018)], { type: "bar" })], false);
    expect(lay.ticks).toHaveLength(3);
    expect(lay.drawn[0]!.bars).toHaveLength(2);
  });

  it("gives a point with no neighbours a dot: a line alone can't show it", () => {
    const lay = layout([series(1, [quarter(2020, 1), quarter(2020, 3), quarter(2020, 4)])], false);
    expect(lay.drawn[0]!.dots).toHaveLength(1);
    expect(lay.drawn[0]!.dots![0]!.x).toBe(lay.x[0]);
    expect(layout([series(1, [quarter(2020, 1)])], false).drawn[0]!.dots).toHaveLength(1);
  });

  it("lists only what is drawn in the legend", () => {
    const lay = layout([series(1, [quarter(2020, 1), quarter(2020, 2)]), series(2, [])], false);
    expect(lay.legend.map((l) => l.name)).toEqual(["S1"]);
    expect(layout([series(1, [quarter(2020, 1, null)])], false).legend).toEqual([]);
  });
});

describe("an axis holds one unit", () => {
  const q = [quarter(2020, 1, 5), quarter(2020, 2, 6)];
  const usd = (id: number, axis: "L" | "R" = "L") => series(id, q.map((p) => ({ ...p, y: 4e9 })), { axis });

  it("moves a dollar layer off a per-share axis", () => {
    const perShare = series(1, q, { unit: "USD/shares" });
    const plan = planAxes([perShare, usd(2)]);
    expect(plan.axes).toEqual(["L", "R"]);
    expect([plan.left, plan.right]).toEqual(["USD per share", "USD"]);
    const lay = layout([perShare, usd(2)], false);
    expect(lay.left!.unit).toBe("USD/shares");
    expect(lay.left!.hi).toBeLessThan(100); // not scaled to billions
    expect(lay.right!.unit).toBe("USD");
    expect(lay.grid.at(-1)!.label).toBe("$6");
    expect(lay.legend.map((l) => l.name)).toEqual(["S1", "S2 (right axis)"]);
  });

  it("splits indexed and YoY, so YoY doesn't flatten", () => {
    const lay = layout([series(1, q, { unit: "index" }), series(2, q.map((p) => ({ ...p, y: 0.1 })), { unit: "pct" })], false);
    expect(lay.left!.unit).toBe("index");
    expect(lay.right!.unit).toBe("pct");
    expect(lay.units).toEqual({ left: "index = 100", right: "% change" });
  });

  it("lets percents of every kind share one, in one caption", () => {
    const margin = series(1, q.map((p) => ({ ...p, y: 0.3 })), { unit: "ratio" });
    const growth = series(2, q.map((p) => ({ ...p, y: 0.1 })), { unit: "pct" });
    expect(planAxes([margin, growth]).axes).toEqual(["L", "L"]);
    expect(planAxes([margin, growth]).left).toBe("%");
    const parts = series(3, q.map((p) => ({ ...p, y: 0.1 })), { unit: "pts", type: "sbar" });
    const lay = layout([parts, growth], false);
    expect(lay.units.left).toBe("% points");
    expect(lay.grid.at(-1)!.label).toMatch(/ pts$/);
  });

  it("uses the requested side when it's free, and joins its own kind across", () => {
    expect(planAxes([usd(1), usd(2, "R")]).axes).toEqual(["L", "R"]);
    const ratioRight = series(3, q, { unit: "multiple", axis: "R" });
    expect(planAxes([usd(1), ratioRight, usd(2, "R")]).axes).toEqual(["L", "R", "L"]);
  });

  it("doesn't draw a third unit, and says so; its values stay", () => {
    const three = [usd(1), series(2, q, { unit: "ratio", axis: "R" }), series(3, q, { unit: "multiple", axis: "R" })];
    expect(planAxes(three).axes).toEqual(["L", "R", null]);
    const lay = layout(three, false);
    expect(lay.drawn[2]!.ys.every((y) => y === null)).toBe(true);
    expect(lay.legend.map((l) => l.id)).toEqual([1, 2]);
    expect(chartNotes(three)[0]).toBe("Not drawn: S3 reads in multiple (x), and both axes are taken (USD, %). Its values are in the table.");
    expect([...drawnIds(three)]).toEqual([1, 2]);
  });

  it("a layer with no values claims nothing", () => {
    const empty = series(1, [], { unit: "index" });
    expect(planAxes([empty, usd(2)]).axes).toEqual(["L", "L"]);
    expect(layout([empty, usd(2)], false).left!.unit).toBe("USD");
  });

  it("is the same answer twice: the plan holds for a chart already planned", () => {
    const list = [series(1, q, { unit: "USD/shares" }), usd(2)];
    const plan = planAxes(list);
    expect(planAxes(list.map((s, i) => ({ ...s, axis: plan.axes[i]! }))).axes).toEqual(plan.axes);
  });

  it("the deck subtitle reads the sides as drawn", () => {
    expect(autoSubtitle([series(1, q, { unit: "USD/shares" }), usd(2)], "quarterly", "fiscal")).toContain("USD per share (left), USD (right)");
  });
});

describe("how a dimensionless value reads", () => {
  const ratio = (o: Partial<FormulaSeries> = {}) => answer([[8080, "FY2020 Q1", "41.5"]], { unit: "ratio", ...o });
  const layer = { id: 1, transform: "ttm" as const, type: "line" as const, axis: "L" as const };

  it("takes the API's word, and a layer's own over it", () => {
    expect(toSeries(ratio({ display: "multiple" }), layer, "x", 0).unit).toBe("multiple");
    expect(toSeries(ratio({ display: "number" }), layer, "x", 0).unit).toBe("number");
    expect(toSeries(ratio({ display: "percent" }), layer, "x", 0).unit).toBe("ratio");
    expect(toSeries(ratio({ display: "multiple" }), { ...layer, display: "percent" }, "x", 0).unit).toBe("ratio");
    expect(toSeries(ratio(), layer, "x", 0).unit).toBe("ratio"); // an API without the field
  });

  it("ignores display on anything with a unit, or a change", () => {
    expect(toSeries(answer([], { unit: "USD" }), { ...layer, display: "percent" }, "x", 0).unit).toBe("USD");
    expect(toSeries(ratio({ display: "multiple" }), { ...layer, transform: "yoy" }, "x", 0).unit).toBe("pct");
  });

  it("formats each in its own way, days as a number", () => {
    expect(formatValue(41.497, "number")).toBe("41.5");
    expect(formatValue(1234.5, "number")).toBe("1,234.5");
    expect(formatValue(1.5, "multiple")).toBe("1.50x");
    expect(formatValue(1.5, "multiple", true, 0.5)).toBe("1.5x");
    expect(formatValue(2, "multiple", true, 1)).toBe("2x");
    expect(formatValue(0.5, "pts")).toBe("50.0 pts");
    expect(formatValue(0.5, "pts", true, 0.5)).toBe("50 pts");
    expect(formatValue(3.5e9, "number", true)).toBe("3.5B");
    expect(unitLabel("multiple")).toBe("multiple (x)");
    expect(unitLabel("number")).toBe("");
  });

  it("gives close ticks the decimals to tell them apart", () => {
    expect(formatValue(0.025, "ratio", true, 0.005)).toBe("2.5%");
    expect(formatValue(0.02, "ratio", true, 0.1)).toBe("2%");
    expect(formatValue(3.5, "USD/shares", true, 0.5)).toBe("$3.50");
    expect(formatValue(5e9, "USD/shares", true, 1e9)).toBe("$5B");
    expect(formatValue(2.5e9, "shares", true)).toBe("2.5B sh");
  });
});

describe("indexing", () => {
  const layer = { id: 1, transform: "idx" as const, type: "line" as const, axis: "L" as const };

  it("warns when the value goes negative, and can't start from zero or less", () => {
    const fell = answer([[8080, "FY2020 Q1", "100"], [8081, "FY2020 Q2", "-50"]]);
    expect(toSeries(fell, layer, "FCF", 0).note).toContain("FCF is below 0 where the value itself is negative");
    const none = toSeries(answer([[8080, "FY2020 Q1", "-5"], [8081, "FY2020 Q2", "10"]]), layer, "FCF", 0);
    expect(none.note).toBe("FCF can't be indexed: its first value in this range is zero or negative.");
    expect(none.points.every((p) => p.y === null)).toBe(true);
    expect(toSeries(answer([[8080, "FY2020 Q1", "5"]]), layer, "FCF", 0).note).toBeUndefined();
  });
});

describe("what a chart says about what isn't on it", () => {
  it("names a missing period and why, in one place", () => {
    const notes = chartNotes([series(1, [year(2016), year(2018)], { name: "AMZN FCF" })]);
    expect(notes[0]).toBe("AMZN FCF: no value for FY2017.");
    expect(notes.at(-1)).toContain("nothing is filled in");
  });

  it("names a run of quarters as a run, and many as a count", () => {
    expect(chartNotes([series(1, [quarter(2017, 1), quarter(2018, 2)])])[0]).toBe("S1: no value for FY2017 Q2–FY2018 Q1.");
    const sparse = [0, 2, 4, 6, 8, 10].map((k) => quarter(2020 + Math.floor(k / 4), (k % 4) + 1));
    expect(chartNotes([series(1, sparse)])[0]).toBe("S1: no value for 5 of 11 periods.");
  });

  it("says when a series starts late or stops early against the others", () => {
    const all = [year(2017), year(2018), year(2019), year(2020)];
    expect(chartNotes([series(1, all), series(2, all.slice(2))])[0]).toBe("S2: starts FY2019.");
    expect(chartNotes([series(1, all), series(2, all.slice(0, 2))])[0]).toBe("S2: stops at FY2018.");
  });

  it("doesn't call one quarter behind the others a gap: companies file at different times", () => {
    const all = [quarter(2020, 1), quarter(2020, 2), quarter(2020, 3)];
    expect(chartNotes([series(1, all), series(2, all.slice(0, 2))])).toEqual([]);
    expect(chartNotes([series(1, all), series(2, all.slice(0, 1))])[0]).toBe("S2: stops at FY2020 Q1.");
  });

  it("names layers with nothing to draw", () => {
    expect(chartNotes([series(1, [year(2020)]), series(2, [], { name: "AMZN dividends paid" })])).toEqual(["No values in this range: AMZN dividends paid."]);
  });

  it("says when nothing goes back as far as the chart asked", () => {
    const pts = [year(2020), year(2021)];
    pts[0]!.periodEnd = "2020-01-26";
    expect(chartNotes([series(1, pts)], { from: "2016-08-01" })).toEqual(["Nothing on file before FY2020 in the range asked for."]);
    expect(chartNotes([series(1, pts)], { from: "2019-12-01" })).toEqual([]);
    expect(chartNotes([series(1, pts)], { from: null })).toEqual([]);
  });

  it("a small multiple leaves the reason to the group, and notes wrap inside the SVG", () => {
    const notes = chartNotes([series(1, [year(2016), year(2018)])], { brief: true });
    expect(notes).toEqual(["S1: no value for FY2017."]);
    expect(footnoteLines(["A long note ".repeat(20)], 300).length).toBeGreaterThan(2);
  });
});

describe("drawing quality", () => {
  it("a negative a sliver past zero doesn't earn a step of empty axis", () => {
    expect(niceScale(-8331, 31e6, "USD")).toMatchObject({ lo: 0, hi: 40e6 });
    expect(niceScale(-2e6, 31e6, "USD").lo).toBeLessThan(0); // a real one still shows
    expect(niceScale(-31e6, 8331, "USD")).toMatchObject({ lo: -40e6, hi: 0 });
  });

  it("makes the margin as wide as the labels, and the caption whole", () => {
    const wide = layout([series(1, [quarter(2020, 1, 1.23e9), quarter(2020, 2, 2e9)], { unit: "USD/shares" })], false);
    expect(wide.grid.every((g) => g.label.length * 6 + 10 <= wide.plot.l)).toBe(true);
    const caption = layout([series(1, [quarter(2020, 1)], { unit: "index" })], false);
    expect(caption.units.left).toBe("index = 100");
    expect(caption.leftCaption).toEqual({ x: 4, anchor: "start" }); // too wide for the margin: runs from the edge
    expect(layout([series(1, [quarter(2020, 1)])], false).leftCaption.anchor).toBe("end");
  });

  it("at Max, no two period labels touch, and the last period is labelled", () => {
    for (const width of [900, 1400, 460, 330]) {
      const pts = Array.from({ length: 68 }, (_, i) => quarter(2009 + Math.floor(i / 4), (i % 4) + 1));
      const lay = layout([series(1, pts)], false, [], { width });
      const xs = lay.xLabels.map((l) => l.x);
      for (let i = 1; i < xs.length; i++) expect(xs[i]! - xs[i - 1]!).toBeGreaterThanOrEqual(9 * 6);
      expect(lay.xLabels.at(-1)!.label).toBe("FY2025 Q4");
    }
  });

  it("keeps a lone bar from filling the chart", () => {
    const lay = layout([series(1, [year(2020)], { type: "bar" })], false);
    expect(lay.drawn[0]!.bars![0]!.w).toBeLessThanOrEqual(56);
  });

  it("a log scale keeps both a $38M and a $98B company readable, and leaves out what it can't draw", () => {
    const small = series(1, [quarter(2020, 1, 38e6), quarter(2020, 2, -5e6), quarter(2020, 3, 40e6)]);
    const big = series(2, [quarter(2020, 1, 98e9), quarter(2020, 2, 99e9), quarter(2020, 3, 98e9)]);
    const lay = layout([small, big], false, [], { log: true });
    expect(lay.left).toMatchObject({ log: true, lo: 1e7, hi: 1e11 });
    expect(lay.grid.map((g) => g.label)).toEqual(["$10M", "$100M", "$1B", "$10B", "$100B"]);
    expect(lay.units.left).toBe("USD, log scale");
    expect(lay.drawn[0]!.ys[1]).toBeNull();
    // a $38M line sits well above the floor, not flat on it as on a dollar axis
    const floor = lay.plot.b;
    expect(floor - lay.drawn[0]!.ys[0]!).toBeGreaterThan(30);
    const linear = layout([small, big], false);
    expect(linear.plot.b - linear.drawn[0]!.ys[0]!).toBeLessThan(2);
  });

  describe("filing markers", () => {
    const mark = (kind: Marker["kind"], filed: string, accessionNo: string): Marker =>
      ({ companyId: NVDA, accessionNo, kind, filed, glyph: { "10-K": "K", "10-Q": "Q", "8-K": "8" }[kind], label: kind, color: 0 });
    const pts = Array.from({ length: 12 }, (_, i) => {
      const y = 2020 + Math.floor(i / 4);
      return { ...quarter(y, (i % 4) + 1), periodEnd: `${y}-${String((i % 4) * 3 + 3).padStart(2, "0")}-28` };
    });

    it("have their own band: the period labels sit below them", () => {
      const without = layout([series(1, pts)], false);
      const lay = layout([series(1, pts)], false, [mark("10-Q", "2021-05-01", "a")]);
      expect(without.axisY).toBe(without.plot.b + 18);
      expect(lay.markers[0]!.y).toBeLessThan(lay.axisY - 8);
      expect(lay.axisY).toBeGreaterThan(lay.plot.b + 18);
    });

    it("never put two letters on one another, 10-Ks first", () => {
      const lay = layout([series(1, pts)], false, [
        mark("10-Q", "2021-05-01", "a"), mark("10-K", "2021-05-02", "b"), mark("8-K", "2021-05-03", "c"), mark("10-Q", "2021-11-01", "d"),
      ]);
      const letters = lay.markers.filter((m) => m.letter);
      expect(letters.map((m) => m.kind)).toEqual(["10-K", "10-Q"]);
      expect(lay.markers).toHaveLength(4); // the rest stay as stems with their hover
    });

    it("mark only 10-Ks on a chart of years", () => {
      const all = [mark("10-K", "2021-02-01", "a"), mark("10-Q", "2021-05-01", "b"), mark("8-K", "2021-05-02", "c")];
      expect(markersFor(all, "annual").map((m) => m.kind)).toEqual(["10-K"]);
      expect(markersFor(all, "quarterly")).toHaveLength(3);
    });

    it("place a filing after the last year by the year's length, not a quarter's", () => {
      const ends = ["2021-12-31", "2022-12-31"];
      const x = dateX("2023-02-01", ends, [100, 200], 400)!;
      expect(x).toBeGreaterThan(200);
      expect(x).toBeLessThan(220); // a month past: a twelfth of the way to the next year
    });
  });
});
