import { describe, expect, it } from "vitest";

import { spreadNote } from "./SmallMultiples";

describe("the dollar charts' size note", () => {
  it("is measured per chart, by that chart's own metric (analyst test 4: revenue 14x, FCF 130x)", () => {
    // Vertiv's peers: Eaton $31.98B and Aaon $2.27B of revenue; free cash flow $3.79B against $29.7M
    const note = spreadNote([
      { name: "revenue", latest: [13.12e9, 31.98e9, 25.0e9, 2.27e9, 5.29e9] },
      { name: "free cash flow", latest: [3.15e9, 3.79e9, 3.17e9, 29.71e6, 642.6e6] },
    ]);
    expect(note).toBe("In free cash flow, the largest company is over 100 times the smallest, which reads as zero in dollars: Log scale or Indexed shows them all.");
    expect(note).not.toContain("revenue");
  });

  it("says nothing when no chart is that wide, and leaves out zero, negative and missing values", () => {
    expect(spreadNote([{ name: "revenue", latest: [13.12e9, 2.27e9] }])).toBeNull();
    expect(spreadNote([{ name: "free cash flow", latest: [3.79e9, -5e6, 0, Number.NaN] }])).toBeNull();
  });

  it("names every wide chart", () => {
    expect(spreadNote([{ name: "revenue", latest: [1e9, 1e6] }, { name: "free cash flow", latest: [1e9, 1e6] }]))
      .toMatch(/^In revenue and free cash flow, /);
  });
});
