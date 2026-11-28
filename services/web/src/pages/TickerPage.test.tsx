import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TickerPage } from "./TickerPage";

let root: Root;
let container: HTMLDivElement;
const replaced: string[] = [];

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  replaced.length = 0;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const answer = (results: unknown[]) => vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results })));

it("puts the company's own address in place of its ticker's, on the same tab (Marek 16)", async () => {
  answer([
    { company_id: "0001234567", name: "Not it", matched_on: "name", matched_alias: "NVDA Fund", is_current: true, tickers: ["NVDQ"], access: [] },
    { company_id: "0001045810", name: "Nvidia", matched_on: "ticker", matched_alias: "NVDA", is_current: true, tickers: ["NVDA"], access: [] },
  ]);
  await act(async () => root.render(<TickerPage token="t" ticker="NVDA" tab="statements" replace={(u) => replaced.push(u)} />));
  await act(async () => {});
  expect(replaced).toEqual(["#/company/0001045810/statements"]);
});

it("says no company has a ticker nobody trades as, with a heading like every not-found page", async () => {
  answer([{ company_id: "0001234567", name: "Zed", matched_on: "name", matched_alias: "ZZZQ", is_current: true, tickers: ["ZED"], access: [] }]);
  await act(async () => root.render(<TickerPage token="t" ticker="ZZZQ" replace={(u) => replaced.push(u)} />));
  await act(async () => {});
  expect(replaced).toEqual([]);
  expect(container.querySelector(".not-found h1")?.textContent).toBe("No company with the ticker ZZZQ");
  expect(container.querySelector(".not-found a")?.getAttribute("href")).toBe("#/companies");
});
