import { afterEach, expect, it, vi } from "vitest";

import { loadScreens, prefetchScreens } from "./lazy";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("prefetches the other screens when the browser is idle", async () => {
  const idle = vi.fn();
  vi.stubGlobal("requestIdleCallback", idle);
  prefetchScreens();
  expect(idle).toHaveBeenCalledWith(expect.any(Function), { timeout: 4000 });
  idle.mock.calls[0]![0](); // runs every import; none may throw
  const modules = await loadScreens();
  const names = ["ChartBuilder", "Companies", "Company", "Compare", "Search", "About", "FilingReader", "AssistantMessage"];
  expect(modules.map((m, i) => typeof (m as Record<string, unknown>)[names[i]!])).toEqual(names.map(() => "function"));
});

it("falls back to a timer without requestIdleCallback", () => {
  vi.useFakeTimers();
  vi.stubGlobal("requestIdleCallback", undefined);
  const spy = vi.spyOn(globalThis, "setTimeout");
  prefetchScreens();
  expect(spy).toHaveBeenCalledWith(expect.any(Function), 2000);
});
