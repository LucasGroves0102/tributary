import { expect, it } from "vitest";

import { sectionName, sectionTitle } from "./sections";

it.each([
  ["I-2", "MD&A"],
  ["7", "MD&A"],
  ["1A", "Risk Factors"],
  ["II-1A", "Risk Factors"],
  ["I-1", "Financial Statements"],
  ["2.02", "Results of operations"],
  ["EX-99.1", "Press release"],
  ["EX-99", "Press release"],
  ["EX-10.1", "Exhibit 10.1"],
  ["3C", "Item 3C"],
])("%s reads %s", (key, name) => expect(sectionName(key)).toBe(name));

it("a title the parser left empty (rule 10) reads as the section's name", () => {
  expect(sectionTitle("2.02", "")).toBe("Results of operations");
  expect(sectionTitle("1A", "  ")).toBe("Risk Factors");
  expect(sectionTitle("1A", "RISK FACTORS")).toBe("RISK FACTORS");
  expect(sectionTitle("7", null)).toBe("MD&A");
});
