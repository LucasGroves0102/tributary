import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { WatchBody } from "../api";
import type { ScreenAlert, ScreenWatch, ScreenWatches } from "../types";
import type { ColumnSpec } from "../views/model";
import { ScreenAlertList } from "./ScreenAlerts";
import type { Watching } from "./useWatches";
import { SavedScreens, WatchControl } from "./WatchControl";

const ITEM = "3f2b8c1e-5d4a-4f6b-9c2d-1a2b3c4d5e6f";
const NVDA = "0001045810";
const TSLA = "0001318605";
const COLUMNS: ColumnSpec[] = [
  { key: "revenue_ttm", label: "Revenue", long: "Revenue", period: "TTM", kind: "usd" },
  { key: "operating_margin_ttm", label: "Op mgn", long: "Operating margin", period: "TTM", kind: "pct" },
];
const LIST: ScreenWatches = { watches: [], max_watches: 20, max_lines: 5, max_companies: 500, max_listed: 20, schedule: "after the nightly figures land" };
const WATCH: ScreenWatch = {
  id: "w1", source: "screen", item_id: ITEM, name: "Cheap growers", entering: true, leaving: false, columns: ["revenue_ttm"],
  lines: [{ column: "operating_margin_ttm", label: "Operating margin (TTM)", direction: "below", threshold: "0.2", company_id: null }],
  checked_at: "2026-11-18T11:00:00Z", outcome: "baseline", companies: 37, created_at: "2026-11-17T10:00:00Z", updated_at: "2026-11-17T10:00:00Z",
};

let root: Root;
let container: HTMLDivElement;
let saves: { itemId: string; body: WatchBody }[];
let stops: string[];

function watching(watch: ScreenWatch | null): Watching {
  const list = { ...LIST, watches: watch ? [watch] : [] };
  return {
    list, of: (id) => list.watches.find((w) => w.item_id === id) ?? null, watched: new Set(list.watches.map((w) => w.item_id)),
    save: async (itemId, body) => {
      saves.push({ itemId, body });
      return { ...WATCH, ...body, lines: body.lines.map((l) => ({ label: "", ...l })) } as ScreenWatch;
    },
    stop: async (itemId) => {
      stops.push(itemId);
    },
    reload: () => undefined,
  };
}

async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function show(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  await settle();
}

const button = (name: string) => [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === name);
async function click(el: HTMLElement | undefined) {
  expect(el).toBeTruthy();
  await act(async () => el!.click());
  await settle();
}
function type(el: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}
function pick(el: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

beforeEach(() => {
  saves = [];
  stops = [];
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results: [] })));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("starts watching a saved screen with the columns shown and a line in the API's units", async () => {
  await show(<WatchControl token="t" itemId={ITEM} name="Cheap growers" watching={watching(null)} columns={COLUMNS}
    shown={["revenue_ttm", "operating_margin_ttm"]} dirty={false} />);
  expect(container.textContent).toContain("Hear when companies enter or leave this screen");
  await click(button("Watch…"));
  const leaving = [...container.querySelectorAll<HTMLInputElement>("input[type=checkbox]")][1]!;
  await act(async () => leaving.click());
  await click(button("Add a line"));
  await act(async () => pick(container.querySelector<HTMLSelectElement>("select[aria-label=Column]")!, "operating_margin_ttm"));
  await act(async () => type(container.querySelector<HTMLInputElement>(".watch-value input")!, "25"));
  await settle();
  expect(container.querySelector(".watch-reads")?.textContent).toBe("Alerts when a company's Operating margin (TTM) rises above 25.0%");
  await click(button("Start watching"));
  expect(saves).toEqual([{ itemId: ITEM, body: {
    entering: true, leaving: false, columns: ["revenue_ttm", "operating_margin_ttm"],
    lines: [{ column: "operating_margin_ttm", label: "Operating margin (TTM)", direction: "above", threshold: "0.25", company_id: null }],
  } }]);
  expect(container.querySelector(".watch-form")).toBeNull();
});

it("refuses a line without a number or a company it names, before asking the API", async () => {
  await show(<WatchControl token="t" itemId={ITEM} name="Cheap growers" watching={watching(null)} columns={COLUMNS} shown={[]} dirty={false} />);
  await click(button("Watch…"));
  await click(button("Add a line"));
  await click(button("Start watching"));
  expect(container.querySelector("[role=alert]")?.textContent).toBe("Give “Revenue (TTM)” a number.");
  await act(async () => type(container.querySelector<HTMLInputElement>(".watch-value input")!, "500"));
  await act(async () => pick(container.querySelector<HTMLSelectElement>("select[aria-label=For]")!, "one"));
  await settle();
  await click(button("Start watching"));
  expect(container.querySelector("[role=alert]")?.textContent).toBe("Pick the company for that line, or choose any company.");
  expect(saves).toEqual([]);
});

it("shows a watched screen's last check, edits it as saved and stops it", async () => {
  await show(<WatchControl token="t" itemId={ITEM} name="Cheap growers" watching={watching(WATCH)} columns={COLUMNS} shown={[]} dirty />);
  const row = container.querySelector(".watch-row")!.textContent!;
  expect(row).toContain("Watching companies entering and Operating margin (TTM) below 20.0%");
  expect(row).toContain("First check done: 37 companies in it now.");
  await click(button("Change…"));
  expect(container.querySelector("[role=note]")?.textContent).toMatch(/unsaved changes/);
  expect(container.querySelector<HTMLInputElement>(".watch-value input")!.value).toBe("20");
  expect(container.querySelector('[role=radio][aria-checked="true"]')?.textContent).toBe("Falls below");
  await click(button("Stop watching"));
  expect(stops).toEqual([ITEM]);
});

it("lists the saved screens, the open one and the watched ones marked", async () => {
  const opened: string[] = [];
  await show(<SavedScreens screens={[{ item_id: ITEM, name: "Cheap growers" }, { item_id: "x2", name: "Banks" }]}
    watched={new Set([ITEM])} openId="x2" onOpen={(id) => opened.push(id)} />);
  const chips = [...container.querySelectorAll<HTMLButtonElement>(".screen-chip")];
  expect(chips.map((c) => c.textContent)).toEqual(["Cheap growers watched", "Banks"]);
  expect(chips[1]!.getAttribute("aria-current")).toBe("true");
  await click(chips[0]);
  expect(opened).toEqual([ITEM]);
});

it("lists screen alerts with links, values as read and what isn't listed, and marks them read", async () => {
  const alert: ScreenAlert = {
    id: "a1", watch_id: "w1", item_id: ITEM, name: "Cheap growers", hidden: 2, created_at: "2026-11-18T11:00:00Z", read_at: null,
    crossed: [{ line: 0, column: "operating_margin_ttm", label: "Operating margin (TTM)", direction: "below", threshold: "0.2", company_id: null,
      unit: "ratio", display: "percent", companies: [{ company_id: NVDA, value: "0.185" }], more: 0 }],
    entered: { companies: [TSLA], more: 4 },
  };
  const posts: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
    if (path === "/v1/alerts/screens") return Response.json({ alerts: [alert], has_more: false, schedule: "" });
    if (path === "/v1/alerts/notifications/read") {
      posts.push(JSON.parse(String(init?.body)));
      return Response.json({ marked: 1 });
    }
    return Response.json({ detail: "Not Found" }, { status: 404 });
  }));
  await show(<ScreenAlertList token="t" />);
  const item = container.querySelector(".alert-item.unread")!;
  expect(item.querySelector(".alert-what")?.textContent).toBe("1 crossed a line, 5 entered");
  expect(item.querySelector<HTMLAnchorElement>("a.alert-co")!.getAttribute("href")).toBe(`#/screen/${ITEM}`);
  const lines = [...item.querySelectorAll(".screen-alert-line")].map((p) => p.textContent);
  expect(lines[0]).toMatch(/^Operating margin \(TTM\) fell below 20\.0%: .+ 18\.5%$/);
  expect(lines[1]).toMatch(/^Entered: .+ and 4 more$/);
  expect([...item.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toContain(`#/company/${NVDA}`);
  expect(item.textContent).toContain("2 more companies you can no longer read aren't listed.");
  expect(posts).toEqual([{ ids: ["a1"] }]);
});

it("lists the screens the user watches, even before any alert (Riley, test 10, finding 14)", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
    if (path === "/v1/alerts/screens") return Response.json({ alerts: [], has_more: false, schedule: "" });
    return Response.json({ detail: "Not Found" }, { status: 404 });
  }));
  await show(<ScreenAlertList token="t" watches={{ ...LIST, watches: [WATCH] }} />);
  const list = container.querySelector(".watched-screens")!;
  expect(list.textContent).toContain("You watch a screen, checked once a day after the night's figures");
  expect(list.querySelector("a")?.getAttribute("href")).toBe(`#/screen/${ITEM}`);
  expect(list.textContent).toContain("Cheap growers: companies entering, 1 line (Operating margin (TTM)).");
  expect(list.textContent).toContain("First check done: 37 companies in it now.");
  // not the "choose Watch" invitation: the user already did
  expect(container.textContent).not.toContain("choose Watch");
  expect(container.textContent).toContain("No alerts from them yet");
});

it("shows nothing from an API without screen alerts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Not Found" }, { status: 404 })));
  await show(<ScreenAlertList token="t" />);
  expect(container.innerHTML).toBe("");
});
