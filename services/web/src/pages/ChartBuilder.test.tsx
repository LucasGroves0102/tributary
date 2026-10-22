import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { companyChart, emptyChart, type ChartState } from "../chart/model";
import { download } from "../download";
import { entry, NVDA } from "../fixtures";
import { ChartBuilder } from "./ChartBuilder";

vi.mock("../download", () => ({ download: vi.fn() }));

const COLUMNS = {
  columns: [
    { key: "revenue_q", metric: "revenue", span: "quarter", unit: "USD" },
    { key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD" },
    { key: "operating_margin_ttm", metric: "operating_margin", span: "ttm", unit: "ratio" },
    { key: "eps_diluted_q", metric: "eps_diluted", span: "quarter", unit: "USD/shares" },
  ],
  filer_categories: [],
  bands: [],
};

function point(key: number, period: string, value: string) {
  return {
    period, period_key: key, period_end: "2026-07-26", value, source_accessions: [],
    inputs: [{
      input: 0, period_start: null, period_end: "2026-07-26", value, value_source: "reported",
      citation: { accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26", source_accessions: null, filing_url: null },
      freshness: { filed: "2026-08-26", ingested_at: "" },
    }],
  };
}

const bodies: { path: string; body: Record<string, string> }[] = [];

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  if (path.endsWith("/v1/screener/columns")) return Response.json(COLUMNS);
  if (path.endsWith("/v1/screener")) {
    return Response.json({ total: 1, rows: [{ ...entry(), filer_categories: null, state_of_incorporation: null }] });
  }
  const body = JSON.parse(String(init?.body ?? "{}"));
  bodies.push({ path, body });
  if (path.endsWith("/v1/formulas/validate")) {
    return body.formula.includes("TSLA")
      ? Response.json({ valid: false, errors: [{ code: "company", message: "TSLA isn't one of your companies", pos: 0, length: 4, hint: "Only companies granted to you can be charted" }] })
      : Response.json({ valid: true, unit: "ratio", alignment: "fiscal", ast: {}, inputs: [] });
  }
  if (path.endsWith("/v1/formulas/evaluate")) {
    if (body.formula.includes("TSLA")) {
      return Response.json({ detail: { errors: [{ code: "company", message: "TSLA isn't one of your companies", pos: 0, length: 4, hint: null }] } }, { status: 403 });
    }
    const margin = body.formula === "operating_margin";
    return Response.json({
      formula: body.formula, unit: margin ? "ratio" : "USD", basis: body.basis, view: body.view,
      alignment: body.basis, as_of: "", inputs: [],
      points: margin
        ? [point(8100, "FY2027 Q1", "0.61"), point(8101, "FY2027 Q2", "0.62")]
        : [point(8100, "FY2027 Q1", "44062000000"), point(8101, "FY2027 Q2", "46743000000")],
    });
  }
  return Response.json({ detail: "not found" }, { status: 404 });
}

let root: Root;
let container: HTMLDivElement;
let latest: ChartState;

function Harness({ initial }: { initial: ChartState }) {
  const [chart, set] = useState(initial);
  latest = chart;
  return <ChartBuilder token="t" chart={chart} setChart={(f) => set(f)} />;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
  bodies.length = 0;
  vi.mocked(download).mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

async function render(initial: ChartState) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Harness initial={initial} />));
  for (let i = 0; i < 3; i++) await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const button = (text: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === text)!;

it("evaluates each layer through the formula API and draws them", async () => {
  await render(companyChart(emptyChart(), NVDA, "NVIDIA CORP"));
  const evals = bodies.filter((b) => b.path.endsWith("/evaluate")).map((b) => b.body);
  expect(evals).toHaveLength(2);
  expect(evals[0]).toMatchObject({ formula: "revenue", default_company: NVDA, basis: "fiscal", view: "quarter" });
  expect(evals[1]).toMatchObject({ formula: "operating_margin", view: "ttm" });
  expect($(".ch-title")?.textContent).toBe("NVIDIA CORP: revenue and operating margin");
  expect([...container.querySelectorAll(".ch-legend text")].map((t) => t.textContent)).toEqual([
    "NVDA revenue",
    "NVDA operating margin (TTM) (right axis)",
  ]);
  expect(container.querySelectorAll(".lchart-svg rect[fill='currentColor']")).toHaveLength(2);
  expect($(".ch-foot")?.textContent).toContain("Fiscal quarters");
  expect(container.textContent).toContain("Latest filing used: 10-Q filed 2026-08-26 (0001045810-26-000075)");
});

it("shows each layer's value, period and source filing on hover", async () => {
  await render(companyChart(emptyChart(), NVDA, "NVIDIA CORP"));
  const svg = $(".lchart-svg")!;
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 900, height: 400 }) as DOMRect;
  await act(async () => svg.dispatchEvent(new MouseEvent("mousemove", { clientX: 880, bubbles: true })));
  const tip = $(".tip")!;
  expect(tip.textContent).toContain("FY2027 Q2");
  expect(tip.textContent).toContain("$46.74B");
  expect(tip.textContent).toContain("62.0%");
  expect(tip.textContent).toContain("10-Q filed Aug 26, 2026 · 0001045810-26-000075");
});

it("switches transform and hides layers", async () => {
  await render(companyChart(emptyChart(), NVDA, "NVIDIA CORP"));
  const transform = container.querySelector<HTMLSelectElement>('select[aria-label="Transform"]')!;
  await act(async () => {
    transform.value = "yoy";
    transform.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => {});
  expect(latest.layers[0]!.transform).toBe("yoy");
  expect(bodies.at(-1)?.body.formula).toBe("(revenue) / (revenue)[-4q] - 1");
  await act(async () => button("Hide").click());
  expect(container.querySelectorAll(".ch-legend text")).toHaveLength(1);
});

it("calendar alignment when the fiscal toggle can't apply", async () => {
  const s = companyChart(emptyChart(), NVDA, "NVIDIA CORP");
  s.layers.push({ id: 9, kind: "metric", companyId: "0000789019", metric: "revenue", transform: "q", type: "line", axis: "L" });
  await render(s);
  expect(container.textContent).toContain("uses calendar quarters");
  expect(bodies.filter((b) => b.path.endsWith("/evaluate")).every((b) => b.body.basis === "calendar")).toBe(true);
});

it("checks formulas inline and reports a company the user can't read", async () => {
  vi.useFakeTimers();
  await render({ ...emptyChart(), layers: [{ id: 1, kind: "formula", formula: "", transform: "ttm", type: "line", axis: "R" }], nextId: 2 });
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Formula"]')!;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, "TSLA.revenue / revenue");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => vi.advanceTimersByTime(400));
  await act(async () => {});
  expect(bodies.some((b) => b.path.endsWith("/validate"))).toBe(true);
  expect($(".layer .err")?.textContent).toContain("TSLA isn't one of your companies");
  expect($(".layer .err")?.textContent).toContain("“TSLA”");
  await act(async () => input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  await act(async () => {});
  await act(async () => {});
  expect(latest.layers[0]!.formula).toBe("TSLA.revenue / revenue");
  expect(container.querySelector(".lchart-svg")).toBeNull();
});

it("exports the plotted data as CSV with exact values and sources", async () => {
  await render(companyChart(emptyChart(), NVDA, "NVIDIA CORP"));
  await act(async () => button("Export CSV").click());
  const [name, blob] = vi.mocked(download).mock.calls[0]!;
  expect(name).toBe("nvidia-corp-revenue-and-operating-margin.csv");
  const text = await new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(blob as Blob);
  });
  expect(text).toContain("FY2027 Q2,46743000000,0001045810-26-000075,0.62,0001045810-26-000075");
});
