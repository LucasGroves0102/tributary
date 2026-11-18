import { expect, it } from "vitest";

import { sectionName, sectionTitle, statementsElsewhere } from "./sections";

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
  ["3.D", "Risk Factors"],
  ["6-K", "Results release"],
  ["40-F", "Form 40-F"],
])("%s reads %s", (key, name) => expect(sectionName(key)).toBe(name));

it("a title the parser left empty (rule 10) reads as the section's name", () => {
  expect(sectionTitle("2.02", "")).toBe("Results of operations");
  expect(sectionTitle("1A", "  ")).toBe("Risk Factors");
  expect(sectionTitle("1A", "RISK FACTORS")).toBe("RISK FACTORS");
  expect(sectionTitle("7", null)).toBe("MD&A");
});

it("a 10-K's Item 8 that only points to the statements leads to the section holding them (Riley, test 10, finding 10)", () => {
  // NVIDIA's 10-K filed Feb 25, 2026, its outline as production stores it
  const nvda = [
    { section_key: "7A", title: "Quantitative and Qualitative Disclosures about Market Risk", chars: 4253 },
    { section_key: "8", title: "Financial Statements and Supplementary Data", chars: 206 },
    { section_key: "9", title: "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure", chars: 98 },
    { section_key: "15", title: "Exhibits and Financial Statement Schedules", chars: 105421 },
    { section_key: "16", title: "Form 10-K Summary", chars: 2565 },
  ];
  expect(statementsElsewhere("10-K", nvda, "8")).toEqual(nvda[3]);
  // Item 8 that holds them, another section, a 10-Q: nothing to add
  expect(statementsElsewhere("10-K", nvda.map((s) => (s.section_key === "8" ? { ...s, chars: 80000 } : s)), "8")).toBeNull();
  expect(statementsElsewhere("10-K", nvda, "9")).toBeNull();
  expect(statementsElsewhere("10-Q", nvda, "8")).toBeNull();
  // nothing after it that names the statements
  expect(statementsElsewhere("10-K", nvda.filter((s) => s.section_key !== "15"), "8")).toBeNull();
});
