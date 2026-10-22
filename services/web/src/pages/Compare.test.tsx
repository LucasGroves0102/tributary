import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { CompareState } from "../compare/state";
import { download } from "../download";
import { entry, NVDA, TESLA, TSLA } from "../fixtures";
import { Compare } from "./Compare";

vi.mock("../download", () => ({ download: vi.fn() }));

const AAPL = "0000320193";
const KO = "0000021344";
const row = (e: typeof TESLA) => ({ ...e, filer_categories: null, state_of_incorporation: null });
const APPLE = row(entry({
  company_id: AAPL, name: "Apple", tickers: ["AAPL"], fiscal_year_end: "0927",
  values: { revenue_ttm: "400000000000", operating_margin_ttm: "0.3", revenue_growth_ttm: "-0.02" },
}));
const PEERS = {
  company_id: NVDA, as_of: "", columns: [], method: "Same 4-digit SIC first",
  company: { ...row(entry()), revenue_band: ">=$100B" },
  peers: [
    { ...APPLE, match: { sic_level: 4, same_filer_category: true, revenue_band: ">=$100B", same_revenue_band: true } },
    {
      ...row(entry({ company_id: KO, name: "COCA COLA CO", tickers: ["KO"], access: [] })),
      match: { sic_level: 2, same_filer_category: true, revenue_band: null, same_revenue_band: false },
    },
  ],
};

let root: Root;
let container: HTMLDivElement;
const onChart = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const u = new URL(url, "http://x");
      if (u.pathname.endsWith("/peers")) return Response.json(PEERS);
      if (u.pathname.endsWith("/v1/companies/search")) {
        return Response.json({ results: [{ company_id: KO, name: "COCA COLA CO", matched_on: "ticker", matched_alias: "KO", is_current: true, access: [] }] });
      }
      const ids = u.searchParams.getAll("company_id");
      const rows = [row(entry()), row(TESLA), APPLE].filter((r) => ids.includes(r.company_id));
      return Response.json({ as_of: "", total: rows.length, limit: 21, offset: 0, sort: "name", columns: [], rows });
    }),
  );
  onChart.mockClear();
  vi.mocked(download).mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
  window.location.hash = "";
});

async function render(compare: CompareState) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Compare token="t" compare={compare} onChart={onChart} />));
  await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const calls = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));
const click = (el: Element | null | undefined) => act(async () => (el as HTMLElement).click());
const rows = () => [...container.querySelectorAll("table.comps tbody tr")];

const COMPS = { target: NVDA, peers: [TSLA, AAPL], cols: ["revenue_ttm", "operating_margin_ttm", "revenue_growth_ttm"] };

it("asks the screener for the comps by id and shows each value with its period and source", async () => {
  await render(COMPS);
  const q = calls().find((u) => u.pathname === "/api/query/v1/screener")!;
  expect(q.searchParams.getAll("company_id")).toEqual([NVDA, TSLA, AAPL]);
  expect(q.searchParams.getAll("columns")).toEqual([...COMPS.cols, "revenue_q"]);
  const [nvda, tsla, aapl] = rows();
  expect(nvda!.className).toBe("sel");
  expect(nvda!.textContent).toContain("target");
  expect(nvda!.querySelectorAll("td")[1]!.textContent).toBe("CY26 Q2 (FY27 Q2)");
  const revenue = [...nvda!.querySelectorAll("td.n")].find((td) => td.textContent === "$130.50B")!;
  expect(revenue.getAttribute("title")).toContain("Source: 10-Q filed Aug 26, 2026");
  expect(tsla!.className).toBe("masked");
  expect(tsla!.textContent).toContain("metadata only");
  expect(aapl!.querySelector("td.n.neg")?.textContent).toBe("-2.0%");
});

it("puts the peer median, without the target, under the table", async () => {
  await render(COMPS);
  const median = rows().find((r) => r.textContent?.startsWith("Peer median"))!;
  // only AAPL: TSLA is ungranted and NVDA is the target
  expect([...median.querySelectorAll("td.n")].map((td) => td.textContent)).toEqual(["$400.00B", "30.0%", "-2.0%"]);
  expect(median.textContent).toContain("1 readable peers");
  expect($(".foot")?.textContent).toContain("1 company shows metadata only");
});

it("suggests peers from the peers endpoint and edits the comps in the URL", async () => {
  await render(COMPS);
  expect(calls().some((u) => u.pathname === `/api/query/v1/companies/${NVDA}/peers`)).toBe(true);
  const chips = [...container.querySelectorAll<HTMLButtonElement>("button.chip")];
  expect(chips.map((c) => c.textContent)).toEqual(["+ KO (metadata only)"]); // AAPL is already in
  await click(chips[0]);
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${TSLA},${AAPL},${KO}&cols=revenue_ttm,operating_margin_ttm,revenue_growth_ttm`);
  await click($(`button[aria-label="Remove Tesla"]`));
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${AAPL}&cols=revenue_ttm,operating_margin_ttm,revenue_growth_ttm`);
  await click($(`button[aria-label="Make Apple the target"]`));
  expect(window.location.hash).toBe(`#/compare?target=${AAPL}&peers=${NVDA},${TSLA}&cols=revenue_ttm,operating_margin_ttm,revenue_growth_ttm`);
});

it("adds a peer found by name or ticker", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  await render({ target: NVDA });
  const input = $('input[aria-label="Add peer"]') as HTMLInputElement;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, "KO");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => vi.advanceTimersByTime(250));
  await act(async () => {});
  expect(calls().find((u) => u.pathname.endsWith("/search"))?.searchParams.get("q")).toBe("KO");
  await click($('.co-results button'));
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${KO}`);
});

it("exports the table as CSV and charts the readable companies", async () => {
  await render(COMPS);
  await click([...container.querySelectorAll("button")].find((b) => b.textContent === "Export CSV"));
  const [name, blob] = vi.mocked(download).mock.calls[0]!;
  expect(name).toBe("comps-nvda.csv");
  const text = await new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(blob as Blob);
  });
  expect(text).toContain(`target,${NVDA},NVDA,Nvidia,financials`);
  expect(text).toContain("peer median (excludes target)");
  const select = $('select[aria-label="Metric to chart"]') as HTMLSelectElement;
  await act(async () => {
    select.value = "operating_margin_ttm";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await click([...container.querySelectorAll("button")].find((b) => b.textContent === "Chart these"));
  expect(onChart).toHaveBeenCalledWith([NVDA, AAPL], "operating_margin", "ttm", "Operating margin, TTM");
});

it("toggles columns and starts empty with a hint", async () => {
  await render({});
  expect($(".empty")?.textContent).toContain("Pick a target company");
  expect(calls().some((u) => u.pathname === "/api/query/v1/screener")).toBe(false);
  const roe = [...container.querySelectorAll("label.chk")].find((l) => l.textContent === "ROE")!;
  await click(roe.querySelector("input"));
  expect(window.location.hash).toContain("cols=revenue_ttm,revenue_growth_ttm,gross_margin_ttm,operating_margin_ttm,free_cash_flow_ttm,fcf_margin_ttm,net_debt_q,debt_to_equity_q,roe_ttm");
});

it("shows a bank's operating margin as n/a and leaves it out of the median, with a note", async () => {
  const JPM = "0000019617";
  const bank = row(entry({ company_id: JPM, name: "JPMORGAN CHASE & CO", tickers: ["JPM"], industry: "National Commercial Banks",
    values: { revenue_ttm: "180000000000", operating_margin_ttm: null } }));
  vi.mocked(fetch).mockImplementation(async (url) => new URL(String(url), "http://x").pathname.endsWith("/peers")
    ? Response.json(PEERS)
    : Response.json({ as_of: "", total: 3, limit: 3, offset: 0, sort: "name", columns: [], rows: [row(entry()), APPLE, bank] }));
  await render({ target: NVDA, peers: [AAPL, JPM], cols: ["revenue_ttm", "operating_margin_ttm"] });
  const jpm = rows().find((r) => r.textContent?.includes("JPM"))!;
  const cell = [...jpm.querySelectorAll("td.n")][1]!;
  expect(cell.textContent).toBe("n/a (bank)");
  expect(cell.getAttribute("title")).toContain("doesn't describe a bank");
  const median = rows().find((r) => r.textContent?.startsWith("Peer median"))!;
  const m = [...median.querySelectorAll("td.n")][1]!;
  expect(m.textContent).toBe("30.0% excl. banks");
  expect(m.getAttribute("title")).toContain("1 bank left out");
  expect($(".foot")?.textContent).toContain("Banks show n/a for operating margin");
  // company rows are a keyboard-navigable list; the median rows aren't
  expect(container.querySelectorAll("table.comps tbody tr[data-nav]")).toHaveLength(3);
});
