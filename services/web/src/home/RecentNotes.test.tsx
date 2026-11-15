import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { RecentNotes } from "./RecentNotes";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const NVDA = "0001045810";

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ as_of: "", checked_at: null, companies: 1, notes: [] })));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const render = async (props: Partial<Parameters<typeof RecentNotes>[0]>) => {
  await act(async () => {
    root.render(<RecentNotes token="t" name={(id) => id} filingsFor={new Set()} {...props} />);
  });
};
const asked = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));

it("with the catalog open, asks only for the user's own companies", async () => {
  await render({ catalogOpen: true, companies: [NVDA] });
  expect(asked()[0]!.searchParams.getAll("company_id")).toEqual([NVDA]);
  expect(container.textContent).toContain("Recent data notes for your companies");
  expect(container.textContent).toContain("companies you watch or analyse");
});

it("with the catalog open and nothing chosen, asks for nothing and says how to get notes", async () => {
  await render({ catalogOpen: true, companies: [] });
  expect(fetch).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Watch a company or start an analysis");
});

it("waits for the watchlist rather than showing every company meanwhile", async () => {
  await render({ catalogOpen: true, companies: null });
  expect(fetch).not.toHaveBeenCalled();
});

it("#24: checks last ran reads Data status's run, not these companies' newest check", async () => {
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
  vi.mocked(fetch).mockImplementation(async (u) => String(u).endsWith("/v1/status/data")
    ? Response.json({ quality: { companies_checked: 7070, last_checked_at: ago(3 * 60_000), checks: [] } })
    : Response.json({ as_of: "", checked_at: ago(18 * 3600_000), companies: 1, notes: [] }));
  await render({ catalogOpen: true, companies: [NVDA] });
  for (let i = 0; i < 3; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
  expect(container.querySelector(".card-h .sub")?.textContent).toContain("checks last ran 3 min ago");
  expect(container.textContent).not.toContain("18 h ago");
});

it("with the catalog closed, the companies the user can read are the API's to work out", async () => {
  await render({ catalogOpen: false });
  expect(asked()[0]!.searchParams.getAll("company_id")).toEqual([]);
});
