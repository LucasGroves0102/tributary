import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { EarningsResponse, EventsResponse, ScreenerRow } from "../types";
import { EarningsView } from "./Earnings";
import { EventsCard } from "./Events";
import VRT_EARNINGS from "./fixtures/vrt-earnings.json";
import VRT_EVENTS from "./fixtures/vrt-events.json";
import VRT_RECAP from "./fixtures/vrt-recap.json";
import VRT_ROW from "./fixtures/vrt-row.json";
import { Overview } from "./Overview";

// Vertiv as production serves it (recap, key stats) and as the branch's Query API answers from its
// real filings (events, earnings; captured read-only from hermes on 2026-11-08)
const VRT = "0001674101";
const RELEASE = "0001628280-26-067707";
const UIG = "0001628280-26-067393";
let earnings: EarningsResponse | null;
let events: EventsResponse | null;

function respond(url: string): Response {
  const p = new URL(url, "http://x").pathname.replace(/^\/api\/query/, "");
  if (p === `/v1/companies/${VRT}/earnings`) return earnings ? Response.json(earnings) : Response.json({ detail: "Not Found" }, { status: 404 });
  if (p === `/v1/companies/${VRT}/events`) return events ? Response.json(events) : Response.json({ detail: "Not Found" }, { status: 404 });
  if (p === "/v1/filings/0001628280-26-067893/recap") return Response.json(VRT_RECAP);
  if (p.endsWith("/financials")) return Response.json({ as_of: "", rows: [] });
  if (p.includes("/sections/1")) return Response.json({ sections: [] });
  return Response.json({ detail: "forbidden" }, { status: 403 });
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  earnings = structuredClone(VRT_EARNINGS) as EarningsResponse;
  events = structuredClone(VRT_EVENTS) as EventsResponse;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function render(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(node));
  for (let i = 0; i < 4; i++) await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...container.querySelectorAll<HTMLElement>(sel)];
const view = (over: Partial<Parameters<typeof EarningsView>[0]> = {}) => (
  <EarningsView token="t" companyId={VRT} name="Vertiv" canReadFilings canReadFinancials {...over} />
);

it("shows the release beside its 10-Q figures, its outlook and its adjustments, each with where to read it", async () => {
  await render(view());
  const seg = $$(".earnings-pick .seg button").map((b) => [b.textContent, b.getAttribute("aria-pressed")]);
  expect(seg).toEqual([["Sep 30, 2026", "true"], ["Jun 30, 2026", "false"]]);
  const release = $(".release-card")!;
  expect(release.querySelector(".release-headline")?.textContent).toMatch(/^Vertiv Reports Strong Third Quarter 2026 with Net Sales Growth of 61%/);
  expect(release.textContent).toContain("8-K filed Oct 28, 2026 · Results of operations, Regulation FD disclosure · Exhibit 99.1");
  expect([...release.querySelectorAll("a")].find((a) => a.textContent === "Read the release")?.getAttribute("href"))
    .toBe(`#/filing/${VRT}/${RELEASE}/EX-99.1`);
  // the quarter's own figures from the 10-Q (the recap route), with the year-ago comparison
  const figures = $$("section.card").find((s) => s.querySelector("h3")?.textContent === "Reported figures")!;
  expect(figures.querySelector(".sub")?.textContent).toBe("10-Q filed Oct 28, 2026 · vs the same quarter a year ago");
  expect([...figures.querySelectorAll("thead th")].map((t) => t.textContent)).toEqual(["Metric", "FY2026 Q3", "FY2025 Q3", "YoY"]);
  const rows = [...figures.querySelectorAll("tbody tr")].map((r) => r.querySelector("td")?.textContent);
  expect(rows).toEqual(["Revenue", "Operating income", "Net income", "EPS (diluted)", "Free cash flow", "Gross margin", "Operating margin", "Net margin"]);
  expect(figures.querySelector("tbody tr td.n b")?.textContent).toBe("$4.31B");
  // outlook: the release's own Guidance blocks, quoted line by line, each with its place in the release
  const blocks = $$(".release-block").filter((b) => b.closest("section")?.querySelector("h3")?.textContent === "Outlook stated in the release");
  expect(blocks.map((b) => b.querySelector(".rl.heading")?.textContent)).toEqual([
    "Fourth Quarter 2026 Guidance", "Full Year 2026 Guidance", "Updated Full Year and Fourth Quarter 2026 Guidance",
    "Fourth Quarter 2026 Guidance", "Full Year 2026 Guidance"]);
  expect([...blocks[3]!.querySelectorAll(".rl")].map((l) => l.textContent).slice(1, 3)).toEqual(["Net sales $4,313M - $4,513M", "Organic net sales growth(2)"]);
  const at = blocks[3]!.querySelector<HTMLAnchorElement>("a.reader-link")!.getAttribute("href")!;
  expect(at).toMatch(new RegExp(`^#/filing/${VRT}/${RELEASE}/EX-99\\.1\\?at=\\d+$`));
  expect(Number(at.split("at=")[1])).toBe(earnings!.quarters[0]!.outlook[3]!.citation.char_start);
  // adjustments: each reconciliation by its heading, its lines as written (folded until opened)
  const tables = $$(".release-table-box");
  expect(tables.map((t) => t.querySelector("summary")?.textContent)).toContain("Reconciliation from operating profit (loss) to adjusted operating profit (loss)");
  expect(tables[2]!.querySelector("pre")?.textContent).toContain("Adjusted operating profit (loss) $ 1,086.9 $ 595.6 $ 2,377.5 $ 1,421.6");
  // outside an analysis there's nothing to pin into
  expect($(".pin-btn")).toBeNull();
  // no beat or miss: the only mentions are the two that say there is none
  const said = container.textContent!.replace("no consensus or beat/miss in Tributary", "").replace("No beat or miss: Tributary holds no estimates.", "");
  expect(said).not.toMatch(/\bbeat|\bmiss|consensus/i);
});

it("switches quarters, and says what a quarter lacks instead of guessing", async () => {
  const q = earnings!.quarters[1]!;
  earnings!.quarters.push({ ...q, period_end: "2026-03-31", release: null, headline: null, outlook: [], adjustments: [],
    report: { accession_no: "0001628280-26-026556", form: "10-Q", filed: "2026-04-22" } });
  earnings!.quarters.unshift({ ...q, period_end: "2026-12-31", estimated: true, report: null, outlook: [], adjustments: [] });
  await render(view());
  // the newest quarter with a release opens: here the expected one, whose 10-K isn't filed yet
  expect($(".release-card .sub")?.textContent).toBe("Quarter ended Dec 31, 2026 (expected)");
  expect(container.textContent).toContain("isn't filed yet, and the figures come from it");
  expect(container.textContent).toContain("This release has no section headed Outlook or Guidance");
  expect(container.textContent).toContain("This release has no reconciliation table and no note with a dollar amount");
  await act(async () => $$(".earnings-pick .seg button").find((b) => b.textContent?.startsWith("Mar 31, 2026"))!.click());
  expect($(".release-card")?.textContent).toContain("No earnings release (an 8-K with Item 2.02) for this quarter");
  expect(container.textContent).toContain("No release, so no outlook for this quarter.");
});

it("an older Query API without the route, and a plan without filings, each say so", async () => {
  earnings = null;
  await render(view());
  expect(container.textContent).toContain("Earnings releases aren't available here yet");
  act(() => root.unmount());
  await render(view({ canReadFilings: false }));
  expect(container.textContent).toContain("The earnings release is filing text, which your access doesn't include for Vertiv.");
  expect(vi.mocked(fetch).mock.calls.filter(([u]) => String(u).includes("/earnings"))).toHaveLength(1);
});

it("lists the 8-Ks since the last report and flags the balances an acquisition after it may have changed", async () => {
  await render(<EventsCard token="t" companyId={VRT} name="Vertiv" />);
  expect($(".events-card .sub")?.textContent).toBe("8-Ks filed after the quarter the latest 10-Q covers (ended Sep 30, 2026; filed Oct 28, 2026)");
  const box = $(".stale-box")!;
  expect(box.querySelector("b")?.textContent).toBe("⚑ Figures as of Sep 30, 2026 that a later 8-K may have changed");
  expect([...box.querySelectorAll("li")].map((l) => l.textContent)).toEqual([
    "Cash: before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K",
    "Debt: before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K",
    "Net debt: before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K",
    "Total assets: before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K"]);
  expect(box.textContent).toContain("no pro forma");
  const rows = $$("li.event");
  expect(rows.map((r) => r.querySelector(".event-date")?.textContent)).toEqual(["Oct 28, 2026", "Oct 27, 2026", "Oct 26, 2026"]);
  const uig = rows[1]!;
  expect([...uig.querySelectorAll(".event-head .tag")].map((t) => t.textContent)).toEqual([
    "8-K", "Acquisition or disposition", "Regulation FD disclosure", "may change cash, debt, net debt, total assets"]);
  expect(uig.querySelector(".event-excerpt")?.textContent).toMatch(/^Acquisition or disposition: “/);
  const read = [...uig.querySelectorAll("a")].find((a) => a.textContent === "Read the 8-K")!.getAttribute("href")!;
  expect(read).toMatch(new RegExp(`^#/filing/${VRT}/${UIG}/2\\.01\\?at=\\d+$`));
  expect([...uig.querySelectorAll("a")].find((a) => a.textContent === "Read the press release")?.getAttribute("href"))
    .toBe(`#/filing/${VRT}/${UIG}/EX-99.1`);
  expect(uig.querySelector("a.edgar-link")?.getAttribute("href")).toMatch(/^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/1674101\//);
  expect(uig.querySelector(".event-announced")).toBeNull(); // its 2.01 flags it
  // F1b: the Oct 26 8-K is Item 7.01 only; its press release says the KES acquisition is complete
  const kes = rows[2]!;
  expect([...kes.querySelectorAll(".event-head .tag")].map((t) => t.textContent)).toEqual([
    "8-K", "Regulation FD disclosure", "may change cash, debt, net debt, total assets"]);
  const note = kes.querySelector(".event-announced")!;
  expect(note.textContent).toMatch(/^⚑ a completed acquisition or disposition, read from its Item 7\.01 announcement \(it has no Item 2\.01\): the words “Completes Acquisition” in /);
  expect(note.querySelector("mark")?.textContent).toBe("“Completes Acquisition”");
  expect(note.textContent).toContain("“Vertiv Completes Acquisition of King Environmental Services Ltd.");
  expect(note.querySelector("a")?.getAttribute("href")).toMatch(new RegExp(`^#/filing/${VRT}/0001628280-26-067127/EX-99\\.1\\?at=\\d+$`));
  // the earnings release beside it (2.02 + 7.01) is never read
  expect(rows[0]!.querySelector(".event-announced")).toBeNull();
});

it("flags Vertiv's Sep 30 cash and net debt on the Overview's key stats, with the event, without a new figure", async () => {
  const row = VRT_ROW as unknown as ScreenerRow;
  await render(<Overview token="t" companyId={VRT} name="Vertiv" row={row} access={["financials", "filings"]} />);
  const stat = (label: string) => $$(".kv.stats > div").find((d) => d.querySelector(".k")?.textContent === label)!;
  expect(stat("Net debt").querySelector(".stale-flag")?.textContent).toBe("⚑ before the Oct 27, 2026 8-K (Acquisition or disposition) and 1 more 8-K");
  expect(stat("Net debt").getAttribute("title")).toContain("Net debt at Sep 30, 2026 is from before the Oct 27, 2026 8-K");
  expect(stat("Net debt").querySelector(".v")?.textContent).toBe("-$783.20M"); // the figure itself is unchanged
  expect(stat("Cash").querySelector(".stale-flag")).not.toBeNull();
  expect(stat("Revenue").querySelector(".stale-flag")).toBeNull();
  expect(stat("Debt / equity").querySelector(".stale-flag")).toBeNull(); // 2.01 doesn't list it
  expect($(".events-card h2")?.textContent).toBe("Since the last report");
  // one request for the card and the flags
  expect(vi.mocked(fetch).mock.calls.filter(([u]) => String(u).includes("/events"))).toHaveLength(1);
});
