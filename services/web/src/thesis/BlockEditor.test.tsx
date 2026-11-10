import { act, createRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Readable } from "../access";
import { quoteBlock, quoteLines, type Passage } from "../analysis/project";
import { clearChartCache } from "../chart/cache";
import { entry, NVDA } from "../fixtures";
import { BlockEditor, type BlockEditorHandle } from "./BlockEditor";
import type { EmbedItem } from "./model";

const AAPL = "0000320193";
const CHART_ID = "11111111-1111-4111-8111-111111111111";
const COMPS_ID = "22222222-2222-4222-8222-222222222222";
const GONE_ID = "99999999-9999-4999-8999-999999999999";
const CHART = {
  title: "NVDA revenue", range: 3, align: "calendar",
  layers: [{ kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L", hidden: false }],
};
const ITEMS: EmbedItem[] = [
  { item_id: CHART_ID, kind: "chart", name: "NVDA revenue", state: CHART },
  { item_id: COMPS_ID, kind: "comps", name: "Comps", state: { target: NVDA, peers: [AAPL], cols: ["revenue_ttm", "operating_margin_ttm"] } },
];
const PASSAGE: Passage = {
  text: "Data Center revenue grew 56%.", companyId: NVDA, accessionNo: "0001045810-26-000075", form: "10-Q",
  filed: "2026-08-27", period: "2026-07-26", section: "Item 2. MD&A",
};
const RELEASE: Passage = {
  ...PASSAGE, form: "8-K", accessionNo: "0001045810-26-000070", periodWord: "quarter", section: "EX-99.1 Outlook",
  text: "Revenue is expected to be $54.0 billion, plus or minus 2%.",
};
const DOC = [
  "# NVDA: thesis", "", "Demand keeps outrunning supply.", "", `[[embed:${CHART_ID}]]`, "", quoteBlock(PASSAGE), "",
  "Peers trail on margins.", "", `[[embed:${COMPS_ID}]]`, "", quoteLines(RELEASE), "", "End.",
].join("\n");

let container: HTMLDivElement;
let root: Root;
let current = "";
let removed: { what: string; before: string }[] = [];
const handle = createRef<BlockEditorHandle>();

function Harness({ initial }: { initial: string }) {
  const [md, setMd] = useState(initial);
  current = md;
  return (
    <BlockEditor markdown={md} onChange={setMd} onRemoved={(what, before) => removed.push({ what, before })} items={ITEMS}
      token="t" label={(id) => (id === NVDA ? "Nvidia (NVDA)" : "Apple (AAPL)")} readable={new Set([NVDA]) as unknown as Readable}
      hasTtm={() => true} onOpenChart={() => {}} handle={handle} />
  );
}

async function settle() {
  for (let i = 0; i < 6; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function render(markdown = DOC) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Harness initial={markdown} />));
  await settle();
}

function respond(url: string) {
  const u = new URL(url, "http://x");
  if (u.pathname.endsWith("/v1/formulas/evaluate")) {
    return Response.json({ formula: "revenue", unit: "USD", display: null, basis: "calendar", view: "ttm", alignment: "calendar", as_of: "2026-11-09T00:00:00Z", inputs: [], points: [
      { period: "CY2026 Q1", period_key: 8105, period_end: "2026-04-26", value: "148515000000", inputs: [], source_accessions: [] },
      { period: "CY2026 Q2", period_key: 8106, period_end: "2026-07-26", value: "165218000000", inputs: [], source_accessions: [] },
    ] });
  }
  if (u.pathname.endsWith("/v1/screener")) {
    const ids = u.searchParams.getAll("company_id");
    return Response.json({ total: ids.length, columns: [], rows: ids.map((id) => ({
      ...entry(), company_id: id, name: id === NVDA ? "Nvidia" : "Apple", tickers: [id === NVDA ? "NVDA" : "AAPL"],
      access: id === NVDA ? ["financials"] : [], values: id === NVDA ? { revenue_ttm: "165218000000", operating_margin_ttm: "0.62" } : {},
    })) });
  }
  return new Response("{}", { status: 404 });
}

const cards = () => [...container.querySelectorAll<HTMLElement>(".block-card")];
const cardLabels = () => cards().map((c) => c.getAttribute("aria-label"));
const boxes = () => [...container.querySelectorAll<HTMLTextAreaElement>("textarea.block-text")];
const byLabel = (label: string) => container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
const key = async (el: HTMLElement, k: string, alt = false) => {
  await act(async () => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, altKey: alt, bubbles: true })));
  await settle();
};
function type(el: HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  clearChartCache();
  removed = [];
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("draws a chart card as a small preview that expands to full size and back", async () => {
  await render();
  const chart = cards()[0]!;
  const toggle = [...chart.querySelectorAll("button")].find((b) => b.textContent === "Expand")!;
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  const body = document.getElementById(toggle.getAttribute("aria-controls")!)!;
  expect(body.classList.contains("preview")).toBe(true);
  // the small-multiple drawing: a 460-wide viewBox, beside the full chart's wider one
  const width = () => Number(chart.querySelector("svg.lchart-svg")!.getAttribute("viewBox")!.split(" ")[2]);
  expect(width()).toBe(460);
  await act(async () => toggle.click());
  await settle();
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(toggle.textContent).toBe("Show smaller");
  expect(body.classList.contains("preview")).toBe(false);
  expect(width()).toBeGreaterThan(460);
  await act(async () => toggle.click());
  expect(body.classList.contains("preview")).toBe(true);
  // only charts: the comps card has no toggle, and looking changes nothing
  expect([...cards()[2]!.querySelectorAll("button")].some((b) => b.textContent === "Expand")).toBe(false);
  expect(current).toBe(DOC);
});

it("draws embeds and pinned quotes as cards, never as [[embed]] lines; comps stay a table", async () => {
  await render();
  expect(cardLabels()).toEqual([
    "Chart “NVDA revenue”", "Quote from the 10-Q", "Comps table “Comps”", "Earnings release passage",
  ]);
  for (const box of boxes()) expect(box.value).not.toContain("[[embed:");
  expect(boxes().map((b) => b.value)).toEqual([
    "# NVDA: thesis\n\nDemand keeps outrunning supply.\n", "", "\nPeers trail on margins.\n", "", "\nEnd.",
  ]);
  // the comps card is the comps table (wishlist B.8), with the median and the masked peer
  const comps = cards()[2]!;
  expect(comps.querySelector("table.comps")).not.toBeNull();
  expect(comps.querySelector("svg")).toBeNull();
  expect(comps.textContent).toContain("metadata only");
  expect(comps.querySelector("tr.agg td.first")?.textContent).toBe("Peer median");
  // a quote says what it is and the period it reports on (H13), its citation linked to EDGAR
  const quote = cards()[1]!;
  expect(quote.querySelector(".embed-h")?.textContent).toContain("Quote 10-Q · quarter ended 2026-07-26");
  expect(quote.querySelector("blockquote")?.textContent).toContain("Data Center revenue grew 56%.");
  expect(quote.querySelector<HTMLAnchorElement>("blockquote a")?.href).toContain("https://www.sec.gov/Archives/edgar/data/1045810/");
  expect(cards()[3]!.querySelector(".embed-h")?.textContent).toContain("Earnings release 8-K · quarter ended 2026-07-26");
  // the chart card draws the chart
  expect(cards()[0]!.querySelector("svg")).not.toBeNull();
  // nothing changed by looking
  expect(current).toBe(DOC);
});

it("moves a card with its buttons and the keyboard, focus following, and says so", async () => {
  await render();
  expect(byLabel("Move the chart “NVDA revenue” up").disabled).toBe(false);
  await act(async () => byLabel("Move the chart “NVDA revenue” up").click());
  await settle();
  expect(current.split("\n").slice(0, 5)).toEqual(["# NVDA: thesis", "", `[[embed:${CHART_ID}]]`, "", "Demand keeps outrunning supply."]);
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Chart “NVDA revenue”");
  expect(container.querySelector('[role="status"]')?.textContent).toBe("Moved the chart “NVDA revenue” up.");
  // Alt+↓ on the focused card moves it back: the Markdown is as it was, byte for byte
  await key(document.activeElement as HTMLElement, "ArrowDown", true);
  expect(current).toBe(DOC);
  // the quote moves whole, past the chart
  const quote = cards()[1]!;
  quote.focus();
  await key(quote, "ArrowUp", true);
  expect(cardLabels().slice(0, 2)).toEqual(["Quote from the 10-Q", "Chart “NVDA revenue”"]);
  expect(current).toContain(quoteBlock(PASSAGE));
  // at the ends, the buttons are off
  await render(`[[embed:${CHART_ID}]]\n\nText.`);
  expect(byLabel("Move the chart “NVDA revenue” up").disabled).toBe(true);
});

it("removes a card with Delete or its button, keeping the text before for Undo", async () => {
  await render();
  const comps = cards()[2]!;
  comps.focus();
  await key(comps, "Delete");
  expect(current).not.toContain(COMPS_ID);
  expect(current).toContain("Peers trail on margins.\n\n> Revenue is expected");
  expect(removed).toEqual([{ what: "comps table “Comps”", before: DOC }]);
  expect(container.querySelector('[role="status"]')?.textContent).toContain("Removed the comps table “Comps”.");
  await act(async () => byLabel("Remove the earnings release passage").click());
  await settle();
  expect(current).not.toContain("Revenue is expected");
  expect(cardLabels()).toEqual(["Chart “NVDA revenue”", "Quote from the 10-Q"]);
});

it("keeps typing in a text box exact, and an embed of a missing item is a card to remove", async () => {
  await render(`Intro.\n\n[[embed:${GONE_ID}]]\n\nEnd.`);
  expect(cards()[0]!.textContent).toContain("no longer in this workspace");
  await act(async () => type(boxes()[1]!, "\nThe end."));
  expect(current).toBe(`Intro.\n\n[[embed:${GONE_ID}]]\n\nThe end.`);
  await act(async () => byLabel("Remove the embed of a missing item").click());
  await settle();
  expect(current).toBe("Intro.\n\nThe end.");
});

it("tells the page where the cursor is and selects a quote in the right text box", async () => {
  await render();
  expect(handle.current!.cursor()).toBeNull(); // nothing placed yet: an embed goes at the end
  const second = boxes()[2]!; // "\nPeers trail on margins.\n", from line 10
  await act(async () => {
    second.focus();
    second.setSelectionRange(6, 6);
    second.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowRight", bubbles: true }));
  });
  const at = handle.current!.cursor()!;
  expect([DOC.slice(at - 5, at), DOC.slice(at, at + 6)]).toEqual(["Peers", " trail"]);
  const start = DOC.indexOf("trail on margins");
  await act(async () => {
    expect(handle.current!.select(start, start + 5)).toBe(true);
  });
  expect(document.activeElement).toBe(boxes()[2]);
  expect(boxes()[2]!.value.slice(boxes()[2]!.selectionStart, boxes()[2]!.selectionEnd)).toBe("trail");
});
