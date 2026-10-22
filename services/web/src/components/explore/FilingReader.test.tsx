import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ACC, NVDA } from "../../fixtures";
import { FilingReader } from "./FilingReader";

const PRIOR = "0001045810-26-000050";
const TEXT = "Our business depends on demand.\n\nExport controls on our data center products may tighten.\n\nCompetition could reduce margins.";
const START = 5000; // where II-1A starts in the document

const FILING = {
  accession_no: ACC, form: "10-Q", filed: "2026-08-26", period_of_report: "2026-07-26",
  document_url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/q.htm",
  sections: [
    { section_key: "I-2", title: "MD&A", chars: 30000 },
    { section_key: "II-1A", title: "Risk Factors", chars: 12000 },
    { section_key: "EX-31.1", title: null, chars: 900 },
  ],
};
const indexRow = (accession_no: string, filed: string, over = {}) => ({
  accession_no, company_id: NVDA, name: "Nvidia", form: "10-Q", filed, accepted_at: null, period_of_report: null,
  filing_url: null, document_url: null, sections: 2, sections_available: true, ...over,
});
const cite = (section_key: string, char_start: number, accession_no = ACC) => ({
  accession_no, form: "10-Q", filed: "2026-08-26", period_of_report: null, section_key, document_url: null,
  filing_url: null, char_start, char_end: char_start + 40,
});
const CHANGES = {
  company_id: NVDA, accession_no: ACC, basis: "prior", as_of: "",
  sections: [{
    section_key: "II-1A", title: "Risk Factors", basis: "prior", stale: false,
    compared_to: { accession_no: PRIOR, form: "10-Q", filed: "2026-05-28", period_of_report: null, section_key: "II-1A", document_url: null, filing_url: null },
    summary: { paragraphs: 3, prior_paragraphs: 3, unchanged: 1, minor_edits: 0, numbers_only: 0, new: 1, removed: 1, rewritten: 1 },
    changes: [
      { kind: "new", similarity: null, text: "Export controls on our data center products may tighten.", prior_text: null, words: null, citation: cite("II-1A", START + 33), prior_citation: null },
      { kind: "rewritten", similarity: 0.7, text: "Competition could reduce margins.", prior_text: "Competition may reduce margins.",
        words: [["=", "Competition"], ["-", "may"], ["+", "could"], ["=", "reduce margins."]], citation: null, prior_citation: null },
      { kind: "removed", similarity: null, text: null, prior_text: "Pandemic supply issues.", words: null, citation: null, prior_citation: null },
    ],
    freshness: { filed: "", ingested_at: "", computed_at: "" },
  }],
};

let denied = false;

function respond(url: string): Response {
  const u = new URL(url, "http://x");
  const p = u.pathname;
  if (denied) return Response.json({ detail: "not permitted to read filings for this company" }, { status: 403 });
  if (p === `/api/query/v1/companies/${NVDA}/filings`) {
    return Response.json({ filings: u.searchParams.get("accession_no") === ACC ? [FILING] : [] });
  }
  if (p.startsWith(`/api/query/v1/companies/${NVDA}/sections/`)) {
    const key = decodeURIComponent(p.split("/").at(-1)!);
    return Response.json({ sections: [{ section_key: key, title: key === "II-1A" ? "Risk Factors" : "MD&A", text: TEXT, truncated: false, total_chars: TEXT.length,
      citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", document_url: null, section_key: key, char_start: START, char_end: START + TEXT.length } }] });
  }
  if (p.endsWith("/changes")) return Response.json(CHANGES);
  if (p.endsWith("/filing-index")) {
    return Response.json({ as_of: "", forms: ["10-Q"], from: null, to: "2026-08-26", limit: 5, offset: 0, has_more: false,
      filings: [indexRow(ACC, "2026-08-26"), indexRow(PRIOR, "2026-05-28")] });
  }
  if (p === "/api/query/v1/search") {
    return Response.json({ query: u.searchParams.get("q"), as_of: "", limit: 50, offset: 0, has_more: false, results: [
      { company_id: NVDA, name: "Nvidia", section_key: "II-1A", title: "Risk Factors", rank: 0.5, snippet: "«Export» «controls» on our data center", passages_matched: 2,
        citation: cite("II-1A", START + 33), freshness: { filed: "", ingested_at: "" } },
      // a same-day filing of the company: not this one, so not listed
      { company_id: NVDA, name: "Nvidia", section_key: "1A", title: "Risk Factors", rank: 0.4, snippet: "«export»", passages_matched: 1,
        citation: cite("1A", 10, "0001045810-26-000076"), freshness: { filed: "", ingested_at: "" } },
    ] });
  }
  return Response.json({ detail: "not found" }, { status: 404 });
}

let root: Root;
let container: HTMLDivElement;
const onAsk = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  denied = false;
  onAsk.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

async function render(sectionKey: string | null, extra: { at?: number; q?: string } = {}) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(<FilingReader token="t" companyId={NVDA} accessionNo={ACC} sectionKey={sectionKey} name="Nvidia" onAsk={onAsk} {...extra} />),
  );
  for (let i = 0; i < 4; i++) await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const paths = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));

it("shows the outline by part, the section, the prior filing's section and asks about it", async () => {
  await render("II-1A");
  expect([...container.querySelectorAll(".toc .grp-h")].map((e) => e.textContent)).toEqual(["Part I", "Part II", "Exhibits"]);
  expect($('.toc a[aria-current="page"]')?.textContent).toContain("Risk Factors");
  expect($(".section-text h2")?.textContent).toBe("II-1A · Risk Factors");
  expect(container.querySelectorAll(".section-text p")).toHaveLength(3);
  // the outline is this one filing, whatever its age
  expect(paths().find((u) => u.pathname.endsWith("/filings"))?.searchParams.get("accession_no")).toBe(ACC);
  const prior = [...container.querySelectorAll("a")].find((a) => a.textContent?.startsWith("Same section in the prior 10-Q"));
  expect(prior?.getAttribute("href")).toBe(`#/filing/${NVDA}/${PRIOR}/II-1A`);
  const index = paths().find((u) => u.pathname.endsWith("/filing-index"))!;
  expect(index.searchParams.getAll("form")).toEqual(["10-Q"]);
  expect(index.searchParams.get("to")).toBe("2026-08-26");
  expect($(".section-nav")?.textContent).toContain("I-2 · MD&A");
  await act(async () => [...container.querySelectorAll("button")].find((b) => b.textContent === "Ask about this section")!.click());
  expect(onAsk).toHaveBeenCalledWith("Summarize Risk Factors in this 10-Q.");
});

it("shows What changed side by side: prior on the left, this filing on the right", async () => {
  await render("II-1A");
  const toggle = [...container.querySelectorAll("button")].find((b) => b.textContent === "What changed (3)")!;
  await act(async () => toggle.click());
  const rows = [...container.querySelectorAll(".sbs-r")];
  expect(rows.map((r) => r.className)).toEqual(["sbs-r new", "sbs-r rewritten", "sbs-r removed"]);
  expect(rows[0]!.querySelector(".old")?.textContent).toBe("not in the prior filing");
  expect(rows[1]!.querySelector(".old del")?.textContent).toBe("may ");
  expect(rows[1]!.querySelector(".old ins")).toBeNull();
  expect(rows[1]!.querySelector(".new ins")?.textContent).toBe("could ");
  expect(rows[2]!.querySelector(".old")?.textContent).toBe("Pandemic supply issues.");
  expect($(".sbs-h a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${PRIOR}/II-1A`);
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
});

it("opens at a cited passage with the search words marked, and lists this filing's matches", async () => {
  await render("II-1A", { at: START + 40, q: "export controls" });
  expect($("p.hit")?.textContent).toContain("Export controls on our data center");
  expect([...container.querySelectorAll(".section-text mark")].map((m) => m.textContent)).toEqual(["Export", "controls"]);
  expect(container.textContent).toContain("2 matches for “export controls”");
  const search = paths().find((u) => u.pathname === "/api/query/v1/search")!;
  expect(Object.fromEntries(search.searchParams)).toMatchObject({ q: "export controls", company: NVDA, form: "10-Q", from: "2026-08-26", to: "2026-08-26" });
  const hits = [...container.querySelectorAll(".search-hits li")];
  expect(hits).toHaveLength(1);
  expect(hits[0]!.querySelector("a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${ACC}/II-1A?at=${START + 33}&q=export+controls`);
  expect([...hits[0]!.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["Export", "controls"]);
  expect($('.toc a[aria-current="page"] .cnt')?.textContent).toBe("2");
});

it("searches from the box: the words go into the address", async () => {
  await render("II-1A");
  const input = container.querySelector<HTMLInputElement>('input[aria-label="Search this filing"]')!;
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(input, "margins");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => input.form!.requestSubmit());
  for (let i = 0; i < 3; i++) await act(async () => {});
  expect(window.location.hash).toBe(`#/filing/${NVDA}/${ACC}/II-1A?q=margins`);
  expect(paths().some((u) => u.pathname === "/api/query/v1/search" && u.searchParams.get("q") === "margins")).toBe(true);
});

it("falls back to the first section for an unknown key, and explains a denial", async () => {
  await render("7");
  expect(container.textContent).toContain("This filing has no section 7; showing I-2.");
  act(() => root.unmount());
  denied = true;
  vi.mocked(fetch).mockClear();
  await render(null);
  expect($(".locked")?.textContent).toContain("needs filings access");
  expect(paths().filter((u) => u.pathname.includes("/sections/"))).toHaveLength(0);
});
