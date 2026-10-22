import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ScreenFilters } from "../companies/screen";
import { download } from "../download";
import { entry, NVDA, TESLA, TSLA } from "../fixtures";
import { Companies } from "./Companies";

vi.mock("../download", () => ({ download: vi.fn() }));

const row = (e: typeof TESLA) => ({ ...e, filer_categories: null, state_of_incorporation: null });
const SCREEN = {
  as_of: "2026-10-21T12:00:00Z",
  total: 2,
  limit: 50,
  offset: 0,
  sort: "-revenue_ttm",
  columns: [],
  rows: [row(entry()), row(TESLA)],
};

let root: Root;
let container: HTMLDivElement;
const onWatch = vi.fn(async () => {});
const onChart = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      new URL(url, "http://x").searchParams.get("format") === "csv"
        ? new Response("company_id,name\n", { headers: { "Content-Type": "text/csv" } })
        : Response.json(SCREEN),
    ),
  );
  onWatch.mockClear();
  onChart.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

async function render(screen: ScreenFilters = {}) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <Companies token="t" screen={screen} watched={new Set([TSLA])} onWatch={onWatch} onChart={onChart} />,
    ),
  );
  await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const calls = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));

it("runs the screen from the URL's filters and shows values with sources", async () => {
  await render({ margin: 20, band: "1b-10b" });
  const q = calls()[0]!;
  expect(q.pathname).toBe("/api/query/v1/screener");
  expect(q.searchParams.getAll("where")).toEqual(["operating_margin_ttm:gte:0.2"]);
  expect(q.searchParams.get("revenue_band")).toBe("1b-10b");
  const nvda = container.querySelectorAll("table.screen tbody tr")[0]!;
  expect(nvda.textContent).toContain("Nvidia");
  const revenue = [...nvda.querySelectorAll("td.n")].find((td) => td.textContent === "$130.50B");
  expect(revenue?.getAttribute("title")).toContain("10-Q filed Aug 26, 2026");
  expect($(".card-h .sub")?.textContent).toBe("revenue $1B to $10B, operating margin ≥ 20%");
});

it("shows ungranted companies honestly: metadata only, with the reason", async () => {
  await render();
  const tsla = container.querySelectorAll("table.screen tbody tr")[1]!;
  expect(tsla.className).toContain("masked");
  expect(tsla.textContent).toContain("Tesla");
  expect(tsla.textContent).toContain("metadata only");
  expect(tsla.textContent).not.toMatch(/\$\d/);
  expect(tsla.querySelector("td.n")?.getAttribute("title")).toContain("doesn't cover");
  expect($(".foot")?.textContent).toContain("1 company on this page shows metadata only");
  // no chart for a company the user can't read
  expect(tsla.querySelector('button[aria-label^="Chart"]')).toBeNull();
});

it("sorts by a column, flipping direction on a second click", async () => {
  await render();
  const th = [...container.querySelectorAll<HTMLElement>("th.sort")].find((t) =>
    t.textContent?.startsWith("Op mgn"),
  )!;
  await act(async () => th.click());
  expect(window.location.hash).toBe("#/companies?sort=-operating_margin_ttm");
  act(() => root.unmount());
  await render({ sort: "-operating_margin_ttm" });
  const again = [...container.querySelectorAll<HTMLElement>("th.sort")].find((t) =>
    t.textContent?.startsWith("Op mgn"),
  )!;
  expect(again.getAttribute("aria-sort")).toBe("descending");
  await act(async () => again.click());
  expect(window.location.hash).toBe("#/companies?sort=operating_margin_ttm");
});

it("applies a filter from the rail to the URL", async () => {
  await render({ industry: "semi" });
  const select = $("#f-fye") as HTMLSelectElement;
  await act(async () => {
    select.value = "1";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(window.location.hash).toBe("#/companies?industry=semi&fye=1");
});

it("watches, unwatches and charts from a row", async () => {
  await render();
  await act(async () => $(`button[aria-label="Add Nvidia to watchlist"]`)!.click());
  expect(onWatch).toHaveBeenCalledWith(NVDA, true);
  await act(async () => $(`button[aria-label="Remove Tesla from watchlist"]`)!.click());
  expect(onWatch).toHaveBeenCalledWith(TSLA, false);
  await act(async () => $(`button[aria-label="Chart Nvidia"]`)!.click());
  expect(onChart).toHaveBeenCalledWith(NVDA, "Nvidia");
});

it("exports the whole screen as the API's CSV", async () => {
  await render({ mine: true });
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Export CSV")!;
  await act(async () => button.click());
  await act(async () => {});
  const csv = calls().find((u) => u.searchParams.get("format") === "csv")!;
  expect(csv.searchParams.get("limit")).toBe("5000");
  expect(csv.searchParams.get("offset")).toBe("0");
  expect(csv.searchParams.get("access")).toBe("financials");
  expect(vi.mocked(download).mock.calls[0]?.[0]).toBe("screener.csv");
});
