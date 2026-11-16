import { describe, expect, it } from "vitest";

import type { CoverageGap } from "../types";
import { coverageWords, GAP_KINDS, REASON_WORDS, shownGaps } from "./coverage";

const gap = (kind: string, cause: string, reason: CoverageGap["reason"], detail: CoverageGap["detail"] = {}): CoverageGap => ({
  kind, cause, reason, since: "2026-11-17", detail,
});

describe("coverage notes", () => {
  it.each([
    [gap("history_short", "revenue_tag", "not_in_sec_data", { revenue_from: "2017-03-31", figures_from: "2009-06-30" }),
      "Revenue starts in 2017", "Revenue before 2017 isn't in the SEC's structured data for Church & Dwight; the filings are. Its other figures go back to 2009."],
    [gap("no_figures", "bank_regulator", "not_in_sec_data"),
      "Reports to its bank regulator", "files its annual and quarterly reports with its banking regulator, not the SEC"],
    [gap("re_registration", "unlinked", "fixable", { moved_on: "2021-04-20", form: "8-K12B", figures_from: "2020-05-02" }),
      "Figures start at its 2021 re-registration", "re-registered with the SEC on Apr 20, 2021 (8-K12B)"],
    [gap("metric_missing", "not_read", "fixable", { metrics: [{ metric: "net_income", last: "2015-12-31" }] }),
      "Net income missing lately", "Net income after Dec 31, 2015 isn't in the figures Tributary reads"],
    [gap("figures_behind", "companyfacts_lacks", "fixable", { form: "20-F", period_end: "2025-12-31", filed: "2026-04-16", figures_to: "2024-12-31" }),
      "Figures behind its latest report", "Figures run to Dec 31, 2024; Church & Dwight's 20-F for the period ending Dec 31, 2025, filed Apr 16, 2026, isn't in them yet."],
    [gap("statements_exhibit", "ex13", "fixable"), "Statements in an exhibit", "(Exhibit 13)"],
    [gap("documents_missing", "partial", "fixable", { expected: 40, stored: 12 }), "Some documents not stored yet", "12 of Church & Dwight's 40"],
    [gap("no_figures", "filed_with_parent", "out_of_scope", { parent: "0000065984" }), "Figures filed with its parent", "part of Entergy's combined filing"],
  ])("words %#", (g, title, text) => {
    const w = coverageWords(g, "Church & Dwight", (id) => (id === "0000065984" ? "Entergy" : id));
    expect(w.title).toBe(title);
    expect(w.text).toContain(text);
  });

  it("names every kind and reason the sweep sends", () => {
    for (const [kind] of GAP_KINDS) {
      expect(coverageWords(gap(kind, "other", "fixable"), "Acme").title).not.toBe("A gap in the data");
    }
    expect(Object.keys(REASON_WORDS).sort()).toEqual(["fixable", "not_in_sec_data", "out_of_scope"]);
  });

  it("says a re-registration's short history once", () => {
    const gaps = [gap("re_registration", "unlinked", "fixable"), gap("history_short", "re_registration", "fixable"),
      gap("documents_missing", "none", "fixable")];
    expect(shownGaps(gaps).map((g) => g.kind)).toEqual(["re_registration", "documents_missing"]);
    expect(shownGaps([gap("history_short", "re_registration", "fixable")])).toHaveLength(1);
  });
});
