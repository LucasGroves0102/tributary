import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SearchResult } from "../types";
import { CompanyPicker, CompanySearch, resultTicker } from "./CompanySearch";

const result = (over: Partial<SearchResult>): SearchResult => ({
  company_id: "0000000001", name: "Acme", matched_on: "name", matched_alias: "Acme", is_current: true,
  tickers: ["ACM"], access: ["financials", "filings"], ...over,
});
const APPLE = result({ company_id: "0000320193", name: "Apple Inc.", tickers: ["AAPL"] });
const META = result({ company_id: "0001326801", name: "Meta Platforms", matched_on: "ticker", matched_alias: "FB", is_current: false, tickers: ["META"] });
const UNREAD = result({ company_id: "0000000009", name: "Some Trust", tickers: null, access: [] });

let results: SearchResult[] = [];
let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results })));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const input = () => container.querySelector<HTMLInputElement>("input")!;
const options = () => [...container.querySelectorAll<HTMLElement>('[role="option"]')];
const type = async (text: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(), text);
    input().dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
};
const key = (k: string) => act(async () => {
  input().dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
});
const asked = () => new URL(String(vi.mocked(fetch).mock.calls.at(-1)![0]), "http://x");

it("asks the server per search, and shows ticker and name", async () => {
  results = [APPLE, UNREAD];
  await act(async () => root.render(<CompanySearch token="t" label="Company" placeholder="" onPick={vi.fn()} />));
  await type("app");
  expect(asked().pathname).toBe("/api/query/v1/companies/search");
  expect(asked().searchParams.get("q")).toBe("app");
  expect(asked().searchParams.get("access")).toBeNull();
  expect(options().map((o) => o.textContent)).toEqual(["AAPLApple", "Some Trustmetadata only"]);
});

it("narrows through the API and the display filter when only readable companies should show", async () => {
  results = [APPLE, UNREAD];
  await act(async () => root.render(
    <CompanySearch token="t" label="Company" placeholder="" onPick={vi.fn()} readable={{ has: (id) => id === APPLE.company_id }} />,
  ));
  await type("a");
  expect(asked().searchParams.get("access")).toBe("financials");
  expect(options()).toHaveLength(1);
});

it("works from the keyboard: arrows move, Enter picks, Escape clears", async () => {
  results = [APPLE, META];
  const onPick = vi.fn();
  await act(async () => root.render(<CompanySearch token="t" label="Company" placeholder="" onPick={onPick} />));
  await type("a");
  expect(input().getAttribute("aria-expanded")).toBe("true");
  await key("ArrowDown");
  expect(options()[1]!.getAttribute("aria-selected")).toBe("true");
  expect(input().getAttribute("aria-activedescendant")).toBe(options()[1]!.id);
  await key("Enter");
  expect(onPick).toHaveBeenCalledWith(META.company_id, "Meta Platforms", "META");
  expect(input().value).toBe("");
  await type("x");
  await key("Escape");
  expect(input().value).toBe("");
});

it("says so when nothing matches, and when a former name is what matched", async () => {
  results = [];
  await act(async () => root.render(<CompanySearch token="t" label="Company" placeholder="" onPick={vi.fn()} />));
  await type("zzz");
  expect(container.querySelector(".co-none")?.textContent).toBe("No company matches “zzz”.");
  results = [META];
  await type("FB");
  expect(options()[0]!.textContent).toContain("formerly FB");
  expect(resultTicker(META)).toBe("META");
  expect(resultTicker(result({ matched_on: "ticker", matched_alias: "BRK-A", tickers: ["BRK-B", "BRK-A"] }))).toBe("BRK-A");
});

it("clicking away closes the list without losing what was typed", async () => {
  results = [APPLE];
  await act(async () => root.render(<CompanySearch token="t" label="Company" placeholder="" onPick={vi.fn()} />));
  input().focus();
  await type("app");
  expect(options()).toHaveLength(1);
  await act(async () => input().blur());
  expect(options()).toHaveLength(0);
  expect(input().value).toBe("app");
  await act(async () => input().focus());
  expect(options()).toHaveLength(1);
});

it("offers suggestions with nothing typed, and CompanyPicker shows the choice and clears it", async () => {
  results = [];
  const onChange = vi.fn();
  const onClear = vi.fn();
  const suggestions = [{ companyId: APPLE.company_id, name: "Apple", ticker: "AAPL" }];
  await act(async () => root.render(
    <CompanyPicker token="t" label="Company" value={{ companyId: "1", name: "Nvidia", ticker: "NVDA" }} emptyLabel="All"
      onChange={onChange} onClear={onClear} suggestions={suggestions} />,
  ));
  const button = container.querySelector<HTMLButtonElement>('button[aria-label^="Company: Nvidia"]')!;
  expect(button.textContent).toContain("NVDA");
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="Clear Company"]')!.click());
  expect(onClear).toHaveBeenCalled();
  await act(async () => button.click());
  expect(options().map((o) => o.textContent)).toEqual(["AAPLApple"]);
  await act(async () => options()[0]!.click());
  expect(onChange).toHaveBeenCalledWith({ companyId: APPLE.company_id, name: "Apple", ticker: "AAPL" });
  // the button is back, and leaving the box without choosing brings it back too
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label^="Company: Nvidia"]')!.click());
  await act(async () => input().blur());
  expect(container.querySelector("input")).toBeNull();
});
