import { clearChartCache } from "../chart/cache";
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
    period, period_key: key, period_end: period.endsWith("Q1") ? "2026-04-26" : "2026-07-26", value, source_accessions: [],
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
  if (path.endsWith("/filing-index")) {
    return Response.json({ filings: [
      { accession_no: "0001045810-26-000060", company_id: NVDA, name: "Nvidia", form: "10-Q", filed: "2026-05-28", accepted_at: null,
        period_of_report: "2026-04-26", filing_url: null, document_url: null, sections: null, sections_available: false },
    ] });
  }
  if (/\/v1\/companies\/\d+\/filings$/.test(path)) {
    return Response.json({ filings: [{ accession_no: "0001045810-26-000061", form: "8-K", filed: "2026-05-28", period_of_report: null,
      document_url: null, sections: [{ section_key: "2.02", title: "Results", chars: 10 }] }] });
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
      alignment: body.basis, as_of: "2026-10-21T12:00:00Z",
      inputs: [{ input: 0, ref: body.formula, ticker: null, company_id: body.default_company ?? null, metric: "revenue", span: "quarter", lag_quarters: 0, unit: "USD" }],
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
  clearChartCache();
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
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  const evals = bodies.filter((b) => b.path.endsWith("/evaluate")).map((b) => b.body);
  expect(evals).toHaveLength(2);
  expect(evals[0]).toMatchObject({ formula: "revenue", default_company: NVDA, basis: "fiscal", view: "quarter" });
  expect(evals[1]).toMatchObject({ formula: "operating_margin", view: "ttm" });
  expect($(".ch-title")?.textContent).toBe("Nvidia: revenue and operating margin");
  expect([...container.querySelectorAll(".ch-legend text")].map((t) => t.textContent)).toEqual([
    "NVDA revenue",
    "NVDA operating margin (TTM) (right axis)",
  ]);
  expect(container.querySelectorAll(".lchart-svg rect[fill='currentColor']")).toHaveLength(2);
  expect($(".ch-foot")?.textContent).toContain("Fiscal quarters");
  expect(container.textContent).toContain("Latest filing used: 10-Q filed 2026-08-26 (0001045810-26-000075)");
});

it("shows each layer's value, period and source filing on hover", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
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
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
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
  const s = companyChart(emptyChart(), NVDA, "Nvidia");
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
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  await act(async () => button("Export CSV").click());
  const [name, blob] = vi.mocked(download).mock.calls[0]!;
  expect(name).toBe("nvidia-revenue-and-operating-margin.csv");
  const text = await new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(blob as Blob);
  });
  expect(text).toContain("FY2027 Q2,46743000000,0001045810-26-000075,0.62,0001045810-26-000075");
});

const select = async (label: string, value: string, i = 0) => {
  const el = container.querySelectorAll<HTMLSelectElement>(`select[aria-label="${label}"]`)[i]!;
  await act(async () => {
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => {});
};

it("renames, recolours and reorders layers from the keyboard-reachable controls", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  const name = container.querySelector<HTMLInputElement>('input[aria-label="Layer name (legend)"]')!;
  expect(name.placeholder).toBe("NVDA revenue");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Quarterly sales");
    name.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => name.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  expect(latest.layers[0]!.name).toBe("Quarterly sales");
  expect([...container.querySelectorAll(".ch-legend text")][0]!.textContent).toBe("Quarterly sales");

  await select("Colour", "5");
  expect(latest.layers[0]!.color).toBe(5);
  expect(container.querySelector(".ch-legend g")!.getAttribute("class")).toBe("c5");

  const down = container.querySelector<HTMLButtonElement>('button[aria-label="Move Quarterly sales down"]')!;
  expect(container.querySelector<HTMLButtonElement>('button[aria-label="Move Quarterly sales up"]')!.disabled).toBe(true);
  await act(async () => down.click());
  expect(latest.layers.map((l) => l.metric)).toEqual(["operating_margin", "revenue"]);
  expect(latest.layers.map((l) => l.color)).toEqual([1, 5]); // colours stay with their layers
});

it("offers stacked types, QoQ and per share, and every range", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  const types = [...container.querySelector<HTMLSelectElement>('select[aria-label="Type"]')!.options].map((o) => o.value);
  expect(types).toEqual(["line", "bar", "area", "sbar", "sarea"]);
  await select("Transform", "qoq");
  expect(bodies.at(-1)?.body).toMatchObject({ formula: "(revenue) / (revenue)[-1q] - 1", view: "quarter" });
  // per share needs a diluted share count in the catalog, which this one lacks
  expect(container.textContent).toContain("Per share isn't available");
  expect([...container.querySelector<HTMLSelectElement>('select[aria-label="Transform"]')!.options].map((o) => o.value)).not.toContain("ps");
  expect([...container.querySelectorAll('[aria-label="Range"] button')].map((b) => b.textContent)).toEqual(["1y", "3y", "5y", "10y", "Max", "Custom"]);
  await act(async () => button("Max").click());
  await act(async () => {});
  expect(latest.range).toBe("max");
  expect(bodies.at(-1)?.body).not.toHaveProperty("from");
});

it("opens the clicked point's filing in the reader, and shows the values as a table", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  const svg = $(".lchart-svg")!;
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 900, height: 400 }) as DOMRect;
  await act(async () => svg.dispatchEvent(new MouseEvent("click", { clientX: 880, clientY: 300, bubbles: true })));
  expect(window.location.hash).toBe(`#/filing/${NVDA}/0001045810-26-000075`);

  // keyboard: focus, move, Enter
  window.location.hash = "";
  await act(async () => svg.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true })));
  expect($(".tip")?.textContent).toContain("FY2027 Q1");
  await act(async () => svg.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(window.location.hash).toBe(`#/filing/${NVDA}/0001045810-26-000075`);

  await act(async () => button("View as table").click());
  const table = container.querySelector("table")!;
  expect(table.querySelector("caption")?.textContent).toContain("values by quarter");
  expect([...table.querySelectorAll("thead th")].map((t) => t.textContent)).toEqual([
    "Period", "NVDA revenue USD", "NVDA operating margin (TTM) %",
  ]);
  expect(table.querySelector("tbody tr")!.textContent).toContain("FY2027 Q2$46.74B62.0%");
  expect(table.querySelector("tbody td")!.getAttribute("title")).toContain("10-Q filed Aug 26, 2026");
  expect(container.querySelector(".lchart-svg")!.closest("[hidden]")).not.toBeNull();
});

it("draws 10-K/10-Q and 8-K results markers when turned on", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  expect(container.querySelector(".ch-mk")).toBeNull();
  const box = [...container.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find((c) => c.parentElement?.textContent?.includes("Filing markers"))!;
  await act(async () => box.click());
  for (let i = 0; i < 3; i++) await act(async () => {});
  expect(latest.markers).toBe(true);
  const marks = [...container.querySelectorAll(".ch-mk")];
  expect(marks.map((m) => m.querySelector("text")!.textContent)).toEqual(["Q", "8"]);
  expect(marks[0]!.querySelector("title")!.textContent).toContain("NVDA 10-Q filed May 28, 2026");
  await act(async () => (marks[1] as unknown as SVGElement).dispatchEvent(new MouseEvent("click", { bubbles: true })));
  expect(window.location.hash).toBe(`#/filing/${NVDA}/0001045810-26-000061`);
});

const type = async (input: HTMLInputElement, text: string) => {
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, text);
    if (input.type === "text") input.setSelectionRange(text.length, text.length);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};
const key = (el: Element, k: string) => act(async () => {
  el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
});

it("autocompletes metrics, tickers and operators in formulas", async () => {
  await render({ ...emptyChart(), layers: [{ id: 1, kind: "formula", formula: "", defaultCompany: NVDA, transform: "ttm", type: "line", axis: "R" }], nextId: 2 });
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Formula"]')!;
  const options = () => [...container.querySelectorAll('[role="option"]')].map((o) => o.querySelector("b")!.textContent);
  await type(input, "oper");
  expect(input.getAttribute("aria-expanded")).toBe("true");
  expect(options()).toEqual(["operating_margin"]);
  await key(input, "Tab");
  expect(input.value).toBe("operating_margin");
  await type(input, "operating_margin ");
  expect(options()).toContain("÷");
  await type(input, "operating_margin / NV");
  expect(options()).toEqual(["NVDA."]);
  await key(input, "ArrowDown");
  expect(input.getAttribute("aria-activedescendant")).toMatch(/-0$/);
  await key(input, "Enter"); // moved to it: Enter takes it
  expect(input.value).toBe("operating_margin / NVDA.");
  expect(options()).toEqual(["eps_diluted", "operating_margin", "revenue"]);
  await key(input, "Escape");
  expect(input.getAttribute("aria-expanded")).toBe("false");
  // Enter without moving applies the formula, as before
  await type(input, "operating_margin / NVDA.revenue");
  await key(input, "Enter");
  await act(async () => {});
  expect(latest.layers[0]!.formula).toBe("operating_margin / NVDA.revenue");
});

it("custom range and annual frequency change what is asked", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  await act(async () => button("Custom").click());
  const first = container.querySelector<HTMLInputElement>('input[aria-label="First period end"]')!;
  const last = container.querySelector<HTMLInputElement>('input[aria-label="Last period end"]')!;
  await type(first, "2020-01-01");
  await type(last, "2025-12-31");
  await act(async () => {});
  expect(latest).toMatchObject({ range: "custom", start: "2020-01-01", end: "2025-12-31" });
  const revenue = () => bodies.filter((b) => b.path.endsWith("/evaluate") && b.body.formula === "revenue").at(-1)!.body;
  expect(revenue()).toMatchObject({ from: "2020-01-01", to: "2025-12-31", view: "quarter" });
  await act(async () => button("Annual").click());
  await act(async () => {});
  expect(latest.frequency).toBe("annual");
  // quarterly revenue becomes the year's total: the TTM at each Q4
  expect(revenue()).toMatchObject({ view: "ttm", from: "2020-01-01" });
  expect([...container.querySelector<HTMLSelectElement>('select[aria-label="Transform"]')!.options].find((o) => o.value === "qoq")!.disabled).toBe(true);
  // the mock has no fourth quarters, so there is no full year to draw
  expect(container.textContent).toContain("No full years for these layers in this range");
});

it("deck output: light chart with subtitle, units and the source footnote", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  expect(container.querySelector(".lchart.deck")).toBeNull();
  expect([...container.querySelectorAll(".ch-unit")].map((t) => t.textContent)).toEqual(["USD", "%"]);
  const box = [...container.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find((c) => c.parentElement?.textContent?.includes("Deck output"))!;
  await act(async () => box.click());
  expect(latest.deck).toBe(true);
  expect(container.querySelector(".lchart.deck")).not.toBeNull();
  expect($(".ch-sub")?.textContent).toBe("Fiscal quarters, FY2027 Q1–FY2027 Q2 · USD (left), % (right)");
  const foot = [...container.querySelectorAll(".ch-foot")].map((t) => t.textContent);
  expect(foot[0]).toBe("Source: SEC filings via Tributary; 10-Q for NVDA; latest NVDA 10-Q filed Aug 26, 2026; as of Oct 21, 2026.");
  expect(foot[1]).toContain("Fiscal quarters; TTM = trailing four quarters");
  await type(container.querySelector<HTMLInputElement>('input[aria-label="Subtitle"]')!, "USD billions, fiscal quarters");
  expect($(".ch-sub")?.textContent).toBe("USD billions, fiscal quarters");
  await act(async () => button("Export SVG").click());
  const svg = await new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(vi.mocked(download).mock.calls.at(-1)![1] as Blob);
  });
  expect(svg).toContain("USD billions, fiscal quarters");
  expect(svg).toContain("Source: SEC filings via Tributary");
  expect(svg).toContain(".ch-bg{fill:#fff}");
});

it("keeps chart data in memory: coming back asks the API nothing", async () => {
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  const evals = () => bodies.filter((b) => b.path.endsWith("/evaluate")).length;
  expect(evals()).toBe(2);
  act(() => root.unmount());
  await render(companyChart(emptyChart(), NVDA, "Nvidia"));
  expect(evals()).toBe(2);
  expect(container.querySelectorAll(".ch-legend text")).toHaveLength(2);
});
