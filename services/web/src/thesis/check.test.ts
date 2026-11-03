import { describe, expect, it } from "vitest";

import { asCitations, clearHighlights, findQuote, highlightQuote, normalize, points, textHash } from "./check";

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

it("only citations a tool returned link to filings", () => {
  expect(asCitations([
    { accession_no: "0001045810-26-000021", form: "10-Q", filed: "2026-08-27", company_id: "0001045810", url: null, section_key: "I-2", verified: true },
    { accession_no: "0000000000-26-000001", verified: false },
  ])).toEqual([{ accession_no: "0001045810-26-000021", form: "10-Q", filed: "2026-08-27", company_id: "0001045810", url: null, sections: ["I-2"] }]);
  expect([points(15), points(-3.25), points(0)]).toEqual(["+15.0 pts", "−3.3 pts", "+0.0 pts"]);
});
