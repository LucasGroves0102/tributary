import { describe, expect, it } from "vitest";

import { asCitations, checkCost, clearHighlights, clock, findQuote, highlightQuote, matchLabel, matchValue, normalize, plainQuote, points, STATUS_LABELS, textHash, wroteOtherwise } from "./check";

describe("plainQuote", () => {
  it.each([
    ["**Export controls** matter.", "Export controls matter."],
    ["__Margins__ fell", "Margins fell"],
    ["It *really* grew.", "It really grew."],
    ["Uses `code` here", "Uses code here"],
    ["- A bullet", "A bullet"],
    ["## A heading", "A heading"],
    ["No markup, 5 * 3 = 15.", "No markup, 5 * 3 = 15."],
  ])("%s", (q, want) => expect(plainQuote(q)).toBe(want));
});

describe("textHash", () => {
  it("is agent-api's thesis_check.text_hash (same hex for the same company and text)", async () => {
    // services/agent tests/test_thesis_check.py test_text_hash_matches_the_web_app
    expect(await textHash("0001045810", "# NVDA\n\nRevenue grew 10% — “fast”.\n")).toBe(
      "55f4f1947748203606ebbc64d43ed7de1b85ceef384d181d98bfff120ad38f17",
    );
    expect(await textHash(null, "x")).toBe("bbe56d7c2d0f1f7271eb6f829800598a0a1c5a54f09cb49c47607e0698af7d6f");
  });
});

describe("findQuote", () => {
  const text = "# Why\n\nData  centers keep\nbuying “GPUs” — fast.\n";
  it("finds a quote ignoring case, whitespace and curly quotes, in original offsets", () => {
    const at = findQuote(text, 'data centers keep buying "GPUs" - fast.')!;
    expect(text.slice(at.start, at.end)).toBe("Data  centers keep\nbuying “GPUs” — fast.");
    expect(findQuote(text, "not there")).toBeNull();
    expect(findQuote(text, "   ")).toBeNull();
  });

  it("normalize maps every kept character back", () => {
    const n = normalize("A \n B");
    expect(n.text).toBe("a b");
    expect(n.at).toEqual([0, 1, 4]);
  });
});

describe("highlightQuote", () => {
  it("marks a quote across inline elements, and clears it again", () => {
    const root = document.createElement("div");
    root.innerHTML = "<p>Revenue <strong>grew 10%</strong> last year. Other text.</p>";
    const mark = highlightQuote(root, "revenue grew 10% last year")!;
    expect(mark.textContent).toBe("Revenue ");
    expect([...root.querySelectorAll("mark.claim-mark")].map((m) => m.textContent)).toEqual(["Revenue ", "grew 10%", " last year"]);
    expect(root.querySelector("strong mark")).not.toBeNull();
    clearHighlights(root);
    expect(root.querySelector("mark")).toBeNull();
    expect(root.innerHTML).toBe("<p>Revenue <strong>grew 10%</strong> last year. Other text.</p>");
    expect(highlightQuote(root, "<img src=x onerror=alert(1)>")).toBeNull();
  });

  it("never parses the quote as HTML", () => {
    const root = document.createElement("div");
    root.textContent = "a <b>tag</b> as text";
    highlightQuote(root, "<b>tag</b>");
    expect(root.querySelector("b")).toBeNull();
    expect(root.querySelector("mark")?.textContent).toBe("<b>tag</b>");
  });
});

describe("cost, time and the user's model", () => {
  const a = { per_hour: 30, network_per_hour: 60, left_this_hour: 28, paused: false, per_day: null, left_today: null, report_questions: 3, check_questions: 4 };
  it("says the server's numbers, and none without them", () => {
    expect(checkCost({ ...a, check_seconds: 130 })).toBe("A check counts as 4 questions (you have 28 left this hour) and usually takes about 2 minutes.");
    expect(checkCost({ ...a, check_questions: 5, check_seconds: 50 })).toBe("A check counts as 5 questions (you have 28 left this hour) and usually takes about 1 minute.");
    expect(checkCost(a)).toBe("A check counts as 4 questions (you have 28 left this hour)."); // an older agent-api
    expect(checkCost(null)).not.toMatch(/\d/);
    expect(clock(0)).toBe("0:00");
    expect(clock(130.6)).toBe("2:10");
  });
  it("writes a model's figure as the model page does", () => {
    const m = { figure: "x", line: "revenue", fy: 2027, case: "base" as const, kind: "money" as const, value: 18750412800 };
    expect([matchLabel(m), matchValue(m)]).toEqual(["FY2027E revenue", "$18.75B"]);
    expect(matchValue({ ...m, value: 8.5159 })).toBe("$8.52"); // per share, to the cent
    expect(matchValue({ ...m, value: 2.1e9 }, "EUR")).toBe("€2.10B");
    expect(matchValue({ ...m, kind: "percent", value: 21.0024 })).toBe("21.0%");
    expect(matchValue({ ...m, kind: "multiple", value: 29.18 })).toBe("29.2x");
    expect(matchLabel({ ...m, fy: null, line: "your price" })).toBe("your price");
    expect(STATUS_LABELS.model).toBe("Your model");
    // said only when the thesis's rounding reads differently from the model's figure
    expect(wroteOtherwise({ ...m, figure: "$18.75B" })).toBeNull();
    expect(wroteOtherwise({ ...m, figure: "$18.8B" })).toBe("$18.8B");
    expect(wroteOtherwise({ ...m, figure: "$185", value: 185 })).toBeNull();
    expect(wroteOtherwise({ ...m, kind: "multiple", figure: "29x", value: 29.18 })).toBe("29x");
    expect(wroteOtherwise({ ...m, kind: "percent", figure: "21.0%", value: 21.0024 })).toBeNull();
  });
});

it("only citations a tool returned link to filings", () => {
  expect(asCitations([
    { accession_no: "0001045810-26-000021", form: "10-Q", filed: "2026-08-27", company_id: "0001045810", url: null, section_key: "I-2", verified: true },
    { accession_no: "0000000000-26-000001", verified: false },
  ])).toEqual([{ accession_no: "0001045810-26-000021", form: "10-Q", filed: "2026-08-27", company_id: "0001045810", url: null, sections: ["I-2"] }]);
  expect([points(15), points(-3.25), points(0)]).toEqual(["+15.0 pts", "−3.3 pts", "+0.0 pts"]);
});
