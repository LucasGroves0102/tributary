import { describe, expect, it } from "vitest";

import type { SectionChanges } from "../types";
import {
  comparedWith, comparisonLabel, countLine, defaultBasis, fallBack, hiddenLine, isMaterial, marks, periodLabel, placed,
  sectionShort,
} from "./changes";
import DECK_TARIFFS from "./fixtures/deck-changes-tariffs.json";
import VRT_PRIOR from "./fixtures/vrt-changes-prior.json";

// real answers of the branch's Query API over production's section texts (2026-11-09): Deckers'
// Q2 FY27 10-Q against Q2 FY26 filtered to "tariffs", ranked; Vertiv's Q3 2026 10-Q against Q2
const deck = DECK_TARIFFS as SectionChanges;
const vrt = VRT_PRIOR as SectionChanges;

describe("what changed, read for materiality", () => {
  it("opens a 10-Q against the same quarter a year earlier, a 10-K against the previous 10-K", () => {
    expect(defaultBasis("10-Q")).toBe("year_ago");
    expect(defaultBasis("10-Q/A")).toBe("year_ago");
    expect(defaultBasis("10-K")).toBe("prior");
    expect(defaultBasis(null)).toBe("prior");
    expect(comparisonLabel("year_ago", "10-Q")).toBe("Same quarter a year earlier");
    expect(comparisonLabel("prior", "10-Q")).toBe("Previous 10-Q");
    expect(comparedWith(deck.comparisons!.find((c) => c.basis === "year_ago")!))
      .toBe("the 10-Q for the quarter ended Sep 30, 2025, filed Oct 31, 2025");
  });

  it("falls back to the prior filing once, only when the year-ago one isn't there and wasn't asked for", () => {
    const none = { ...deck, comparisons: deck.comparisons!.filter((c) => c.basis === "prior"), sections: [] };
    expect(fallBack(none, "year_ago", false)).toBe(true);
    expect(fallBack(none, "year_ago", true)).toBe(false); // the user chose it: say so, don't switch
    expect(fallBack(deck, "year_ago", false)).toBe(false); // there is one
    expect(fallBack({ ...none, basis: "prior" }, "prior", false)).toBe(false);
    // an older API says nothing of comparisons: an empty year-ago answer falls back
    const older = { company_id: "x", accession_no: "y", basis: "year_ago", as_of: "", sections: [] } as SectionChanges;
    expect(isMaterial(older)).toBe(false);
    expect(fallBack(older, "year_ago", false)).toBe(true);
  });

  it("ranks every section's changes in one list, reading order keeps the API's", () => {
    const ranked = placed(deck, "ranked");
    const scores = ranked.map((p) => p.change.score!);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
    expect(ranked[1]!.change.text).toContain("$46,000 of IEEPA tariff net refunds");
    expect(placed(vrt, "reading").map((p) => p.change)).toEqual(vrt.sections.flatMap((s) => s.changes));
  });

  it("says what the pass left out in one sentence, and how many changes match", () => {
    expect(hiddenLine(vrt)).toBe("Not shown: 9 calendar rollovers (dates, “second” to “third” quarter), 2 paragraphs only split, " +
      "rejoined or page furniture, 2 table rows whose figures alone changed, 2 minor edits.");
    expect(hiddenLine({ ...vrt, sections: [] })).toBeNull();
    expect(countLine(deck, 10)).toBe("10 of 91 changes about “tariffs”");
    expect(countLine(vrt, 22)).toBe("22 changes");
    expect(countLine({ ...vrt, matched: 40, total: 40 }, 30)).toBe("40 changes (the first 30 shown)");
  });

  it("words periods and section names", () => {
    expect(periodLabel("three months")).toBe("3 months");
    expect(periodLabel("six months")).toBe("6 months, year to date");
    expect(periodLabel("three and six months")).toBe("3 and 6 months");
    expect(periodLabel(null)).toBeNull();
    expect(sectionShort("I-2")).toBe("MD&A");
    expect(sectionShort("II-1A")).toBe("Risk Factors");
    expect(sectionShort("7")).toBe("MD&A");
  });

  it("marks the API's topic terms as word prefixes and phrases, nothing else", () => {
    expect(marks("IEEPA tariff net refunds; Tariffs and rate", ["tariff"])).toEqual([
      ["IEEPA ", false], ["tariff", true], [" net refunds; ", false], ["Tariffs", true], [" and rate", false]]);
    expect(marks("the supply  chain held", ["supply chain"])).toEqual([["the ", false], ["supply  chain", true], [" held", false]]);
    expect(marks("antitariff", ["tariff"])).toEqual([["antitariff", false]]);
    expect(marks("a (b) c", ["(b"])).toEqual([["a ", false], ["(b", true], [") c", false]]);
  });
});
