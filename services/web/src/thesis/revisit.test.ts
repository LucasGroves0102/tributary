import { describe, expect, it } from "vitest";

import type { CheckClaim, CheckModel, ThesisCheckResult } from "../types";
import { diffChecks, flagWords, isQuiet, newerFiling, quoteKey } from "./revisit";

const NVDA = "0001045810";
const OLD_Q = { accession_no: "0001045810-26-000021", form: "10-Q", filed: "2026-08-27" };
const NEW_Q = { accession_no: "0001045810-26-000030", form: "10-Q", filed: "2026-11-20" };

describe("newerFiling", () => {
  const recorded = { created_at: "2026-11-02T09:00:00Z", checked_at: "2026-11-02T09:00:00+00:00", filings: [{ company_id: NVDA, latest: OLD_Q }] };
  const before = { created_at: "2026-11-02T09:00:00Z" }; // stored before Revisit

  it("compares with the newest 10-K/10-Q the check recorded", () => {
    expect(newerFiling(recorded, NVDA, NEW_Q)).toEqual(NEW_Q);
    expect(newerFiling(recorded, NVDA, OLD_Q)).toBeNull(); // the same filing
    // another filing the same day as the recorded one is newer
    expect(newerFiling(recorded, NVDA, { ...OLD_Q, accession_no: "0001045810-26-000022", form: "10-K" })).not.toBeNull();
    // an older one (the index is newest first, so only after a correction) is not
    expect(newerFiling(recorded, NVDA, { ...NEW_Q, filed: "2026-08-01" })).toBeNull();
  });

  it("falls back to the check's date without a record", () => {
    expect(newerFiling(before, NVDA, NEW_Q)).toEqual(NEW_Q);
    expect(newerFiling(before, NVDA, { ...NEW_Q, filed: "2026-11-02" })).toBeNull(); // same day
    expect(newerFiling({ ...before, checked_at: "2026-11-25T00:00:00Z" }, NVDA, NEW_Q)).toBeNull();
    // a record for another company, or latest unknown (index unreadable then): the date
    expect(newerFiling({ ...before, filings: [{ company_id: "0000789019", latest: NEW_Q }] }, NVDA, NEW_Q)).toEqual(NEW_Q);
    expect(newerFiling({ ...before, filings: [{ company_id: NVDA, latest: null }] }, NVDA, NEW_Q)).toEqual(NEW_Q);
  });

  it("only 10-Ks and 10-Qs count", () => {
    expect(newerFiling(before, NVDA, { ...NEW_Q, form: "8-K" })).toBeNull();
    expect(newerFiling(before, NVDA, { ...NEW_Q, form: "10-Q/A" })).toBeNull();
    expect(newerFiling(before, NVDA, null)).toBeNull();
  });
});

const claim = (quote: string, status: CheckClaim["status"]): CheckClaim => ({ quote, quote_found: true, status, explanation: "", citations: [], figures: [] });
const model = (itemId: string, name: string, years: [number, CheckModel["years"][0]["growth_flag"], CheckModel["years"][0]["margin_flag"]][]): CheckModel => ({
  ref: "model-1", item_id: itemId, name, company_id: NVDA,
  years: years.map(([fy, g, m]) => ({ fy, growth: 10, margin: 20, growth_vs_cagr: 0, growth_flag: g, margin_flag: m, commentary: "", citations: [], figures: [] })),
});
const result = (claims: CheckClaim[], assumptions: CheckModel[] = [], notMentioned = 0): ThesisCheckResult => ({
  version: 1,
  thesis: { item_id: "x", title: "t", company_id: NVDA, text_hash: "0".repeat(64), characters: 1, characters_checked: 1, truncated: false },
  summary: "", claims, assumptions,
  not_mentioned: Array.from({ length: notMentioned }, () => ({ point: "p", citations: [], figures: [] })),
  notes: [], citations: [],
  verification: { ok: true, figures: [], unverified_accessions: [], used_data: true, freshness: true },
  checked_at: "", model: "m", questions: 4, stop: "end_turn", rounds: 1, cost_usd: null,
});

describe("diffChecks", () => {
  it("matches claims by normalised quote and reports flips, new and removed", () => {
    const d = diffChecks(
      result([claim("Revenue grew 20%.", "supported"), claim("Margins “never” fall", "unverifiable"), claim("Old point.", "supported")]),
      result([claim("revenue  grew 20%", "contradicted"), claim('Margins "never" fall.', "unverifiable"), claim("A new point.", "supported")]),
    );
    expect(d.flipped).toEqual([{ quote: "revenue  grew 20%", from: "supported", to: "contradicted" }]);
    expect(d.unchanged).toBe(1);
    expect(d.added).toEqual([{ quote: "A new point.", status: "supported" }]);
    expect(d.removed).toEqual([{ quote: "Old point.", status: "supported" }]);
    expect(isQuiet(d)).toBe(false);
  });

  it("matches a repeated quote in order", () => {
    const d = diffChecks(
      result([claim("Same.", "supported"), claim("Same.", "contradicted")]),
      result([claim("Same.", "supported"), claim("Same.", "supported"), claim("Same.", "unverifiable")]),
    );
    expect(d.unchanged).toBe(1);
    expect(d.flipped).toEqual([{ quote: "Same.", from: "contradicted", to: "supported" }]);
    expect(d.added).toEqual([{ quote: "Same.", status: "unverifiable" }]);
    expect(d.removed).toEqual([]);
  });

  it("reports assumption flags that changed, by model and fiscal year", () => {
    const A = "11111111-1111-4111-8111-111111111111";
    const B = "22222222-2222-4222-8222-222222222222";
    const C = "33333333-3333-4333-8333-333333333333";
    const d = diffChecks(
      result([], [model(A, "Base", [[2026, "above", null], [2027, null, "below"]]), model(B, "Bear", [[2026, null, null]])]),
      result([], [model(A, "Base", [[2026, null, null], [2027, null, "below"], [2028, "above", null]]), model(C, "Bull", [[2026, "above", null]])]),
    );
    expect(d.flags).toEqual([{ itemId: A, model: "Base", fy: 2026, what: "growth", from: "above", to: null }]);
    expect(d.modelsAdded).toEqual(["Bull"]);
    expect(d.modelsRemoved).toEqual(["Bear"]);
  });

  it("is quiet when nothing changed; counts points not mentioned", () => {
    const d = diffChecks(result([claim("A.", "supported")], [], 2), result([claim("a", "supported")], [], 3));
    expect(isQuiet(d)).toBe(true);
    expect(d.notMentioned).toEqual({ before: 2, after: 3 });
  });

  it("tolerates results without assumptions (older shapes)", () => {
    const old = { ...result([claim("A.", "supported")]), assumptions: undefined } as unknown as ThesisCheckResult;
    expect(diffChecks(old, result([claim("A.", "supported")])).flags).toEqual([]);
  });
});

it("quoteKey drops case, spacing, curly quotes and edge punctuation", () => {
  expect(quoteKey("  “Revenue”  GREW—fast.  ")).toBe('revenue" grew-fast');
  expect(quoteKey("(Revenue grew.)")).toBe("revenue grew");
});

it("flagWords", () => {
  expect(flagWords("growth", "above")).toBe("above the CAGR");
  expect(flagWords("growth", null)).toBe("within 10 pts of the CAGR");
  expect(flagWords("margin", "below")).toBe("below the 5-year range");
  expect(flagWords("margin", null)).toBe("within the 5-year range");
});
