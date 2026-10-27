import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ScreenFilters } from "../companies/screen";
import { download } from "../download";
import { entry, NVDA, TESLA, TSLA } from "../fixtures";
import { ScopeContext, type Scope } from "../scope";
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

async function render(screen: ScreenFilters = {}, scope: Scope | null = null) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <ScopeContext.Provider value={scope}>
        <Companies token="t" screen={screen} watched={new Set([TSLA])} onWatch={onWatch} onChart={onChart} />
      </ScopeContext.Provider>,
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

it("moves through rows with the keyboard and opens one with Enter", async () => {
  await render();
  const rows = [...container.querySelectorAll<HTMLTableRowElement>("table.screen tbody tr")];
  expect(rows.map((r) => r.tabIndex)).toEqual([0, -1]); // one tab stop for the table
  rows[0]!.focus();
  const key = (el: Element, k: string) => act(async () => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true })));
  await key(rows[0]!, "ArrowDown");
  expect(document.activeElement).toBe(rows[1]);
  expect(rows.map((r) => r.tabIndex)).toEqual([-1, 0]);
  await key(rows[1]!, "Home");
  expect(document.activeElement).toBe(rows[0]);
  await key(rows[0]!, "End");
  expect(document.activeElement).toBe(rows[1]);
  await key(rows[1]!, "Enter");
  await act(async () => new Promise((r) => setTimeout(r, 10)));
  expect(window.location.hash).toBe(`#/company/${TSLA}`);
  // keys typed on a button inside the row are left alone
  const watch = rows[1]!.querySelector("button")!;
  await key(watch, "ArrowUp");
  expect(document.activeElement).toBe(rows[1]);
});

it("shows a skeleton while the screen runs and a retry when it fails", async () => {
  let fail = true;
  vi.mocked(fetch).mockImplementation(async () =>
    fail ? Response.json({ detail: "database is restarting" }, { status: 503 }) : Response.json(SCREEN));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root.render(<Companies token="t" screen={{}} watched={null} onWatch={onWatch} />));
  expect(container.querySelector(".skel[role=status]")?.textContent).toContain("Running the screen");
  await act(async () => {});
  await act(async () => {});
  const panel = container.querySelector(".errpanel")!;
  expect(panel.textContent).toContain("Couldn't load this screen");
  expect(panel.textContent).toContain("database is restarting");
  fail = false;
  await act(async () => panel.querySelector("button")!.click());
  await act(async () => {});
  expect(container.querySelectorAll("table.screen tbody tr")).toHaveLength(2);
});

it("a demo sandbox screens only its companies, with no 'only mine' box and a demo count", async () => {
  const scope = { ids: new Set([NVDA]) };
  vi.mocked(fetch).mockImplementation(async () => Response.json({ ...SCREEN, total: 1, rows: [row(entry())] }));
  await render({}, scope);
  expect(calls()[0]!.searchParams.get("access")).toBe("financials");
  expect(container.textContent).not.toContain("Only companies I can read");
  expect($(".page > .row .muted")?.textContent).toBe("1 company in your demo");
  expect($(".card-h .sub")?.textContent).toBe("no filters");
  expect(container.textContent).not.toContain("metadata only");
  act(() => root.unmount());
  await render({ margin: 20, mine: true }, scope);
  expect(calls().at(-1)!.searchParams.get("access")).toBe("financials");
  expect($(".page > .row .muted")?.textContent).toBe("1 of the 1 companies in your demo");
  expect($(".card-h .sub")?.textContent).toBe("operating margin ≥ 20%");
});

it("a named user keeps the full index and the 'only mine' box", async () => {
  await render();
  expect(calls()[0]!.searchParams.get("access")).toBeNull();
  expect(container.textContent).toContain("Only companies I can read");
  expect($(".page > .row .muted")?.textContent).toBe("2 companies with financials");
});
