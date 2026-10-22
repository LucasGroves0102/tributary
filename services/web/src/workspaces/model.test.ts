import { describe, expect, it } from "vitest";

import { addLayer, emptyChart, type ChartState } from "../chart/model";
import {
  canSave,
  currentFingerprint,
  findWorkspace,
  fingerprint,
  itemRoute,
  nameProblem,
  restoreCompare,
  restoreOpen,
  restoreScreen,
  stateOf,
  suggestName,
} from "./model";

const NVDA = "0001045810";
const AAPL = "0000320193";

function chart(): ChartState {
  let s: ChartState = { ...emptyChart(), title: "NVDA revenue" };
  s = addLayer(s, { kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L" });
  s = addLayer(s, { kind: "formula", formula: "NVDA.revenue / AAPL.revenue", transform: "q", type: "bar", axis: "R", hidden: true });
  return s;
}

describe("saved states", () => {
  it("saves a chart without ids or incomplete layers, in the API's shape", () => {
    const s = addLayer(chart(), { kind: "metric", transform: "ttm", type: "line", axis: "L" });
    expect(stateOf({ kind: "chart", chart: s })).toEqual({
      title: "NVDA revenue",
      range: 3,
      align: "calendar",
      layers: [
        { kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L" },
        { kind: "formula", formula: "NVDA.revenue / AAPL.revenue", transform: "q", type: "bar", axis: "R", hidden: true },
      ],
    });
  });

  it("saves comps and screens as their URL cleaners keep them (no page)", () => {
    expect(stateOf({ kind: "comps", compare: { target: NVDA, peers: [AAPL, NVDA, "x"] } })).toEqual({ target: NVDA, peers: [AAPL] });
    expect(stateOf({ kind: "screen", screen: { margin: 20, page: 3, sort: "-revenue_ttm" } })).toEqual({ margin: 20 });
  });

  it("the API's filled-in defaults don't count as unsaved changes", () => {
    const current = { kind: "chart" as const, chart: chart() };
    const fromApi = JSON.parse(JSON.stringify(stateOf(current)));
    fromApi.layers[0].hidden = false; // the API stores the default
    fromApi.layers = fromApi.layers.map((l: object) => Object.fromEntries(Object.entries(l).reverse()));
    expect(fingerprint("chart", fromApi)).toBe(currentFingerprint(current));
    expect(fingerprint("chart", { ...fromApi, range: 5 })).not.toBe(currentFingerprint(current));
    expect(fingerprint("comps", { peers: [AAPL], target: NVDA })).toBe(
      currentFingerprint({ kind: "comps", compare: { target: NVDA, peers: [AAPL] } }),
    );
  });

  it("restores untrusted states through the same cleaners as a URL", () => {
    expect(restoreCompare({ target: NVDA, peers: [AAPL, "../x", 7], cols: ["roe_ttm", "price"], extra: 1 })).toEqual({
      target: NVDA,
      peers: [AAPL],
      cols: ["roe_ttm"],
    });
    expect(restoreCompare("nope")).toEqual({});
    expect(restoreScreen({ margin: 20, band: "huge", sort: "drop table", mine: true, page: 4, q: { x: 1 } })).toEqual({
      margin: 20,
      mine: true,
    });
    expect(itemRoute("comps", { target: NVDA })).toEqual({ view: "compare", compare: { target: NVDA } });
    expect(itemRoute("screen", {})).toEqual({ view: "companies" });
    expect(itemRoute("chart", { layers: "x" })).toEqual({ view: "chart" });
  });

  it("needs something to save", () => {
    expect(canSave({ kind: "chart", chart: emptyChart() })).toBe(false);
    expect(canSave({ kind: "chart", chart: chart() })).toBe(true);
    expect(canSave({ kind: "comps", compare: { peers: [AAPL] } })).toBe(false);
    expect(canSave({ kind: "comps", compare: { target: NVDA } })).toBe(true);
    expect(canSave({ kind: "screen", screen: {} })).toBe(true);
  });
});

describe("names and open items", () => {
  it("checks names like the API", () => {
    expect(nameProblem("  ", 10)).toBe("Give it a name.");
    expect(nameProblem("x".repeat(11), 10)).toMatch(/At most 10/);
    expect(nameProblem("a\u0007b", 10)).toMatch(/control/);
    expect(nameProblem(" ok ", 10)).toBeNull();
  });

  it("finds a workspace by name ignoring case and spaces", () => {
    const list = [{ name: "Beverages" }, { name: "Semis" }];
    expect(findWorkspace(list, "  beverages ")).toBe(list[0]);
    expect(findWorkspace(list, "Bev")).toBeUndefined();
  });

  it("suggests names from the screen", () => {
    const label = (id: string) => (id === NVDA ? "NVDA" : "AAPL");
    expect(suggestName({ kind: "chart", chart: chart() }, label)).toBe("NVDA revenue");
    expect(suggestName({ kind: "comps", compare: { target: NVDA, peers: [AAPL] } }, label)).toBe("NVDA comps");
    expect(suggestName({ kind: "comps", compare: { target: NVDA } }, label)).toBe("NVDA comps");
  });

  it("keeps only well-formed open items from storage", () => {
    const ok = { itemId: "i", workspaceId: "w", workspaceName: "W", name: "N", saved: "{}" };
    expect(restoreOpen({ chart: ok, comps: { ...ok, itemId: 3 }, notes: ok })).toEqual({ chart: ok });
    expect(restoreOpen("x")).toEqual({});
  });
});
