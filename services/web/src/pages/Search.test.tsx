import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ACC, NVDA } from "../fixtures";
import { groupHits, parseSearch, searchQuery, type SearchState } from "../search/state";
import type { Company, SectionHit } from "../types";
import { Search } from "./Search";

const MSFT = "0000789019";
const K_ACC = "0001045810-26-000021";
const hit = (company_id: string, name: string, accession_no: string, section_key: string, form = "10-Q"): SectionHit => ({
  company_id, name, section_key, title: section_key === "1A" || section_key === "II-1A" ? "Risk Factors" : "MD&A", rank: 0.5,
  snippet: "new «export» «controls» on data center products", passages_matched: 2,
  citation: { accession_no, form, filed: "2026-08-26", period_of_report: null, section_key, document_url: null, filing_url: null, char_start: 4100, char_end: 4200 },
  freshness: { filed: "2026-08-26", ingested_at: "" },
});
const HITS = [
  hit(NVDA, "Nvidia", ACC, "II-1A"),
  hit(MSFT, "Microsoft", "0000789019-26-000010", "1A", "10-K"),
  hit(NVDA, "Nvidia", K_ACC, "1A", "10-K"),
  hit(NVDA, "Nvidia", ACC, "I-2"),
];
const COMPANIES: Company[] = [
  { company_id: NVDA, name: "Nvidia", tiers: ["financials", "filings"] },
  { company_id: MSFT, name: "Microsoft", tiers: ["financials", "filings"] },
  { company_id: "0000320193", name: "Apple", tiers: ["financials"] },
];

const OK = () => Response.json({ query: "", as_of: "2026-10-21T10:00:00Z", limit: 20, offset: 0, has_more: true, results: HITS });
let reply = OK;

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async () => reply()));
  reply = OK;
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

async function render(search?: SearchState, companies = COMPANIES) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Search token="t" search={search} companies={companies} />));
  for (let i = 0; i < 3; i++) await act(async () => {});
}

const calls = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));

it("says how far back the ranking goes when the API searched a recent window", async () => {
  reply = () => Response.json({ query: "", as_of: "2026-10-21T10:00:00Z", limit: 20, offset: 0, has_more: false, results: HITS, searched_from: "2026-09-21" });
  await render({ q: "supply chain", page: 0 });
  expect(container.querySelector(".searched-from")?.textContent).toContain("since");
  expect(container.querySelector(".searched-from")?.textContent).toContain("Pick a company or");
});

it("says nothing about a window when the whole index was searched", async () => {
  await render({ q: "supply chain", page: 0 });
  expect(container.querySelector(".searched-from")).toBeNull();
});

it("groups ranked passages by company and filing, each opening the reader at the passage", async () => {
  await render({ q: "export controls", form: "10-Q", from: "2026-01-01", page: 1 });
  const u = calls()[0]!;
  expect(u.pathname).toBe("/api/query/v1/search");
  expect(Object.fromEntries(u.searchParams)).toEqual({ q: "export controls", form: "10-Q", from: "2026-01-01", limit: "20", offset: "20" });
  expect([...container.querySelectorAll(".result-co h2")].map((h) => h.textContent)).toEqual(["Nvidia", "Microsoft"]);
  const nvda = container.querySelector(".result-co")!;
  const filings = [...nvda.querySelectorAll(".result-filing")];
  expect(filings).toHaveLength(2);
  expect([...filings[0]!.querySelectorAll("li b")].map((b) => b.textContent)).toEqual(["II-1A", "I-2"]);
  expect(filings[0]!.querySelector("li a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${ACC}/II-1A?at=4100&q=export+controls`);
  expect([...filings[0]!.querySelectorAll("li mark")].slice(0, 2).map((m) => m.textContent)).toEqual(["export", "controls"]);
  expect(container.textContent).toContain("2 companies on your plan");
  expect(container.textContent).toContain("21–24");
  await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent === "Next")!.click());
  expect(window.location.hash).toBe("#/search?q=export+controls&form=10-Q&from=2026-01-01&page=2");
});

it("submits the form into the address and offers only companies with filings access", async () => {
  await render();
  expect(calls()).toHaveLength(0);
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label^="Company: All you can read"]')!.click());
  expect([...container.querySelectorAll('[aria-label="Company results"] [role="option"]')].map((o) => o.textContent))
    .toEqual(["Nvidia", "Microsoft"]);
  await act(async () => container.querySelector<HTMLInputElement>('input[aria-label="Company"]')!.blur());
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Search words"]')!;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, '  "supply chain" -china ');
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="Form"]')!;
  await act(async () => {
    select.value = "8-K";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  // the form filter applies at once with the words typed so far: no second step
  expect(parseSearch(window.location.hash.split("?")[1]!)).toEqual({ q: '"supply chain" -china', form: "8-K" });
});

const pickDate = async (label: string, value: string) => {
  const el = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

it("applies a filter to the search on screen at once, and holds a half-typed year", async () => {
  await render({ q: "tariffs" });
  await pickDate("Filed from", "0002-01-01");
  expect(window.location.hash).toBe("");
  await pickDate("Filed from", "2024-01-01");
  expect(parseSearch(window.location.hash.split("?")[1]!)).toEqual({ q: "tariffs", from: "2024-01-01" });
});

it("refuses inverted dates, says so and offers to swap them, without blaming the words", async () => {
  await render({ q: "tariffs", from: "2026-01-01", to: "2020-01-01" });
  expect(calls()).toHaveLength(0);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("“Filed from” is after “to”");
  expect(container.textContent).not.toContain("different words");
  await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent === "Swap the dates")!.click());
  expect(parseSearch(window.location.hash.split("?")[1]!)).toEqual({ q: "tariffs", from: "2020-01-01", to: "2026-01-01" });
});

it("with no match under filters, offers to clear them instead of blaming the words", async () => {
  reply = () => Response.json({ query: "zzz", as_of: "", limit: 20, offset: 0, has_more: false, results: [] });
  await render({ q: "zzz", form: "10-K" });
  expect(container.textContent).toContain("No passages match “zzz” with these filters");
  expect(container.textContent).not.toContain("different words");
});

it("the company filter searches every company with the catalog open, and names a linked one", async () => {
  vi.mocked(fetch).mockImplementation(async (u: string | URL | Request) => {
    const url = new URL(String(u), "http://x");
    return url.pathname.endsWith("/v1/companies/search")
      ? Response.json({ results: [{ company_id: "0001318605", name: "Tesla, Inc.", matched_on: "ticker", matched_alias: "TSLA", is_current: true, tickers: ["TSLA"], access: ["financials", "filings"] }] })
      : reply();
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Search token="t" search={{ q: "x", company: "0001318605" }} companies={[]} open />));
  for (let i = 0; i < 3; i++) await act(async () => {});
  expect(container.querySelector('button[aria-label^="Company: "]')?.textContent).toContain("Tesla");
  expect(container.querySelector('button[aria-label^="Company: "]')?.textContent).toContain("TSLA");
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="Clear Company"]')!.click());
  expect(parseSearch(window.location.hash.split("?")[1]!)).toEqual({ q: "x" });
});

it("says why there are no results: too broad, no access, nothing matched", async () => {
  reply = () => Response.json({ detail: "search too broad: add a company, form, section or date filter, or more specific words" }, { status: 422 });
  await render({ q: "the" });
  expect(container.querySelector(".error")?.textContent).toContain("search too broad");
  act(() => root.unmount());
  reply = () => Response.json({ query: "zzz", as_of: "", limit: 20, offset: 0, has_more: false, results: [] });
  await render({ q: "zzz" });
  expect(container.textContent).toContain("No passages match “zzz”");
  act(() => root.unmount());
  vi.mocked(fetch).mockClear();
  await render({ q: "chips" }, [COMPANIES[2]!]);
  expect(container.textContent).toContain("Searching filing text needs filings access");
  expect(container.textContent).toContain("none on your plan");
  expect(calls()).toHaveLength(0);
});

it("keeps only valid search state in the address", () => {
  const s: SearchState = { q: "a & b?", form: "10-K", from: "2025-01-01", to: "2026-01-01", company: NVDA, page: 3 };
  expect(parseSearch(searchQuery(s))).toEqual(s);
  expect(parseSearch("q=x&form=S-1&from=yesterday&company=NVDA&page=-1")).toEqual({ q: "x" });
  expect(parseSearch("q=%20%20")).toBeNull();
  expect(parseSearch("page=99&q=x")).toEqual({ q: "x" });
  expect(groupHits(HITS).map((c) => [c.name, c.filings.map((f) => f.hits.length)])).toEqual([
    ["Nvidia", [2, 1]],
    ["Microsoft", [1]],
  ]);
});
