import { describe, expect, it } from "vitest";

import type { IndexedFiling } from "../types";
import {
  comparableForm,
  countMatches,
  highlight,
  outlineGroups,
  paragraphAt,
  paragraphs,
  priorComparable,
  searchTerms,
  snippetRuns,
} from "./text";

describe("paragraphs", () => {
  const text = "  Risk one is here.\n\nRisk two\ncontinues.\nThird starts here.\n• a bullet\n";
  const ps = paragraphs(text);

  it("splits on blank lines and on a break before a capital or bullet, keeping offsets", () => {
    expect(ps.map((p) => p.text)).toEqual(["Risk one is here.", "Risk two\ncontinues.", "Third starts here.", "• a bullet"]);
    for (const p of ps) expect(text.slice(p.start, p.end)).toBe(p.text);
  });

  it("finds the paragraph holding an offset", () => {
    expect(paragraphAt(ps, 0)).toBe(0);
    expect(paragraphAt(ps, text.indexOf("continues"))).toBe(1);
    expect(paragraphAt(ps, text.indexOf("Third") - 1)).toBe(2);
    expect(paragraphAt(ps, text.length + 5)).toBe(-1);
  });
});

it("groups a 10-K's items into parts, a 10-Q's keys by part, and exhibits last", () => {
  const s = (section_key: string) => ({ section_key, title: null, chars: 1 });
  expect(outlineGroups(["1", "1A", "7", "7A", "9B", "10", "15"].map(s)).map((g) => [g.label, g.sections.map((x) => x.section_key)]))
    .toEqual([["Part I", ["1", "1A"]], ["Part II", ["7", "7A", "9B"]], ["Part III", ["10"]], ["Part IV", ["15"]]]);
  expect(outlineGroups(["I-1", "I-2", "II-1A", "EX-99.1"].map(s)).map((g) => g.label)).toEqual(["Part I", "Part II", "Exhibits"]);
  expect(outlineGroups(["2.02", "9.01", "EX-99.1"].map(s)).map((g) => g.label)).toEqual(["Items", "Exhibits"]);
});

describe("search terms and highlighting", () => {
  it("reads websearch syntax: phrases, words, no 'or', no excluded words", () => {
    expect(searchTerms('"export controls" or tariffs -china a')).toEqual(["export controls", "tariffs"]);
    expect(searchTerms('-"supply chain" (chips)')).toEqual(["chips"]);
  });

  it("marks matches at word starts, case-insensitively, phrases across whitespace", () => {
    expect(highlight("Exports rose; export\ncontrols tightened.", ["export controls", "export"])).toEqual([
      { mark: "Exports" }, " rose; ", { mark: "export\ncontrols" }, " tightened.",
    ]);
    expect(highlight("reexport", ["export"])).toEqual(["reexport"]);
    expect(highlight("a (b) c", ["(b"])).toEqual(["a (b) c"]);
    expect(countMatches("Tariffs and tariff rates", ["tariff"])).toBe(2);
    expect(highlight("plain", [])).toEqual(["plain"]);
  });

  it("turns the API's « » snippet marks into runs", () => {
    expect(snippetRuns("new «export» «controls» … end")).toEqual(["new ", { mark: "export" }, " ", { mark: "controls" }, " … end"]);
  });
});

describe("prior comparable filing", () => {
  const row = (accession_no: string, form: string, filed: string): IndexedFiling => ({
    accession_no, company_id: "0001045810", name: null, form, filed, accepted_at: null, period_of_report: null,
    filing_url: null, document_url: null, sections: 1, sections_available: true,
  });
  const index = [
    row("0001045810-26-000075", "10-Q", "2026-08-26"),
    row("0001045810-26-000074", "10-Q/A", "2026-08-01"),
    row("0001045810-26-000050", "10-Q", "2026-05-28"),
    row("0001045810-26-000021", "10-K", "2026-02-26"),
  ];

  it("is the previous original of the same periodic form", () => {
    expect(priorComparable(index, { accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26" })?.accession_no)
      .toBe("0001045810-26-000050");
    expect(priorComparable(index, { accession_no: "0001045810-26-000074", form: "10-Q/A", filed: "2026-08-01" })?.accession_no)
      .toBe("0001045810-26-000050");
    expect(priorComparable(index, { accession_no: "0001045810-26-000021", form: "10-K", filed: "2026-02-26" })).toBeNull();
    expect(priorComparable(index, { accession_no: "0001045810-26-000080", form: "8-K", filed: "2026-09-01" })).toBeNull();
    expect(comparableForm("10-K/A")).toBe("10-K");
    expect(comparableForm("DEF 14A")).toBeNull();
  });
});
