import { describe, expect, it } from "vitest";

import type { DataNote } from "../types";
import { edgarUrl, explain, groupNotes, rankOf, summaryText } from "./notes";

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

describe("filings the XBRL data doesn't have yet", () => {
  const NOW = Date.parse("2026-11-06T12:00:00Z");
  const behind = (filings: { form: string; filed: string }[]) => note("companyfacts_behind",
    { missing_filings: filings.length, missing_filed: filings[0]!.filed, facts_latest_filed: "2026-05-01" },
    { filings: filings.map((f) => ({ role: "missing" as const, accession_no: "x", filing_url: null, ...f })) });

  it("doesn't date two filings by the newest one", () => {
    const { text, title } = explain(behind([{ form: "10-Q", filed: "2026-11-06" }, { form: "10-Q", filed: "2026-07-29" }]), NOW);
    expect(title).toBe("Newer filings aren't in the figures yet");
    expect(text).toContain("2 10-K/10-Q filings, the newest filed Nov 6, 2026 and the oldest Jul 29, 2026,");
    expect(text).not.toContain("2 10-K/10-Q filings filed");
  });

  it("promises the usual day or two only while that's true", () => {
    const fresh = explain(behind([{ form: "10-Q", filed: "2026-11-05" }]), NOW).text;
    expect(fresh).toContain("a 10-Q filed Nov 5, 2026");
    expect(fresh).toContain("usually within a day or two");
    const late = explain(behind([{ form: "10-Q", filed: "2026-07-29" }]), NOW).text;
    expect(late).toContain("It has been waiting 100 days, longer than the usual day or two");
    expect(late).not.toContain("usually within");
    const lateOldest = explain(behind([{ form: "10-Q", filed: "2026-11-06" }, { form: "10-Q", filed: "2026-07-29" }]), NOW).text;
    expect(lateOldest).toContain("The oldest has been waiting 100 days");
  });
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
  expect(behind.text).toContain("EDGAR has 2 10-K/10-Q filings, the newest filed Aug 27, 2026");
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

it("groups run newest first, by their newest note, drop when empty, and derived quarters stay last", () => {
  const groups = groupNotes([
    note("derived_quarters", {}, { date: "2026-09-30" }),
    note("restatement", {}, { date: "2021-11-02" }),
    note("outlier", {}, { date: "2022-12-31" }),
    note("mystery", {}, { date: "2019-01-01" }),
    note("companyfacts_behind", {}, { date: "2026-11-03" }),
    note("stock_split", {}, { date: "2025-05-28" }),
  ]);
  expect(groups.map((g) => [g.key, g.notes.map((n) => n.kind)])).toEqual([
    ["freshness", ["companyfacts_behind"]],
    ["splits", ["stock_split"]],
    ["values", ["outlier"]],
    ["restated", ["restatement"]],
    ["other", ["mystery"]],
    ["derived", ["derived_quarters"]],
  ]);
});

describe("what a note can honestly say", () => {
  it("a stock split reads as a split, not a restatement", () => {
    const e = explain(note("stock_split", { split: "10-for-1", periods: 4, first_period_end: "2023-07-30", last_period_end: "2024-04-28" }, { metric: null }));
    expect(e.title).toBe("10-for-1 stock split");
    expect(e.text).toContain("4 periods from Jul 30, 2023 to Apr 28, 2024");
    expect(e.text).toContain("isn't a correction");
    expect(explain(note("stock_split", { split: "1-for-6", reverse: true, periods: 1 }, { metric: null })).title).toBe("1-for-6 reverse stock split");
  });

  it("a clean factor without the other side may be a split, and the note stays a restatement", () => {
    const e = explain(note("restatement", { change: -0.9, may_be_split: "10-for-1" }, { metric: "eps_diluted" }));
    expect(e.title).toBe("EPS (diluted) restated");
    expect(e.text).toContain("−90%");
    expect(e.text).toContain("may be a split");
  });

  it("a factor of a thousand is a unit, and a change of thousands of percent isn't a correction", () => {
    const unit = explain(note("restatement", { change: 999, unit_change: true }, { metric: "weighted_shares_basic" }));
    expect(unit.title).toContain("unit changed");
    const shell = explain(note("restatement", { change: -141.96 }, { metric: "stockholders_equity" }));
    expect(shell.title).toBe("Stockholders equity restated");
    expect(shell.text).toContain("−14,196%");
    expect(shell.text).toContain("describe different things");
  });

  it("a real restatement stays what it is", () => {
    const e = explain(note("restatement", { change: 0.061 }, { metric: "net_income" }));
    expect(e.text).toContain("+6.1%");
    expect(e.text).toContain("growth rates against the original would differ");
  });

  it("a seasonal peak isn't called an error, and a one-off isn't called one for sure", () => {
    const seasonal = explain(note("outlier", { vs_neighbours: 12.1, seasonal: true }, { metric: "operating_cash_flow" }));
    expect(seasonal.title).toBe("Operating cash flow: seasonal peak");
    expect(seasonal.text).not.toContain("scale error");
    const spike = explain(note("outlier", { vs_neighbours: 17 }, { metric: "cash" }));
    expect(spike.title).toBe("Cash: one-quarter spike");
    expect(spike.text).toContain("one-off event, or a scale error");
  });

  it("segment operating income above the total says what usually explains it", () => {
    const e = explain(note("segment_sum_business_segment", { axis: "business_segment", members: 2, deviation: 0.0449 }, { metric: "operating_income" }));
    expect(e.text).toContain("sum to 4.5% more");
    expect(e.text).toContain("unallocated corporate costs");
  });
});

it("links only to EDGAR", () => {
  const f = { role: "source" as const, accession_no: "a", form: "10-Q", filed: null };
  expect(edgarUrl({ ...f, filing_url: "https://www.sec.gov/Archives/edgar/data/1/2/" })).toBe("https://www.sec.gov/Archives/edgar/data/1/2/");
  expect(edgarUrl({ ...f, filing_url: "javascript:alert(1)" })).toBeNull();
  expect(edgarUrl({ ...f, filing_url: null })).toBeNull();
});

describe("what gold leaves out reads as what it is", () => {
  it("names a period that hadn't ended when the filing was filed, not a gap to 2032", () => {
    const e = explain(note("mistagged_fact", { rule: "period_after_filing", form: "10-Q", filed: "2024-05-09" }, { metric: "income_tax", period_end: "2033-03-31" }));
    expect(e.title).toBe("Income tax: left out, a period after its filing");
    expect(e.text).toContain("10-Q filed May 9, 2024");
    expect(e.text).toContain("hadn't ended when it was filed");
  });

  it("names a debt figure above total liabilities as a scale slip", () => {
    const e = explain(note("mistagged_fact", { rule: "debt_above_liabilities", form: "10-K", filed: "2020-03-11" }, { metric: "long_term_debt", period_end: "2019-12-31" }));
    expect(e.title).toBe("Long-term debt: left out, larger than total liabilities");
    expect(e.text).toContain("scale slip");
  });

  it("says why net debt is left out, with or without an ended investments series", () => {
    const ended = explain(note("net_debt_gap", { investments_end: "2025-10-26" }, { metric: "net_debt", period_end: "2026-07-26" }));
    expect(ended.title).toBe("Net debt: left out");
    expect(ended.text).toContain("last tagged at Oct 26, 2025");
    const part = explain(note("net_debt_gap", {}, { metric: "net_debt", period_end: "2020-12-31" }));
    expect(part.text).toContain("interest expense is more than half");
  });

  it("files them with gaps and with values to double-check", () => {
    const groups = groupNotes([note("net_debt_gap", {}), note("mistagged_fact", {})]);
    expect(groups.map((g) => [g.key, g.notes.length]).sort()).toEqual([["periods", 1], ["values", 1]]);
  });
});

it("says when the same quarter is missing year after year", () => {
  const e = explain(note("series_gap", { missing_from: "2020-10-26", missing_to: "2021-01-31", repeated: 5 }, { metric: "capex" }));
  expect(e.title).toBe("Capex: the same quarter missing in 5 years");
  expect(e.text).toContain("the 10-K tags the year in a way the 10-Qs don't");
});

it("says a computed gross profit is revenue less cost", () => {
  const e = explain(note("derived_quarters", { quarters: 12, first_period_end: "2023-12-31", latest_period_end: "2026-09-30", computed: true }, { metric: "gross_profit" }));
  expect(e.title).toBe("Gross profit: revenue less cost of revenue");
  expect(e.text).toContain("the filing prints no gross profit");
});

describe("ranked and grouped notes (M2)", () => {
  it("says where else a grouped series was found", () => {
    const e = explain(note("segment_sum_product_service", { axis: "product_service", members: 6, deviation: 1, repeats: 24, first_period_end: "2021-03-31", last_period_end: "2026-06-30" }));
    expect(e.title).toBe("Revenue: products and services don't add up (24 periods)");
    expect(e.text).toContain("The same check found this in 24 periods, from Mar 31, 2021 to Jun 30, 2026; this is the newest.");
  });

  it("leaves a single finding's words alone", () => {
    expect(explain(note("outlier", { vs_neighbours: 12.5, repeats: 1 })).title).toBe("Revenue: one-quarter spike");
  });

  it("reads a note without a rank, from an older API, as a plain note", () => {
    expect(rankOf(note("outlier"))).toBe("note");
    expect(rankOf(note("outlier", {}, { rank: "minor" }))).toBe("minor");
  });

  it("puts the group with what needs attention first, whatever its date", () => {
    const groups = groupNotes([
      note("restatement", {}, { date: "2026-10-30", rank: "minor" }),
      note("impossible_value", { rule: "negative" }, { date: "2025-03-31", rank: "attention" }),
    ]);
    expect(groups.map((g) => g.key)).toEqual(["values", "restated"]);
  });

  it("summarises by rank, and says nothing from an older API", () => {
    expect(summaryText({ attention: 1, note: 3, minor: 40, findings: 312 })).toBe(
      "1 needs attention · 3 worth a look · 40 minor or old, from 312 check findings");
    expect(summaryText({ attention: 0, note: 0, minor: 2, findings: 5 })).toBe(
      "nothing needs attention · 2 minor or old, from 5 check findings");
    expect(summaryText(undefined)).toBeNull();
  });

  it("words a stock compensation benefit and debt parts in the wrong scale (050)", () => {
    const benefit = explain(note("sign_flip", { rule: "benefit" }, { metric: "stock_based_compensation", severity: "info" }));
    expect(benefit.title).toBe("Stock based compensation: a benefit this quarter");
    expect(benefit.text).toContain("isn't a slip");
    const parts = explain(note("mistagged_fact", { rule: "debt_parts_scale", form: "10-K", filed: "2020-03-11", factor: 1000 }, { metric: "long_term_debt_current", period_end: "2019-12-31" }));
    expect(parts.title).toBe("Debt: left out, parts in the wrong scale");
    expect(parts.text).toContain("add up to 1,000 times the total debt");
  });
});
