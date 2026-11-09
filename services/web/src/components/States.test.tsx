import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { ApiError, failureReason } from "../api";
import { ErrorPanel } from "./States";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const shown = (e: unknown) => {
  act(() => root.render(<ErrorPanel what="the results" detail={failureReason(e)} onRetry={() => undefined} />));
  return container.querySelector(".errpanel p")?.textContent;
};

it("says a failure usually clears only when it's one that does (a 5xx, no answer)", () => {
  expect(shown(new ApiError("query took longer than 5s; narrow it", 503))).toBe(
    "Couldn't load the results. The server didn't answer in time. It usually clears in a moment.");
  expect(shown(new TypeError("Failed to fetch"))).toContain("It usually clears in a moment.");
});

it("a 4xx's reason stands on its own: retrying the same request won't clear it", () => {
  expect(shown(new ApiError("search too broad: add words or a company", 422))).toBe(
    "Couldn't load the results. Search too broad: add words or a company.");
  expect(shown(new ApiError("not permitted", 403))).not.toContain("clears");
});
