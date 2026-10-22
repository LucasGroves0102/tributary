import { expect, it } from "vitest";

import type { DataNote } from "../types";
import { edgarUrl, explain, groupNotes } from "./notes";

const note = (kind: string, detail: Record<string, unknown> = {}, over: Partial<DataNote> = {}): DataNote => ({
  company_id: "0000320193", kind, severity: "warning", metric: "revenue", period_end: "2025-03-29", date: "2025-03-29",
  detail, checked_at: null, filings: [], ...over,
});

it.each([
  ["annual_sum", { deviation: -0.0421 }, "Revenue: quarters don't add up to the year",
    "The four quarters of revenue in the fiscal year ending Mar 29, 2025 sum to 4.2% less than the annual figure in the 10-K"],
  ["overlapping_quarters", { overlap_days: 7 }, "Revenue: overlapping quarters", "starts 7 days before the previous one ends"],
  ["calendar_collision", { calendar_quarter: "2025Q1", period_ends: ["2025-01-04", "2025-03-29"] },
    "Revenue: two quarters in one calendar quarter", "Fiscal quarters ending Jan 4, 2025 and Mar 29, 2025 both fall in calendar 2025Q1"],
  ["impossible_value", { rule: "negative" }, "Revenue is negative", "below zero"],
  ["impossible_value", { rule: "current_assets > total_assets" }, "Current assets exceeds total assets", "current assets is larger than total assets"],
  ["sign_flip", {}, "Revenue: sign flips for one quarter", "opposite sign"],
  ["outlier", { vs_neighbours: 12.5 }, "Revenue: one-quarter spike", "12.5× the size of the quarters around it"],
  ["segment_sum_geography", { axis: "geography", members: 4, deviation: 0.031 }, "Revenue: geographic areas don't add up",
    "4 geographic areas sum to 3.1% more than consolidated revenue"],
  ["something_new", {}, "Revenue: something new", "A data check flagged revenue"],
])("explains %s", (kind, detail, title, text) => {
  const e = explain(note(kind, detail));
  expect(e.title).toBe(title);
  expect(e.text).toContain(text);
});

it("explains restatements with both filings and a signed change", () => {
  const e = explain(note("restatement", { original_filed: "2025-05-01", restated_filed: "2026-02-01", change: -0.2 }, {
    metric: "cash",
    filings: [
      { role: "original", accession_no: "a", form: "10-Q", filed: "2025-05-01", filing_url: null },
      { role: "restated", accession_no: "b", form: "10-K/A", filed: "2026-02-01", filing_url: null },
    ],
  }));
  expect(e.title).toBe("Cash restated");
  expect(e.text).toBe(
    "Cash for the period ending Mar 29, 2025 was first reported in the 10-Q filed May 1, 2025 and changed by −20% in the 10-K/A filed Feb 1, 2026. " +
      "Tributary shows the latest figure; growth rates against the original would differ.",
  );
});

it("explains several missing filings and derived quarters", () => {
  const behind = explain(note("companyfacts_behind", { missing_filings: 2, missing_filed: "2026-08-27", facts_latest_filed: "2026-05-28" }, { metric: null }));
  expect(behind.text).toContain("EDGAR has 2 10-K/10-Q filings filed Aug 27, 2026");
  expect(behind.text).toContain("end with the filing of May 28, 2026");
  const derived = explain(note("derived_quarters", { quarters: 1, first_period_end: "2025-12-27", latest_period_end: "2025-12-27" }));
  expect(derived.title).toBe("Revenue: 1 derived quarter");
  expect(derived.text).toContain("For 1 quarter between Dec 27, 2025 and Dec 27, 2025");
});

it("never trusts detail types blindly", () => {
  const e = explain(note("outlier", { vs_neighbours: "<script>" }));
  expect(e.text).toContain("many times the size");
  expect(explain(note("series_gap", {})).text).toContain("from an unknown date to an unknown date");
});

it("groups notes in a fixed order and drops empty groups", () => {
  const groups = groupNotes([note("derived_quarters"), note("restatement"), note("outlier"), note("mystery"), note("companyfacts_behind")]);
  expect(groups.map((g) => [g.key, g.notes.map((n) => n.kind)])).toEqual([
    ["freshness", ["companyfacts_behind"]],
    ["restated", ["restatement"]],
    ["values", ["outlier"]],
    ["derived", ["derived_quarters"]],
    ["other", ["mystery"]],
  ]);
});

it("links only to EDGAR", () => {
  const f = { role: "source" as const, accession_no: "a", form: "10-Q", filed: null };
  expect(edgarUrl({ ...f, filing_url: "https://www.sec.gov/Archives/edgar/data/1/2/" })).toBe("https://www.sec.gov/Archives/edgar/data/1/2/");
  expect(edgarUrl({ ...f, filing_url: "javascript:alert(1)" })).toBeNull();
  expect(edgarUrl({ ...f, filing_url: null })).toBeNull();
});
