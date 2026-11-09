/** "Connect your data" in the web app: hidden when the server has it off, the Connections page
 * (the key goes out once in the PUT body and is never shown or kept), the Price tab (a prompt
 * without a connection, never a price request), and price chart layers (pure rules). */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uiContext } from "../assistant/actions";
import { Price, inRange } from "../company/Price";
import { basis, emptyChart, layerKey, priceRequest, restoreChart, type ChartState } from "../chart/model";
import { priceSeries, quarterOf } from "../chart/prices";
import { toSeries } from "../chart/series";
import { Settings } from "../pages/Settings";
import { parse, href } from "../route";
import type { MarketConnections, PriceRow, Prices, Valuation } from "../types";
import { stateOf } from "../workspaces/model";
import { clearMarketData } from "./useMarketData";

const NVDA = "0001045810";
const KEY = "test".repeat(10); // obviously fake: low entropy, never a real key
const PROVIDERS = [{ id: "tiingo", name: "Tiingo", signup_url: "https://www.tiingo.com/account/api/token",
  terms_url: "https://www.tiingo.com/about/pricing", attribution: "End-of-day prices from Tiingo, fetched with your own API key.",
  data: "End-of-day daily prices for US equities" }];
const CONNECTED = { provider: "tiingo", key_hint: "…4567", created_at: "2026-11-02T15:00:00Z", validated_at: "2026-11-02T15:00:00Z" };
const META = { company_id: NVDA, provider: "tiingo", attribution: PROVIDERS[0]!.attribution, ticker: "NVDA",
  status: "ok" as const, fetched_at: "2026-11-02T15:00:00Z", stale: false, warning: null };
const ROWS: PriceRow[] = [
  { day: "2025-09-30", close: "186.58", adj_close: "186.50" },
  { day: "2025-12-31", close: "190.00", adj_close: "189.90" },
  { day: "2026-09-29", close: "180.00", adj_close: "180.00" },
  { day: "2026-09-30", close: "186.58", adj_close: "186.58" },
  { day: "2026-10-30", close: "202.89", adj_close: null },
];
const VALUATION: Valuation = {
  ...META,
  price: { close: "202.89", day: "2026-10-30", ticker: "NVDA" },
  inputs: {
    shares_outstanding: { value: "24300000000", unit: "shares", period_end: "2026-08-20",
      citation: { accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26", xbrl_tag: null, filing_url: null } },
    net_income: { value: "99200000000", unit: "USD", period_end: "2026-07-26",
      citation: { accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26", xbrl_tag: null, filing_url: null } },
    revenue: null, operating_income: null, net_debt: null,
  },
  formulas: { market_cap: "close × shares_outstanding", pe: "market cap / TTM net income", ps: "market cap / TTM revenue",
    ev: "market cap + net debt", ev_ebit: "(market cap + net debt) / TTM operating income" },
  share_classes: null,
  market_cap: { value: "4930227000000", price_day: "2026-10-30", shares_period_end: "2026-08-20" },
  enterprise_value: null,
  multiples: { pe: { value: "49.70", note: null }, ps: { value: null, note: "no TTM revenue in the filings" },
    ev_ebit: { value: null, note: "no net debt in the filings" } },
  note: null,
};

let root: Root;
let container: HTMLDivElement;
let calls: { method: string; path: string; body: string | null }[];
let connections: MarketConnections | null;

function respond(url: string, init?: RequestInit): Response {
  const path = new URL(url, "http://x").pathname;
  const method = init?.method ?? "GET";
  calls.push({ method, path, body: typeof init?.body === "string" ? init.body : null });
  if (path === "/api/query/v1/connections") {
    return connections ? Response.json(connections) : Response.json({ detail: "Not Found" }, { status: 404 });
  }
  if (path === "/api/query/v1/connections/tiingo" && method === "PUT") {
    const body = JSON.parse(String(init!.body)) as { api_key: string };
    if (body.api_key !== KEY) {
      return Response.json({ detail: "the provider refused this API key: check it and try again", code: "bad_key" }, { status: 422 });
    }
    connections = { providers: PROVIDERS, connections: [CONNECTED] };
    return Response.json({ connection: CONNECTED });
  }
  if (path === "/api/query/v1/connections/tiingo" && method === "DELETE") {
    connections = { providers: PROVIDERS, connections: [] };
    return Response.json({ deleted: true });
  }
  if (path.endsWith("/prices")) return Response.json({ ...META, rows: ROWS } satisfies Prices);
  if (path.endsWith("/valuation")) return Response.json(VALUATION);
  return Response.json({ detail: "unexpected" }, { status: 500 });
}

async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

const text = () => container.textContent ?? "";

function type(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  clearMarketData();
  calls = [];
  connections = { providers: PROVIDERS, connections: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => respond(url, init)));
});

afterEach(() => {
  act(() => root?.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("Settings → Connections", () => {
  it("says the server has none when the feature is off", async () => {
    connections = null; // an older API: 404
    await mount(<Settings token="t" />);
    expect(text()).toContain("No data connections are available");
    expect(container.querySelector("input")).toBeNull();
  });

  it("reads no providers offered as off (the API's answer without a master key: no 404 in the console)", async () => {
    connections = { providers: [], connections: [] };
    await mount(<Settings token="t" />);
    expect(text()).toContain("No data connections are available");
    expect(container.querySelector("input")).toBeNull();
  });

  it("connects with a password field, clears it, and shows only the hint", async () => {
    await mount(<Settings token="t" />);
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Tiingo API key"]')!;
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    await act(async () => type(input, KEY));
    await act(async () => container.querySelector("form")!.requestSubmit());
    await settle();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(JSON.parse(put.body!)).toEqual({ api_key: KEY });
    expect(input.value).toBe("");
    expect(text()).toContain("…4567");
    expect(text()).not.toContain(KEY);
    expect(container.innerHTML).not.toContain(KEY);
    expect(JSON.stringify({ ...localStorage })).not.toContain(KEY);
  });

  it("shows the API's error for a refused key", async () => {
    await mount(<Settings token="t" />);
    const input = container.querySelector<HTMLInputElement>("input")!;
    await act(async () => type(input, "a".repeat(40)));
    await act(async () => container.querySelector("form")!.requestSubmit());
    await settle();
    expect(container.querySelector('[role="alert"]')!.textContent).toContain("refused this API key");
  });

  it("disconnects", async () => {
    connections = { providers: PROVIDERS, connections: [CONNECTED] };
    await mount(<Settings token="t" />);
    const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Disconnect")!;
    await act(async () => button.click());
    await settle();
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/query/v1/connections/tiingo")).toBe(true);
    expect(text()).toContain("its prices are deleted");
    expect(text()).not.toContain("…4567");
  });
});

describe("Price tab", () => {
  it("without a connection shows a prompt and never asks for prices", async () => {
    await mount(<Price token="t" companyId={NVDA} name="Nvidia" />);
    expect(text()).toContain("Connect your market data");
    expect(container.querySelector(`a[href="${href({ view: "settings" })}"]`)).not.toBeNull();
    expect(calls.some((c) => c.path.endsWith("/prices") || c.path.endsWith("/valuation"))).toBe(false);
  });

  it("renders nothing when the feature is off", async () => {
    connections = null;
    await mount(<Price token="t" companyId={NVDA} name="Nvidia" />);
    expect(text()).toBe("");
    expect(calls.some((c) => c.path.endsWith("/prices"))).toBe(false);
  });

  it("with a connection draws the closes and the valuation with dates", async () => {
    connections = { providers: PROVIDERS, connections: [CONNECTED] };
    await mount(<Price token="t" companyId={NVDA} name="Nvidia" />);
    expect(container.querySelector(".price-chart svg")!.getAttribute("aria-label")).toContain("last $202.89");
    expect(text()).toContain("$4.93T");
    expect(text()).toContain("49.7×");
    expect(text()).toContain("no TTM revenue in the filings");
    expect(text()).toContain("Oct 30, 2026");
    expect(text()).toContain("End-of-day prices from Tiingo");
  });
});

describe("price rules", () => {
  it("inRange keeps the last N years", () => {
    expect(inRange(ROWS, 1).map((r) => r.day)).toEqual(["2025-12-31", "2026-09-29", "2026-09-30", "2026-10-30"]);
    expect(inRange(ROWS, null)).toHaveLength(5);
  });

  it("quarter-end closes on calendar quarters, YoY and indexed over adjusted closes", () => {
    expect(quarterOf("2026-10-30")).toEqual({ key: 2026 * 4 + 3, period: "CY2026 Q4" });
    const q = priceSeries({ price: { company: NVDA, transform: "q" } }, ROWS);
    expect(q.points.map((p) => [p.period, p.period_end, p.value])).toEqual([
      ["CY2025 Q3", "2025-09-30", "186.58"],
      ["CY2025 Q4", "2025-12-31", "190.00"],
      ["CY2026 Q3", "2026-09-30", "186.58"],
      ["CY2026 Q4", "2026-10-30", "202.89"],
    ]);
    expect(q.points.every((p) => p.inputs.length === 0)).toBe(true);
    const yoy = priceSeries({ price: { company: NVDA, transform: "yoy" } }, ROWS);
    expect(yoy.points.map((p) => [p.period, Number(p.value).toFixed(4)])).toEqual([
      ["CY2026 Q3", (186.58 / 186.5 - 1).toFixed(4)],
      ["CY2026 Q4", (202.89 / 189.9 - 1).toFixed(4)], // no adjusted close: the close
    ]);
    const idx = toSeries(priceSeries({ price: { company: NVDA, transform: "idx" } }, ROWS), { id: 1, transform: "idx", type: "line", axis: "L" }, "x", 0);
    expect(idx.points[0]!.y).toBe(100);
    const ranged = priceSeries({ price: { company: NVDA, transform: "q", from: "2026-01-01" } }, ROWS);
    expect(ranged.points.map((p) => p.period)).toEqual(["CY2026 Q3", "CY2026 Q4"]);
  });

  it("a price layer is calendar, keyed, saved without values and restored", () => {
    const s: ChartState = {
      ...emptyChart(), align: "fiscal", nextId: 3,
      layers: [
        { id: 1, kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "bar", axis: "L" },
        { id: 2, kind: "price", companyId: NVDA, transform: "q", type: "line", axis: "R" },
      ],
    };
    expect(basis(s)).toBe("calendar");
    expect(basis({ ...s, layers: [s.layers[0]!, { ...s.layers[1]!, hidden: true }] })).toBe("fiscal");
    expect(priceRequest(s.layers[1]!, { ...s, range: "max" })).toEqual({ price: { company: NVDA, transform: "q" } });
    expect(JSON.parse(layerKey(s.layers[1]!, { ...s, range: "max" }, () => true)!)).toEqual({ price: { company: NVDA, transform: "q" } });
    const saved = stateOf({ kind: "chart", chart: s }) as { layers: Record<string, unknown>[] };
    expect(saved.layers[1]).toEqual({ kind: "price", transform: "q", type: "line", axis: "R", companyId: NVDA });
    const back = restoreChart({ ...saved, layers: [...saved.layers, { kind: "price", companyId: "NVDA" }, { kind: "price", companyId: NVDA, transform: "ttm" }] });
    expect(back.layers.map((l) => [l.kind, l.transform])).toEqual([["metric", "ttm"], ["price", "q"], ["price", "q"]]);
  });

  it("the assistant never gets price layers or the Price tab", () => {
    const s: ChartState = {
      ...emptyChart(),
      layers: [{ id: 1, kind: "price", companyId: NVDA, transform: "q", type: "line", axis: "L" }],
    };
    expect(uiContext({ view: "chart" }, s, () => null).chart!.layers).toEqual([]);
    expect(uiContext({ view: "company", companyId: NVDA, tab: "price" }, s, () => null).tab).toBe("overview");
    expect(uiContext({ view: "settings" }, s, () => null).page).toBe("about");
  });

  it("routes", () => {
    expect(parse("#/settings")).toEqual({ view: "settings" });
    expect(href({ view: "settings" })).toBe("#/settings");
    expect(parse(`#/company/${NVDA}/price`)).toEqual({ view: "company", companyId: NVDA, tab: "price" });
  });
});
