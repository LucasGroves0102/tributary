import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";

const ME = {
  user_id: "alice",
  companies: [{ company_id: "0001045810", name: "NVIDIA CORP", tiers: ["financials"] }],
};

function respond(url: string): Response {
  if (url.endsWith("/v1/demo/users")) return Response.json({ users: ["alice", "bob"] });
  if (url.endsWith("/v1/demo/token")) return Response.json({ token: "t", expires_in: 3600 });
  if (url.endsWith("/v1/me")) return Response.json(ME);
  return new Response(null, { status: 404 });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  // Current Chrome and Edge return a Promise from scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as never;
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("renders the signed-in user's access after loading", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(<App />));
  await act(async () => {});
  expect(container.textContent).toContain("NVIDIA CORP");
  act(() => root.unmount());
});
