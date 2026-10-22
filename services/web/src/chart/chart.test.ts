import { describe, expect, it } from "vitest";

import { fin, NVDA } from "../fixtures";
import type { FormulaSeries } from "../types";
import { svgText } from "./export";
import { layout, nearest, niceScale } from "./layout";
import { catalog, formatValue, metricLabel } from "./metrics";
import {
  applyTemplate,
  basis,
  companyChart,
  emptyChart,
  fromDate,
  layerName,
  request,
  restoreChart,
  TEMPLATES,
  type ChartState,
  type Layer,
} from "./model";
import { chartCsv, csvCell, financialSeries, latestSource, periodKey, toSeries, xAxis } from "./series";

const MSFT = "0000789019";
const TODAY = new Date("2026-10-21T12:00:00Z");
const ttm = (m: string) => !m.startsWith("eps");
const metric = (over: Partial<Layer> = {}): Layer => ({
  id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "q", type: "line", axis: "L", ...over,
});
const chart = (layers: Layer[], over: Partial<ChartState> = {}): ChartState => ({
  ...emptyChart(), layers, ...over,
});

function series(points: [number, string, string][], over: Partial<FormulaSeries> = {}): FormulaSeries {
  return {
    formula: "revenue", unit: "USD", basis: "calendar", view: "quarter", alignment: "calendar",
    as_of: "2026-10-21T12:00:00Z", inputs: [],
    points: points.map(([key, period, value], i) => ({
      period, period_key: key, period_end: `2026-0${i + 1}-30`, value, source_accessions: [],
      inputs: [{
        input: 0, period_start: null, period_end: `2026-0${i + 1}-30`, value, value_source: "reported",
        citation: { accession_no: `0001045810-26-00000${i}`, form: "10-Q", filed: `2026-0${i + 2}-15`, source_accessions: null, filing_url: null },
        freshness: { filed: `2026-0${i + 2}-15`, ingested_at: "" },
      }],
    })),
    ...over,
  };
}

describe("layer requests", () => {
  it("make a metric layer a bare-metric formula with its company as default", () => {
    expect(request(metric(), chart([metric()]), ttm, TODAY)).toEqual({
      formula: "revenue", default_company: NVDA, basis: "calendar", view: "quarter", from: "2023-07-21",
    });
  });

  it("map transforms to views and formulas", () => {
    const s = chart([]);
    expect(request(metric({ transform: "ttm" }), s, ttm, TODAY)?.view).toBe("ttm");
    expect(request(metric({ transform: "yoy" }), s, ttm, TODAY)).toMatchObject({
      formula: "(revenue) / (revenue)[-4q] - 1", view: "ttm",
    });
    expect(request(metric({ transform: "idx" }), s, ttm, TODAY)?.view).toBe("ttm");
    // no TTM values for EPS: YoY over quarters
    expect(request(metric({ metric: "eps_diluted", transform: "yoy" }), s, ttm, TODAY)?.view).toBe("quarter");
  });

  it("skip incomplete layers", () => {
    expect(request({ ...metric(), kind: "formula", formula: " " }, chart([]), ttm)).toBeNull();
  });

  it("use fiscal quarters only for a single company", () => {
    const one = chart([metric(), metric({ id: 2, metric: "net_income" })], { align: "fiscal" });
    expect(basis(one)).toBe("fiscal");
    const two = chart([metric(), metric({ id: 2, companyId: MSFT })], { align: "fiscal" });
    expect(basis(two)).toBe("calendar");
    const hiddenOther = chart([metric(), metric({ id: 2, companyId: MSFT, hidden: true })], { align: "fiscal" });
    expect(basis(hiddenOther)).toBe("fiscal");
    const formula: Layer = { ...metric(), kind: "formula", formula: "MSFT.revenue / revenue", defaultCompany: NVDA };
    expect(basis(chart([formula], { align: "fiscal" }))).toBe("calendar");
  });

  it("set the range's start a quarter before N years back", () => {
    expect(fromDate(2, TODAY)).toBe("2024-07-21");
    expect(fromDate(5, TODAY)).toBe("2021-07-21");
  });
});

describe("chart state", () => {
  it("names layers for the legend", () => {
    const short = (id: string) => (id === NVDA ? "NVDA" : id);
    expect(layerName(metric({ transform: "ttm", metric: "operating_margin" }), short, metricLabel)).toBe(
      "NVDA operating margin (TTM)",
    );
    expect(layerName({ ...metric(), kind: "formula", formula: "a / b", transform: "yoy" }, short, metricLabel)).toBe("a / b YoY");
  });

  it("builds a company's revenue and margin chart, and templates across its companies", () => {
    const s = companyChart(emptyChart(), NVDA, "Nvidia");
    expect(s.title).toBe("Nvidia: revenue and operating margin");
    expect(s.align).toBe("fiscal");
    expect(s.layers.map((l) => [l.metric, l.transform, l.type, l.axis])).toEqual([
      ["revenue", "q", "bar", "L"],
      ["operating_margin", "ttm", "line", "R"],
    ]);
    const t = applyTemplate(s, TEMPLATES[1]!, [MSFT]);
    expect(t.layers).toHaveLength(1);
    expect(t.layers[0]).toMatchObject({ companyId: NVDA, metric: "revenue", transform: "yoy" });
    expect(applyTemplate(emptyChart(), TEMPLATES[0]!, [NVDA, MSFT]).layers.map((l) => l.companyId)).toEqual([NVDA, MSFT]);
  });

  it("restores only well-formed state", () => {
    const s = restoreChart({
      title: "T",
      range: 9,
      align: "fiscal",
      layers: [
        { kind: "metric", companyId: NVDA, metric: "revenue", transform: "yoy", type: "bar", axis: "R" },
        { kind: "metric", companyId: "../etc", metric: "revenue" },
        { kind: "formula", formula: "net_income / revenue", defaultCompany: "x", transform: "bogus" },
        "junk",
      ],
    });
    expect(s.range).toBe(3);
    expect(s.align).toBe("fiscal");
    expect(s.layers).toEqual([
      { id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "yoy", type: "bar", axis: "R" },
      { id: 2, kind: "formula", formula: "net_income / revenue", transform: "ttm", type: "line", axis: "L" },
    ]);
    expect(s.nextId).toBe(3);
    expect(restoreChart(null)).toEqual(emptyChart());
  });
});

describe("series", () => {
  it("parse exact decimals for drawing and keep them for the CSV", () => {
    const s = toSeries(series([[8100, "CY2025 Q1", "44062000000.00"], [8101, "CY2025 Q2", "46743000000.00"]]),
      metric(), "NVDA revenue", 0);
    expect(s.points.map((p) => p.y)).toEqual([44062000000, 46743000000]);
    expect(s.points[1]!.raw).toBe("46743000000.00");
    expect(s.points[0]!.sources[0]).toMatchObject({ form: "10-Q", accessionNo: "0001045810-26-000000" });
    const csv = chartCsv([s]);
    expect(csv.split("\r\n")[0]).toBe("period,NVDA revenue (USD),NVDA revenue sources");
    expect(csv).toContain("CY2025 Q2,46743000000.00,0001045810-26-000001");
  });

  it("index to the first value, and give YoY a percent unit", () => {
    const data = series([[1, "a", "50"], [2, "b", "75"]]);
    const idx = toSeries(data, metric({ transform: "idx" }), "x", 0);
    expect(idx.unit).toBe("index");
    expect(idx.points.map((p) => p.y)).toEqual([100, 150]);
    expect(idx.points[1]!.raw).toBe("150");
    expect(toSeries(series([[1, "a", "-5"], [2, "b", "5"]]), metric({ transform: "idx" }), "x", 0).points[1]!.y).toBeNull();
    expect(toSeries(data, metric({ transform: "yoy" }), "x", 0).unit).toBe("pct");
  });

  it("share one quarter axis and find the latest source", () => {
    const a = toSeries(series([[2, "CY2 Q3", "1"], [3, "CY2 Q4", "2"]]), metric(), "a", 0);
    const b = toSeries(series([[1, "CY2 Q2", "1"], [3, "CY2 Q4", "2"]]), metric({ id: 2 }), "b", 1);
    expect(xAxis([a, b]).map((t) => t.period)).toEqual(["CY2 Q2", "CY2 Q3", "CY2 Q4"]);
    expect(latestSource([a, b])?.filed).toBe("2026-03-15");
    expect(chartCsv([a, b]).split("\r\n")[1]).toBe("CY2 Q2,,,1,0001045810-26-000000");
  });

  it("keep CSV text cells from becoming spreadsheet formulas", () => {
    expect(csvCell("-0.125")).toBe("-0.125");
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("-1+2")).toBe("'-1+2");
    expect(csvCell('a, "b"')).toBe('"a, ""b"""');
  });
});

describe("layout", () => {
  it("picks round scales that include zero", () => {
    expect(niceScale(12, 87, "USD")).toEqual({ lo: 0, hi: 100, step: 20, unit: "USD" });
    expect(niceScale(-0.05, 0.31, "ratio")).toMatchObject({ lo: -0.1, hi: 0.4, step: 0.1 });
  });

  it("draws bars and lines on their own axes, with a legend and hover targets", () => {
    const rev = toSeries(series([[1, "Q1", "10"], [2, "Q2", "20"], [3, "Q3", "30"]]), metric({ type: "bar" }), "rev", 0);
    const om = toSeries(series([[1, "Q1", "0.1"], [2, "Q2", "0.2"]], { unit: "ratio" }), metric({ id: 2, axis: "R" }), "om", 1);
    const lay = layout([rev, om], true);
    expect(lay.left?.unit).toBe("USD");
    expect(lay.right?.unit).toBe("ratio");
    expect(lay.drawn[0]!.bars).toHaveLength(3);
    expect(lay.drawn[1]!.path).toMatch(/^M[\d.]+,[\d.]+L[\d.]+,[\d.]+$/);
    expect(lay.drawn[1]!.dashed).toBe(true);
    expect(lay.legend.map((l) => l.name)).toEqual(["rev", "om (right axis)"]);
    expect(lay.rightLabels.at(-1)?.label).toBe("20%");
    expect(nearest(lay, lay.x[2]! + 3)).toBe(2);
  });
});

describe("metrics", () => {
  it("come from the screener columns with their TTM availability", () => {
    const m = catalog([
      { key: "eps_diluted_q", metric: "eps_diluted", span: "quarter", unit: "USD/shares" },
      { key: "revenue_q", metric: "revenue", span: "quarter", unit: "USD" },
      { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" },
    ]);
    expect(m.map((x) => [x.label, x.ttm])).toEqual([["EPS (diluted)", false], ["Revenue", true]]);
  });

  it("format values by unit", () => {
    expect(formatValue(46743000000, "USD")).toBe("$46.74B");
    expect(formatValue(0.6234, "ratio")).toBe("62.3%");
    expect(formatValue(1.05, "USD/shares")).toBe("$1.05");
    expect(formatValue(123.456, "index")).toBe("123.5");
  });
});

it("exports a standalone SVG without hover marks, with the light palette embedded", () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 900 400");
  svg.setAttribute("class", "lchart-svg");
  svg.innerHTML = '<g class="c0"><path d="M0,0"/></g><line class="hover-only ch-hover"/>';
  const text = svgText(svg);
  expect(text).toContain('width="900"');
  expect(text).toContain(".c0{color:#3b5bdb}");
  expect(text).not.toContain("hover-only");
  expect(text).not.toContain("var(--");
});

it("turns /financials rows into a fiscal-quarter series with each value's filing", () => {
  expect(periodKey("FY2027 Q2")).toBe(2027 * 4 + 1);
  expect(periodKey("CY2026 Q4")).toBe(2026 * 4 + 3);
  expect(periodKey(null)).toBeNull();
  const rows = [
    fin("revenue", "2026-07-26", "46743000000", "FY2027 Q2"),
    fin("revenue", "2026-04-26", "44062000000", "FY2027 Q1"),
    fin("revenue", "2026-01-25", "1", "", { fiscal_period: null }),
    fin("operating_margin", "2026-07-26", "0.6", "FY2027 Q2"),
  ];
  const s = financialSeries(rows, "revenue", { id: 1, name: "Revenue", color: 9, type: "bar", axis: "L" });
  expect(s.points.map((p) => [p.period, p.y])).toEqual([["FY2027 Q1", 44062000000], ["FY2027 Q2", 46743000000]]);
  expect(s.unit).toBe("USD");
  expect(s.color).toBe(1);
  expect(s.points[0]!.sources[0]).toMatchObject({ form: "10-Q", url: "https://www.sec.gov/x/", periodEnd: "2026-04-26" });
  expect(financialSeries(rows, "operating_margin", { id: 2, name: "m", color: 1, type: "line", axis: "R" }).unit).toBe("ratio");
});
