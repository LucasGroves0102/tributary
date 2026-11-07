import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Bad({ fail }: { fail: boolean }) {
  if (fail) throw new Error("malformed body: rows.map is not a function");
  return <p>fine</p>;
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

it("a page that throws shows a way out, not a blank window or the error", async () => {
  const onReload = vi.fn();
  await act(async () => root.render(<ErrorBoundary onReload={onReload}><Bad fail /></ErrorBoundary>));
  expect(container.querySelector("[role=alert] h1")?.textContent).toBe("This page didn't load");
  expect(container.textContent).not.toContain("rows.map");
  await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
  expect(onReload).toHaveBeenCalledOnce();
  expect(container.querySelector<HTMLAnchorElement>('a[href="#/"]')?.textContent).toBe("Go to Home");
});

it("going to another page tries again", async () => {
  await act(async () => root.render(<ErrorBoundary resetKey="#/a"><Bad fail /></ErrorBoundary>));
  expect(container.querySelector("[role=alert]")).not.toBeNull();
  await act(async () => root.render(<ErrorBoundary resetKey="#/b"><Bad fail={false} /></ErrorBoundary>));
  expect(container.textContent).toBe("fine");
});
