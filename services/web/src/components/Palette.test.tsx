import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SearchResult } from "../types";
import { Palette, WATCH_PREFIX, type PaletteActions } from "./Palette";

const TESLA: SearchResult = { company_id: "0001318605", name: "Tesla, Inc.", matched_on: "ticker", matched_alias: "TSLA", is_current: true, tickers: ["TSLA"], access: ["financials", "filings"] };
const APPLE: SearchResult = { company_id: "0000320193", name: "Apple Inc.", matched_on: "name", matched_alias: "Apple Inc.", is_current: true, tickers: ["AAPL"], access: [] };

let results: SearchResult[] = [];
let root: Root;
let container: HTMLDivElement;
const actions = (): PaletteActions => ({
  go: vi.fn(), company: vi.fn(), watch: vi.fn(), ask: vi.fn(), searchFilings: vi.fn(), compareTickers: vi.fn(),
  chartTicker: vi.fn(), toggleTheme: vi.fn(), exportWatchlist: vi.fn(), openAssistant: vi.fn(),
});

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results })));
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function open(initial: string, a: PaletteActions, watched?: Set<string>, companies = [] as { companyId: string; name: string; sub: string }[]) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(
    <Palette token="t" initial={initial} companies={companies} watched={watched} actions={a} onClose={vi.fn()} />,
  ));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
}

const options = () => [...container.querySelectorAll('[role="option"]')] as HTMLElement[];

it("finds a company by a ticker its name doesn't contain (the server decides, not the text filter)", async () => {
  results = [TESLA];
  await open("TSLA", actions());
  const first = options()[0]!;
  expect(first.textContent).toContain("Tesla");
  expect(first.textContent).toContain("TSLA");
});

it("asks for one-letter tickers too, ranked as the server ranks", async () => {
  results = [APPLE, TESLA];
  await open("a", actions());
  const url = new URL(String(vi.mocked(fetch).mock.calls[0]![0]), "http://x");
  expect(url.pathname).toBe("/api/query/v1/companies/search");
  expect(url.searchParams.get("q")).toBe("a");
  expect(options().slice(0, 2).map((o) => o.textContent)).toEqual([expect.stringContaining("Apple"), expect.stringContaining("Tesla")]);
});

it("keeps a matching page reachable beside a full list of companies", async () => {
  results = Array.from({ length: 8 }, (_, i) => ({ ...TESLA, company_id: `000000000${i}`, name: `Chart Industries ${i}` }));
  await open("chart", actions());
  const labels = options().map((o) => o.textContent ?? "");
  expect(labels.some((l) => l.startsWith("Chart builder"))).toBe(true);
  expect(labels.some((l) => l.startsWith("Search filings"))).toBe(true);
  expect(labels.filter((l) => l.startsWith("Chart Industries")).length).toBeGreaterThan(3);
});

it("watch: marks a company already on the watchlist, lists it last and doesn't add it again", async () => {
  results = [TESLA, APPLE];
  const a = actions();
  await open(`${WATCH_PREFIX}a`, a, new Set([TESLA.company_id]));
  const [apple, tesla] = options();
  expect(apple!.textContent).toContain("add to watchlist");
  expect(tesla!.textContent).toContain("on your watchlist");
  expect(tesla!.getAttribute("aria-disabled")).toBe("true");
  await act(async () => tesla!.click());
  expect(a.watch).not.toHaveBeenCalled();
  await act(async () => apple!.click());
  expect(a.watch).toHaveBeenCalledWith(APPLE.company_id);
});
