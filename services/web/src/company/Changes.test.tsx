import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { SectionChanges } from "../types";
import { Changes } from "./Filings";
import DECK_TARIFFS from "./fixtures/deck-changes-tariffs.json";
import VRT_PRIOR from "./fixtures/vrt-changes-prior.json";

// What changed (F3) against the branch's Query API answers over production's texts: Deckers'
// Q2 FY27 10-Q ranked and filtered to "tariffs", Vertiv's Q3 10-Q against the previous 10-Q
const DECK = "0000910521";
const ACC = "0001628280-26-068116";
let answer: (u: URL) => Response;

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  answer = () => Response.json(DECK_TARIFFS);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => answer(new URL(url, "http://x"))));
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function render(form = "10-Q") {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Changes token="t" companyId={DECK} accessionNo={ACC} name="Deckers" form={form} />));
  for (let i = 0; i < 4; i++) await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...container.querySelectorAll<HTMLElement>(sel)];
const asked = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x").searchParams);
const button = (label: string) => $$("button").find((b) => b.textContent === label)!;
const click = async (el: HTMLElement) => {
  await act(async () => el.click());
  for (let i = 0; i < 3; i++) await act(async () => {});
};

it("opens a 10-Q against the same quarter a year earlier, most material first, and says how it ranks", async () => {
  await render();
  const first = asked()[0]!;
  expect([first.get("basis"), first.get("view")]).toEqual(["year_ago", "ranked"]);
  expect(button("Same quarter a year earlier").getAttribute("aria-pressed")).toBe("true");
  expect(button("Most material first").getAttribute("aria-pressed")).toBe("true");
  expect($(".changes-basis")?.textContent).toBe("Compared with the 10-Q for the quarter ended Sep 30, 2025, filed Oct 31, 2025.");
  expect($(".changes-why")?.textContent).toMatch(/^Ranked by what changed: new or removed figures/);
  expect($(".changes-hidden")?.textContent).toBe("Not shown: 4 paragraphs only split, rejoined or page furniture, 4 table rows whose figures alone changed, 6 minor edits.");
  expect($(".changes-count")?.textContent).toBe("10 of 91 changes about “tariffs”");
  const items = $$(".diff p.new, .diff p.rewritten, .diff p.removed");
  expect(items).toHaveLength(10);
  // tester 2's refund passage: new, its period, why it ranks, the topic marked
  const refund = items[1]!;
  expect([...refund.querySelectorAll(".change-head .tag")].map((t) => t.textContent)).toEqual(["MD&A", "3 and 6 months"]);
  expect(refund.querySelector(".change-head b")?.textContent).toBe("New");
  expect(refund.querySelector(".change-why")?.textContent)
    .toBe("Why it ranks here: new passage · 5 new figures · new risk wording: tariff, refund · Results of operations");
  expect([...refund.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["tariffs", "tariff", "tariff", "tariff"]);
  expect(refund.querySelector(".change-links a")?.getAttribute("href")).toMatch(new RegExp(`^#/filing/${DECK}/${ACC}/I-2\\?at=\\d+$`));
});

it("asks the API again for each comparison, order, topic, new figures and section", async () => {
  await render();
  await click(button("Previous 10-Q"));
  expect(asked().at(-1)!.get("basis")).toBe("prior");
  await click(button("Reading order"));
  expect(asked().at(-1)!.get("view")).toBe("reading");
  const input = $("input[aria-label='Topic']") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "credit facility");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(button("Filter"));
  expect(asked().at(-1)!.get("q")).toBe("credit facility");
  await click($("label.changes-check input")!);
  expect(asked().at(-1)!.get("numbers")).toBe("new");
  const select = $("select[aria-label='Section']") as HTMLSelectElement;
  await act(async () => {
    select.value = "II-1A";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  for (let i = 0; i < 3; i++) await act(async () => {});
  expect(asked().at(-1)!.get("section")).toBe("II-1A");
  await click(button("Clear"));
  expect(asked().at(-1)!.get("q")).toBeNull();
});

it("falls back to the previous 10-Q when no 10-Q from a year earlier is indexed, and says so", async () => {
  answer = (u) => u.searchParams.get("basis") === "year_ago"
    ? Response.json({ ...DECK_TARIFFS, sections: [], note: "no comparable earlier filing", comparisons: DECK_TARIFFS.comparisons.filter((c) => c.basis === "prior") })
    : Response.json(VRT_PRIOR);
  await render();
  expect(asked().map((p) => p.get("basis"))).toEqual(["year_ago", "prior"]);
  expect($(".changes-basis")?.textContent).toBe(
    "Compared with the 10-Q for the quarter ended Jun 30, 2026, filed Jul 29, 2026: no 10-Q from a year earlier is indexed, so the previous one.");
  expect($(".changes-hidden")?.textContent).toContain("9 calendar rollovers (dates, “second” to “third” quarter)");
  // reading order and periods: Vertiv's third-quarter text against the second quarter's
  expect($$(".diff .tag.period").map((t) => t.textContent)).toContain("3 months");
});

it("an older Query API (no view in its answer) gets the plain list, with the comparison still choosable", async () => {
  const older: SectionChanges = { ...(VRT_PRIOR as SectionChanges) };
  delete older.view;
  older.sections = older.sections.map(({ material: _, ...s }) => ({
    ...s, changes: s.changes.map(({ kind, similarity, text, prior_text, words, citation, prior_citation }) => (
      { kind, similarity, text, prior_text, words, citation, prior_citation })),
  }));
  answer = () => Response.json(older);
  await render();
  expect($("input[aria-label='Topic']")).toBeNull();
  expect(button("Most material first")).toBeUndefined();
  expect(button("Previous 10-Q")).toBeDefined();
  expect($(".diff")?.textContent).toContain("unchanged or minor, hidden");
  expect($(".changes-why")).toBeNull();
});

it("a 10-K is compared with the previous 10-K, with no comparison to choose", async () => {
  await render("10-K");
  expect(asked()[0]!.get("basis")).toBe("prior");
  expect(button("Same quarter a year earlier")).toBeUndefined();
});
