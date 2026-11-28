import { afterEach, expect, it, vi } from "vitest";

import { rememberScroll, restoreScroll, savedScroll } from "./scrollMemory";

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState(null, "");
});

it("keeps where a page was left on its own history entry, once the scrolling stops (Marek 68)", () => {
  vi.useFakeTimers();
  const stop = rememberScroll();
  Object.defineProperty(window, "scrollY", { value: 400, configurable: true });
  window.dispatchEvent(new Event("scroll"));
  expect(savedScroll()).toBeNull();
  vi.advanceTimersByTime(200);
  expect(savedScroll()).toBe(400);
  stop();
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
});

it("a new entry has no place: it starts at the top", () => {
  window.history.replaceState(null, "");
  expect(savedScroll()).toBeNull();
  window.history.replaceState({ scroll: 0 }, "");
  expect(savedScroll()).toBeNull();
});

it("goes back as far as the page is tall, waiting a moment for content still arriving", () => {
  const to = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  restoreScroll(400, () => 1e9);
  expect(to).toHaveBeenCalledWith(0, expect.any(Number));
  to.mockRestore();
});
