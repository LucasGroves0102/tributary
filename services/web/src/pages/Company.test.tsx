import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { excerpt } from "../company/Overview";
import { download } from "../download";
import { ACC, entry, fin, NVDA, nvdaRow, TESLA, TSLA } from "../fixtures";
import type { CompanyTab } from "../route";
import type { ScreenerRow } from "../types";
import { Company } from "./Company";

vi.mock("../download", () => ({ download: vi.fn() }));

const AAPL = "0000320193";
const K_ACC = "0001045810-26-000021";
const EIGHT_K = "0001045810-26-000080";
const tesla: ScreenerRow = { ...TESLA, filer_categories: null, state_of_incorporation: null };
const recapValue = (value: string, fiscal: string, over = {}) => ({
  value, period_start: null, period_end: "2026-07-26", fiscal_period: fiscal, calendar_period: null, value_source: "reported",
  citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", xbrl_tag: null, source_accessions: null, filing_url: null },
  freshness: { filed: "2026-08-26", ingested_at: "" }, ...over,
});
const RECAP = {
  accession_no: ACC, company_id: NVDA, form: "10-Q", filed: "2026-08-26", period_end: "2026-07-26", as_of: "", note: "",
  metrics: [{ metric: "revenue", span: "quarter", unit: "USD", current: recapValue("46743000000", "FY2027 Q2"),
    prior_quarter: recapValue("44062000000", "FY2027 Q1", { change: "2681000000", pct_change: "0.060846" }),
    year_ago: recapValue("30040000000", "FY2026 Q2", { change: "16703000000", pct_change: "0.556026" }) }],
  flags: [{ flag: "restructuring", section_key: "I-2", title: "Restructuring", matches: ["restructuring"],
    snippet: "a $184 million restructuring charge", citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", section_key: "I-2" } }],
};
const FILINGS = [
  { accession_no: EIGHT_K, form: "8-K", filed: "2026-08-26", period_of_report: "2026-08-26", document_url: null,
    sections: [{ section_key: "2.02", title: "Results", chars: 100 }, { section_key: "9.01", title: null, chars: 10 }, { section_key: "EX-99.1", title: null, chars: 9000 }] },
  { accession_no: ACC, form: "10-Q", filed: "2026-08-26", period_of_report: "2026-07-26", document_url: null,
    sections: [{ section_key: "I-2", title: "MD&A", chars: 30000 }] },
  { accession_no: K_ACC, form: "10-K", filed: "2026-02-26", period_of_report: "2026-01-25", document_url: null,
    sections: [{ section_key: "1", title: "Business", chars: 30000 }] },
];
const indexRow = (accession_no: string, form: string, filed: string, sections: number | null, over = {}) => ({
  accession_no, company_id: NVDA, name: "NVIDIA CORP", form, filed, accepted_at: null, period_of_report: null,
  filing_url: `https://www.sec.gov/Archives/edgar/data/1045810/${accession_no.replace(/-/g, "")}/`,
  document_url: null, sections, sections_available: !!sections, ...over,
});
const INDEX = [
  indexRow(EIGHT_K, "8-K", "2026-08-26", 3, { period_of_report: "2026-08-26" }),
  indexRow(ACC, "10-Q", "2026-08-26", 1, { period_of_report: "2026-07-26" }),
  indexRow("0001045810-26-000070", "4", "2026-08-20", 0),
  indexRow(K_ACC, "10-K", "2026-02-26", 1, { period_of_report: "2026-01-25" }),
];
let indexReadable = true;
const CHANGES = {
  company_id: NVDA, accession_no: ACC, basis: "prior", as_of: "",
  sections: [{
    section_key: "II-1A", title: "Risk Factors", basis: "prior", stale: false,
    compared_to: { accession_no: "0001045810-26-000050", form: "10-Q", filed: "2026-05-28", period_of_report: null, section_key: "II-1A", document_url: null, filing_url: null },
    summary: { paragraphs: 40, prior_paragraphs: 39, unchanged: 37, minor_edits: 1, numbers_only: 0, new: 1, removed: 0, rewritten: 1 },
    changes: [
      { kind: "new", similarity: null, text: "Export controls on our data center products may tighten.", prior_text: null, words: null,
        citation: { accession_no: ACC, form: "10-Q", filed: "2026-08-26", period_of_report: null, section_key: "II-1A", document_url: null, char_start: 0, char_end: 10 }, prior_citation: null },
      { kind: "rewritten", similarity: 0.7, text: "x", prior_text: "y", words: [["=", "Demand"], ["-", "may"], ["+", "could"], ["=", "fall."]],
        citation: null, prior_citation: null },
    ],
    freshness: { filed: "", ingested_at: "", computed_at: "" },
  }],
};

let rowFor: (id: string) => ScreenerRow = () => nvdaRow();

function respond(url: string): Response {
  const u = new URL(url, "http://x");
  const p = u.pathname;
  if (p === "/api/query/v1/screener") {
    return Response.json({ as_of: "", total: 1, limit: 1, offset: 0, sort: "name", columns: [], rows: [rowFor(u.searchParams.get("company_id")!)] });
  }
  if (p.endsWith("/expected-filings")) {
    return Response.json({ company_id: NVDA, as_of: "2026-10-21", method: "Estimate, not a filing.", expected: [entry().next_expected] });
  }
  const ungranted = p.includes(TSLA);
  if (p.endsWith("/recap")) return ungranted ? Response.json({ detail: "no" }, { status: 403 }) : Response.json(RECAP);
  if (p.includes("/sections/1")) {
    return Response.json({ sections: [{ section_key: "1", title: "Business", text: "NVIDIA pioneered accelerated computing. We sell GPUs.", truncated: true,
      total_chars: 30000, citation: { accession_no: K_ACC, form: "10-K", filed: "2026-02-26", document_url: null } }] });
  }
  if (p.endsWith("/filing-index")) {
    // public metadata: every user gets it; sections only where filings are readable
    const forms = u.searchParams.getAll("form");
    const offset = Number(u.searchParams.get("offset") ?? 0);
    const rows = INDEX.filter((f) => !forms.length || forms.includes(f.form))
      .map((f) => (indexReadable ? f : { ...f, sections: null, sections_available: false }));
    return Response.json({ as_of: "", forms, from: null, to: null, limit: 25, offset, has_more: offset === 0 && !forms.length, filings: rows });
  }
  if (ungranted) return Response.json({ detail: "not permitted" }, { status: 403 });
  if (p.endsWith("/financials")) {
    const view = u.searchParams.get("view");
    const metrics = u.searchParams.getAll("metrics");
    return Response.json({ as_of: "", rows: [
      fin("revenue", "2026-04-26", "44062000000", "FY2027 Q1"),
      fin("revenue", "2026-07-26", "46743000000", "FY2027 Q2"),
      fin("operating_margin", "2026-07-26", "0.6", "FY2027 Q2"),
      ...(view === "ttm" ? [fin("revenue", "2026-01-25", "130497000000", "FY2026 Q4")] : []),
    ].filter((r) => metrics.includes(r.metric)) });
  }
  if (p.endsWith("/segments")) return Response.json({ as_of: "", rows: [] });
  if (p.endsWith("/changes")) return Response.json(CHANGES);
  if (p.endsWith("/filings")) return Response.json({ filings: FILINGS });
  if (p.endsWith("/peers")) {
    return Response.json({ company_id: NVDA, as_of: "", columns: [], method: "Same 4-digit SIC first.",
      company: { ...nvdaRow(), revenue_band: ">=$100B" },
      peers: [
        { ...nvdaRow({ ...entry({ company_id: AAPL, name: "Apple Inc.", tickers: ["AAPL"] }), filer_categories: null, state_of_incorporation: null }),
          match: { sic_level: 3, same_filer_category: true, revenue_band: ">=$100B", same_revenue_band: true } },
        { ...tesla, match: { sic_level: 2, same_filer_category: false, revenue_band: null, same_revenue_band: false } },
      ] });
  }
  return Response.json({ detail: "not found" }, { status: 404 });
}

let root: Root;
let container: HTMLDivElement;
const onWatch = vi.fn(async () => {});
const onChart = vi.fn();
const onChartMany = vi.fn();
const onAsk = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => respond(url)));
  rowFor = () => nvdaRow({ access: ["filings", "financials"] });
  indexReadable = true;
  [onWatch, onChart, onChartMany, onAsk].forEach((f) => f.mockClear());
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.location.hash = "";
});

async function render(tab: CompanyTab = "overview", companyId = NVDA) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(<Company token="t" companyId={companyId} tab={tab} watched={false} onWatch={onWatch}
      onChart={onChart} onChartMany={onChartMany} onAsk={onAsk} />),
  );
  for (let i = 0; i < 3; i++) await act(async () => {});
}

const $ = (sel: string) => container.querySelector<HTMLElement>(sel);
const paths = () => vi.mocked(fetch).mock.calls.map(([u]) => new URL(String(u), "http://x"));
const button = (text: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === text)!;
const click = (el: Element | null | undefined) => act(async () => (el as HTMLElement).click());

it("heads the page with public metadata, the next expected filing and the tabs", async () => {
  await render();
  const screen = paths().find((u) => u.pathname === "/api/query/v1/screener")!;
  expect(screen.searchParams.getAll("company_id")).toEqual([NVDA]);
  expect($("h1")?.textContent).toBe("NVIDIA CORP");
  const head = $(".co-head")!.textContent!;
  expect(head).toContain("NVDA · Nasdaq");
  expect(head).toContain("Semiconductors & Related Devices (SIC 3674) · fiscal year ends January · large accelerated filer · incorporated in DE");
  expect(head).toContain("next 10-Q for the period ending Oct 25, 2026 expected ~Nov 19, 2026");
  expect([...container.querySelectorAll(".tabs a")].map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    ["Overview", `#/company/${NVDA}`], ["Statements", `#/company/${NVDA}/statements`], ["Segments", `#/company/${NVDA}/segments`],
    ["Ratios", `#/company/${NVDA}/ratios`], ["Filings", `#/company/${NVDA}/filings`], ["Peers", `#/company/${NVDA}/peers`],
  ]);
  expect($('.tabs a[aria-current="page"]')?.textContent).toBe("Overview");
  await click(button("+ Watch"));
  expect(onWatch).toHaveBeenCalledWith(true);
  await click(button("Compare"));
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}`);
  await click(button("Chart"));
  expect(onChart).toHaveBeenCalledWith("NVIDIA CORP");
});

it("overview: key stats with periods, the latest quarter recap with flags, and Item 1", async () => {
  await render();
  const stat = [...container.querySelectorAll(".kv > div")].find((d) => d.querySelector(".k")?.textContent === "Revenue")!;
  expect(stat.querySelector(".v")?.textContent).toBe("$165.22B");
  expect(stat.querySelector(".p")?.textContent).toBe("TTM to Jul 26, 2026");
  expect(paths().some((u) => u.pathname === `/api/query/v1/filings/${ACC}/recap`)).toBe(true);
  const recap = [...container.querySelectorAll("table")].find((t) => t.textContent?.includes("QoQ"))!;
  expect([...recap.querySelectorAll("th")].map((t) => t.textContent)).toEqual(["Metric", "FY2027 Q2", "FY2027 Q1", "QoQ", "FY2026 Q2", "YoY"]);
  expect(recap.querySelector("tbody tr")?.textContent).toBe("Revenue$46.74B$44.06B+6.1%$30.04B+55.6%");
  expect($(".flags")?.textContent).toContain("a $184 million restructuring charge");
  expect($(".flags a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${ACC}/I-2`);
  expect($(".excerpt")?.textContent).toBe("NVIDIA pioneered accelerated computing. We sell GPUs.");
  expect([...container.querySelectorAll("a")].find((a) => a.textContent === "Read Item 1")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${K_ACC}/1`);
});

it("an ungranted company shows its metadata and says what its plan doesn't cover", async () => {
  rowFor = () => tesla;
  await render("overview", TSLA);
  expect($("h1")?.textContent).toBe("Tesla, Inc.");
  expect($(".tier.none")?.textContent).toBe("metadata only");
  expect(container.textContent).toContain("Financials for Tesla, Inc. aren't included in your access");
  expect(container.textContent).toContain("The recap needs financials for Tesla, Inc.");
  expect(container.textContent).toContain("needs the pro plan and a grant for Tesla, Inc.");
  // no value or text requests for it at all
  expect(paths().some((u) => /recap|financials|sections/.test(u.pathname))).toBe(false);
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Chart")).toBe(false);
});

it("statements: quarterly, annual and TTM, each value cited, CSV export", async () => {
  await render("statements");
  const q = paths().find((u) => u.pathname.endsWith("/financials"))!;
  expect(q.searchParams.get("view")).toBe("quarter");
  expect(q.searchParams.getAll("metrics")).toContain("cost_of_revenue");
  const cell = [...container.querySelectorAll("td.n")].find((td) => td.textContent === "$46.74B")!;
  expect(cell.getAttribute("title")).toContain(`Source: 10-Q filed Aug 26, 2026, ${ACC}`);
  expect(cell.querySelector("a")?.getAttribute("href")).toBe("https://www.sec.gov/x/");
  await click(button("Annual"));
  for (let i = 0; i < 2; i++) await act(async () => {});
  const a = paths().filter((u) => u.pathname.endsWith("/financials")).at(-1)!;
  expect([a.searchParams.get("view"), a.searchParams.get("quarters")]).toEqual(["ttm", "40"]);
  expect([...container.querySelectorAll("thead th")].map((t) => t.firstChild?.textContent)).toEqual(["Income statement", "FY2026"]);
  await click(button("Export CSV"));
  expect(vi.mocked(download).mock.calls.at(-1)![0]).toBe("nvda-is-a.csv");
});

it("ratios read TTM values; segments say honestly when none are loaded", async () => {
  await render("ratios");
  const r = paths().find((u) => u.pathname.endsWith("/financials"))!;
  expect(r.searchParams.get("view")).toBe("ttm");
  expect(container.textContent).toContain("Operating margin");
  expect(container.textContent).toContain("60.0%");
  act(() => root.unmount());
  await render("segments");
  expect(container.textContent).toContain("No segment data for NVIDIA CORP yet");
});

it("filings: form filters, 8-K items, the reader, and what changed", async () => {
  await render("filings");
  const rows = () => [...container.querySelectorAll(".grid2 tbody tr")];
  expect(rows()).toHaveLength(4);
  expect(rows()[0]!.textContent).toContain("2.02 Results of operations");
  expect(rows()[0]!.textContent).not.toContain("9.01");
  expect(rows()[0]!.querySelector("a")?.getAttribute("href")).toBe(`#/filing/${NVDA}/${EIGHT_K}`);
  // a Form 4 Tributary hasn't parsed links to EDGAR instead
  expect(rows()[2]!.textContent).toContain("not parsed");
  expect(rows()[2]!.querySelector("a")?.getAttribute("href")).toMatch(/^https:\/\/www\.sec\.gov\//);
  // the form filter and paging run on the server
  await click(button("Older"));
  expect(paths().filter((u) => u.pathname.endsWith("/filing-index")).at(-1)!.searchParams.get("offset")).toBe("25");
  await click(button("10-K"));
  const last = paths().filter((u) => u.pathname.endsWith("/filing-index")).at(-1)!;
  expect(last.searchParams.getAll("form")).toEqual(["10-K", "10-K/A", "10-KT"]);
  expect(last.searchParams.get("offset")).toBeNull();
  expect(rows()).toHaveLength(1);
  await click(button("All"));
  await click(rows()[1]!.querySelector("button"));
  for (let i = 0; i < 2; i++) await act(async () => {});
  expect(paths().some((u) => u.pathname === `/api/query/v1/companies/${NVDA}/filings/${ACC}/changes`)).toBe(true);
  const diff = $(".diff")!;
  expect(diff.textContent).toContain("Risk Factors");
  expect(diff.textContent).toContain("1 new");
  expect(diff.textContent).toContain("38 unchanged or minor, hidden");
  expect(diff.querySelector("p.new")?.textContent).toContain("Export controls");
  expect(diff.querySelector("p.rewritten ins")?.textContent).toBe("could ");
  expect(diff.querySelector("p.rewritten del")?.textContent).toBe("may ");
});

it("filings: a free plan sees the whole index with EDGAR links, no reader", async () => {
  rowFor = () => nvdaRow({ access: ["financials"] });
  indexReadable = false;
  await render("filings");
  const rows = [...container.querySelectorAll(".grid2 tbody tr")];
  expect(rows).toHaveLength(4);
  expect(rows.map((r) => r.querySelector(".tag")?.textContent)).toEqual(["8-K", "10-Q", "4", "10-K"]);
  expect(container.querySelector('a[href^="#/filing/"]')).toBeNull();
  expect(rows[1]!.querySelector("a")?.textContent).toBe("EDGAR ↗");
  expect(rows[1]!.textContent).not.toContain("What changed");
  expect(rows[0]!.textContent).toContain("index only");
  expect(container.textContent).toContain("Reading parsed text needs filings access");
  // no parsed-filings request without filings access
  expect(paths().some((u) => u.pathname === `/api/query/v1/companies/${NVDA}/filings`)).toBe(false);
});

it("peers: suggested by SIC and size, into Compare and the chart builder", async () => {
  await render("peers");
  const body = [...container.querySelectorAll("tbody tr")];
  expect(body.map((r) => r.querySelector(".co b")?.textContent)).toEqual(["NVIDIA CORP", "Apple Inc.", "Tesla, Inc."]);
  expect(body[1]!.textContent).toContain("same 3-digit SIC group, same size band, same filer category");
  expect(body[2]!.className).toBe("masked");
  await click(button("Chart margins"));
  expect(onChartMany).toHaveBeenCalledWith([NVDA, AAPL], "operating_margin", "ttm", "Operating margin, TTM");
  await click(button("Open in Compare"));
  expect(window.location.hash).toBe(`#/compare?target=${NVDA}&peers=${AAPL},${TSLA}`);
});

it("cuts the business description at a sentence", () => {
  const text = "First sentence here. ".repeat(60);
  const out = excerpt(text, 100);
  expect(out.endsWith(". …")).toBe(true);
  expect(out.length).toBeLessThanOrEqual(104);
  expect(excerpt("Short.")).toBe("Short.");
});
