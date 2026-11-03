import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { clearChartCache } from "../chart/cache";
import { clearShared } from "../chart/fetch";
import type { ChartState } from "../chart/model";
import { fin, NVDA, seg } from "../fixtures";
import type { SegmentRow } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { Revenue } from "./Revenue";

const Q = [
  ["FY2026 Q1", "2025-04-27"], ["FY2026 Q2", "2025-07-27"], ["FY2026 Q3", "2025-10-26"], ["FY2026 Q4", "2026-01-25"],
  ["FY2027 Q1", "2026-04-26"], ["FY2027 Q2", "2026-07-26"],
] as const;
const CN = "nvda:ComputeAndNetworkingSegmentMember";
const GR = "nvda:GraphicsSegmentMember";
const series = (member: string, values: (number | null)[], over: Partial<SegmentRow> = {}) =>
  values.flatMap((v, i) => (v === null ? [] : [seg(member, Q[i]![0], Q[i]![1], String(v), over)]));
// TTM: Compute and Graphics add up to revenue; geography names only the US (partial)
const SEGMENTS = [
  ...series(CN, [100, 110, 120, 130, 150, 170]),
  ...series(GR, [20, 20, 20, 20, 18, 16]),
  ...series("country:US", [50, 50, 50, 50, 60, 70], { axis: "geography", member_label: "United States" }),
];
const REVENUE = [120, 130, 140, 150, 168, 186];
const RECEIVABLES = [12, 13, 14, 15, 17, 25];

let asked: URL[];
function respond(url: string): Response {
  const u = new URL(url, "http://x");
  asked.push(u);
  const p = u.pathname.replace(/^\/api\/query/, "");
  if (p === `/v1/companies/${NVDA}/segments`) return Response.json({ company_id: NVDA, as_of: "2026-11-02T00:00:00Z", rows: SEGMENTS });
  if (p === `/v1/companies/${NVDA}/financials`) {
    const metrics = u.searchParams.getAll("metrics");
    return Response.json({ as_of: "", rows: [
      ...REVENUE.map((v, i) => fin("revenue", Q[i]![1], String(v), Q[i]![0])),
      ...RECEIVABLES.map((v, i) => fin("receivables", Q[i]![1], String(v), Q[i]![0])),
    ].filter((r) => metrics.includes(r.metric)) });
  }
  if (p === "/v1/formulas/evaluate") return Response.json({ formula: "x", unit: "ratio", basis: "fiscal", view: "ttm", alignment: "fiscal", as_of: "", inputs: [], points: [] });
  return Response.json({ detail: "not found" }, { status: 404 });
}

let root: Root;
let container: HTMLDivElement;
const create = vi.fn(async (kind: string, name: string, _state: object, _target: object) => ({
  item_id: "i1", workspace_id: "w1", kind, name, created_at: "", updated_at: "", opened_at: null, state: {}, workspace_name: "Semis",
}));
const saved = { list: { state: "ok", workspaces: [{ workspace_id: "w1", name: "Semis", created_at: "", updated_at: "", items: [] }] }, create } as unknown as Saved;
const onOpenChart = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  asked = [];
  clearChartCache();
  clearShared();
  create.mockClear();
  onOpenChart.mockClear();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function render() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Revenue token="t" companyId={NVDA} name="Nvidia" ticker="NVDA" saved={saved} onOpenChart={onOpenChart} />));
  for (let i = 0; i < 10; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

const buttons = (text: string) => [...container.querySelectorAll("button")].filter((b) => b.textContent === text);

it("draws segments and geography over time, the growth decomposition and revenue quality", async () => {
  await render();
  const axes = [...container.querySelectorAll(".rev-axis")];
  expect(axes.map((a) => a.querySelector("h3")?.textContent)).toEqual(["Revenue by business segment", "Revenue by geography"]);
  // segment layers read /segments once per axis, never the formula API
  const segAsks = asked.filter((u) => u.pathname.endsWith("/segments"));
  expect(new Set(segAsks.map((u) => `${u.searchParams.get("view")}|${u.searchParams.get("axis") ?? ""}`))).toEqual(
    new Set(["ttm|", "ttm|business_segment", "ttm|geography"]),
  );
  // the segment table: members by period, coverage of reported revenue
  const table = axes[0]!.querySelector("table")!;
  expect([...table.querySelectorAll("tbody tr")].map((r) => r.querySelector("td")?.textContent)).toEqual([
    "ComputeAndNetworking", "Graphics", "Sum vs reported revenue",
  ]);
  expect(axes[0]!.querySelector("svg")).not.toBeNull(); // drawn by LayerChart
  // geography names only part of revenue: said, not stacked
  expect(axes[1]!.textContent).toContain("don't add up to reported revenue");
  // decomposition: points of TTM growth
  const dec = container.querySelector("table.decomposition")!;
  expect(dec.textContent).toContain("+46.2 pts"); // Compute, FY2027 Q2: (170 − 110) / 130
  expect(dec.textContent).toContain("−3.1 pts"); // Graphics: (16 − 20) / 130
  expect(dec.querySelector("tr.unattributed")?.textContent).toBe("Not attributed——+0.0 pts+0.0 pts"); // they cover revenue
  expect(dec.querySelector("tr.total")?.textContent).toContain("+43.1%"); // 186 / 130 − 1
  // quality: receivables 25 vs 13 a year earlier (+92%) while revenue grew 43%: flagged
  const quality = container.querySelector("table.quality")!;
  expect(quality.querySelector("tr.flagged td")?.textContent).toBe("FY2027 Q2");
  expect(container.textContent).toContain("Receivables grew more than 10 points faster than revenue in 1 of the last 6 quarters");
});

it("saves a chart to a workspace as a chart item, and opens one in the chart builder", async () => {
  await render();
  await act(async () => buttons("Save to workspace")[0]!.click());
  const form = container.querySelector<HTMLFormElement>("form.save-form")!;
  await act(async () => form.requestSubmit());
  for (let i = 0; i < 5; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  expect(create).toHaveBeenCalledTimes(1);
  const [kind, name, state, target] = create.mock.calls[0]!;
  expect([kind, name, target]).toEqual(["chart", "NVDA: revenue by business segment (TTM)", { workspaceId: "w1" }]);
  const layers = (state as { layers: Record<string, unknown>[] }).layers;
  expect(layers.map((l) => [l.kind, l.members, l.type, l.transform])).toEqual([
    ["segment", [CN], "sbar", "ttm"], ["segment", [GR], "sbar", "ttm"],
  ]);
  expect(container.textContent).toContain("Saved to Semis");
  await act(async () => buttons("Open in Chart builder")[2]!.click()); // the decomposition
  const opened = onOpenChart.mock.calls[0]![0] as ChartState;
  expect(opened.layers.map((l) => l.transform)).toEqual(["contrib", "contrib", "yoy"]);
});
