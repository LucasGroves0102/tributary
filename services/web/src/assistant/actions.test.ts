import { describe, expect, it } from "vitest";

import { companyChart, emptyChart } from "../chart/model";
import { NVDA } from "../fixtures";
import type { UiAction, UiLayer } from "../types";
import { applyChartAction, compareAfter, isChartAction, screenAfter, uiContext } from "./actions";

const AAPL = "0000320193";
const layer = (over: Partial<UiLayer> = {}): UiLayer => ({
  kind: "metric", company_id: NVDA, company_label: "NVDA", metric: "revenue",
  transform: "ttm", type: "line", axis: "L", ...over,
});
const chartAction = (a: UiAction) => {
  if (!isChartAction(a)) throw new Error("not a chart action");
  return a;
};

describe("compare actions", () => {
  const COMPARE = {
    action: "set_compare" as const, label: "Comps: NVDA vs AAPL",
    target: { company_id: NVDA, company_label: "NVDA" },
    peers: [{ company_id: AAPL, company_label: "AAPL" }, { company_id: NVDA, company_label: "NVDA" }],
  };

  it("set the target and peers, keeping the columns unless given", () => {
    const current = { target: AAPL, cols: ["roe_ttm"] };
    expect(compareAfter(current, { ...COMPARE, columns: null })).toEqual({ target: NVDA, peers: [AAPL], cols: ["roe_ttm"] });
    expect(compareAfter(current, { ...COMPARE, columns: ["revenue_ttm", "salary_ttm"] })).toEqual({
      target: NVDA, peers: [AAPL], cols: ["revenue_ttm"],
    });
  });
});

describe("chart actions", () => {
  it("replace the layers and title", () => {
    const s = applyChartAction(companyChart(emptyChart(), NVDA, "NVIDIA CORP"), chartAction({
      action: "set_chart_layers", title: "", label: "Chart: NVDA revenue, AAPL revenue",
      layers: [layer(), layer({ company_id: AAPL, company_label: "AAPL", transform: "yoy" })],
    }));
    expect(s.title).toBe("NVDA revenue, AAPL revenue");
    expect(s.layers.map((l) => [l.companyId, l.metric, l.transform])).toEqual([
      [NVDA, "revenue", "ttm"],
      [AAPL, "revenue", "yoy"],
    ]);
    expect(new Set(s.layers.map((l) => l.id)).size).toBe(2);
  });

  it("add formula layers with their default company", () => {
    const s = applyChartAction(emptyChart(), chartAction({
      action: "add_chart_layer", label: "x",
      layer: layer({ kind: "formula", formula: "net_income / revenue", metric: undefined, axis: "R" }),
    }));
    expect(s.layers[0]).toMatchObject({ kind: "formula", formula: "net_income / revenue", defaultCompany: NVDA, axis: "R" });
  });

  it("remove by company, metric or both", () => {
    const base = companyChart(emptyChart(), NVDA, "NVIDIA CORP");
    const rm = (company_id: string | null, metric: string | null) =>
      applyChartAction(base, chartAction({ action: "remove_chart_layer", label: "x", company_id, metric })).layers;
    expect(rm(NVDA, null)).toEqual([]);
    expect(rm(null, "revenue").map((l) => l.metric)).toEqual(["operating_margin"]);
    expect(rm(AAPL, null)).toHaveLength(2);
  });

  it("set options on the chart and every layer", () => {
    const s = applyChartAction(companyChart(emptyChart(), NVDA, "NVIDIA CORP"), chartAction({
      action: "set_chart_options", label: "x", options: { range: 5, transform: "yoy", type: "bar" },
    }));
    expect(s.range).toBe(5);
    expect(s.align).toBe("fiscal");
    expect(s.layers.every((l) => l.transform === "yoy" && l.type === "bar")).toBe(true);
  });
});

it("merges screen filters unless reset", () => {
  expect(screenAfter({ band: "1b-10b", page: 3 }, { margin: 25 }, false)).toEqual({ band: "1b-10b", margin: 25 });
  expect(screenAfter({ band: "1b-10b" }, { industry: "software" }, true)).toEqual({ industry: "software" });
});

describe("screen context", () => {
  it("describes the chart page with labelled layers", () => {
    const ctx = uiContext({ view: "chart" }, companyChart(emptyChart(), NVDA, "NVIDIA CORP"), (id) =>
      id === NVDA ? "NVIDIA CORP" : null,
    );
    expect(ctx.page).toBe("chart");
    expect(ctx.chart?.align).toBe("fiscal");
    expect(ctx.chart?.layers[0]).toEqual({
      kind: "metric", company_id: NVDA, label: "NVIDIA CORP", metric: "revenue",
      transform: "q", type: "bar", axis: "L", hidden: false,
    });
  });

  it("sends the screener's filters without paging, and the company of a company page", () => {
    expect(uiContext({ view: "companies", screen: { margin: 20, page: 2 } }, emptyChart(), () => null)).toEqual({
      page: "companies", screen: { margin: 20 },
    });
    expect(uiContext({ view: "company", companyId: NVDA }, emptyChart(), () => null)).toEqual({
      page: "company", company_id: NVDA,
    });
  });

  it("sends the company page's tab and the comps", () => {
    expect(uiContext({ view: "company", companyId: NVDA, tab: "ratios" }, emptyChart(), () => null)).toEqual({
      page: "company", company_id: NVDA, tab: "ratios",
    });
    expect(uiContext({ view: "compare", compare: { target: NVDA, peers: [AAPL], cols: ["roe_ttm"] } }, emptyChart(), () => null))
      .toEqual({ page: "compare", compare: { target: NVDA, peers: [AAPL], columns: ["roe_ttm"] } });
    expect(uiContext({ view: "compare" }, emptyChart(), () => null)).toEqual({ page: "compare" });
  });

  it("stays under agent-api's size cap", () => {
    const s = { ...emptyChart(), layers: Array.from({ length: 12 }, (_, i) => ({
      id: i, kind: "formula" as const, formula: "revenue + ".repeat(60) + "revenue",
      transform: "ttm" as const, type: "line" as const, axis: "L" as const,
    })) };
    const ctx = uiContext({ view: "chart" }, s, () => null);
    expect(JSON.stringify(ctx).length).toBeLessThanOrEqual(3800);
    expect(ctx.chart!.layers.length).toBeGreaterThan(0);
    expect(ctx.chart!.layers[0]!.formula!.length).toBe(300);
  });
});
