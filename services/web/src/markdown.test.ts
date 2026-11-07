import { describe, expect, it } from "vitest";

import { renderAnswer, renderMarkdown } from "./markdown";
import type { Citation } from "./types";

const NVDA_10Q: Citation = {
  accession_no: "0001045810-26-000075",
  form: "10-Q",
  filed: "2026-08-26",
  company_id: "0001045810",
  url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/",
};

function dom(html: string): HTMLElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

describe("model text a person reads", () => {
  it("writes timestamps, dates and field names as words", () => {
    const html = renderAnswer("Freshness: latest filing 2026-08-26; data as_of 2026-11-06T23:08:36Z.", []);
    expect(dom(html).textContent).toBe("Freshness: latest filing Aug 26, 2026; data as of Nov 6, 2026.\n");
  });

  it("writes an XBRL concept in words, the tag on its hover", () => {
    const html = renderAnswer("| Line | Source |\n|---|---|\n| Buybacks | us-gaap:PaymentsForRepurchaseOfCommonStock |", []);
    const span = dom(html).querySelector("td span")!;
    expect(span.textContent).toBe("Payments for repurchase of common stock");
    expect(span.getAttribute("title")).toBe("us-gaap:PaymentsForRepurchaseOfCommonStock");
    expect(dom(html).textContent).not.toContain("us-gaap");
  });

  it("leaves accession numbers alone, and still flags a figure", () => {
    const html = renderAnswer("0001045810-26-000075 reported $5B on 2026-08-26.", [], ["$5B"]);
    expect(dom(html).querySelector(".accession")?.textContent).toBe("0001045810-26-000075");
    expect(dom(html).querySelector(".figure.unverified")?.textContent).toBe("$5B");
    expect(dom(html).textContent).toContain("Aug 26, 2026");
  });

  it("leaves a thesis as its author wrote it", () => {
    expect(dom(renderMarkdown("Filed 2026-08-26 under us-gaap:Revenues.")).textContent).toContain("2026-08-26 under us-gaap:Revenues");
  });
});

describe("renderAnswer", () => {
  it("links accession numbers that came from tool results", () => {
    const html = renderAnswer("Revenue was $96.2B (10-Q, 0001045810-26-000075).", [NVDA_10Q]);
    const a = dom(html).querySelector("a.accession")!;
    expect(a.getAttribute("href")).toBe(NVDA_10Q.url);
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("marks accession numbers no tool returned", () => {
    const html = renderAnswer("See 0000320193-26-000020.", [NVDA_10Q]);
    expect(dom(html).querySelector(".accession.unverified")?.textContent).toBe(
      "0000320193-26-000020",
    );
    expect(dom(html).querySelector("a")).toBeNull();
  });

  it("only links to sec.gov", () => {
    const evil = { ...NVDA_10Q, url: "https://evil.example/x" };
    expect(dom(renderAnswer("0001045810-26-000075", [evil])).querySelector("a")).toBeNull();
  });

  it("strips scripts and event handlers from model output", () => {
    const html = renderAnswer(
      '<img src=x onerror="alert(1)"><script>alert(2)</script>[x](javascript:alert(3))',
      [],
    );
    const root = dom(html);
    expect(root.querySelector("script")).toBeNull();
    expect(root.querySelector("img")?.getAttribute("onerror")).toBeNull();
    expect(root.querySelector("a")?.getAttribute("href") ?? "").not.toMatch(/javascript/);
  });

  it("flags unverified figures", () => {
    const html = renderAnswer("Revenue was **$96.2B**, up $9.9B.", [], ["$9.9B"]);
    const flagged = dom(html).querySelectorAll(".figure.unverified");
    expect([...flagged].map((e) => e.textContent)).toEqual(["$9.9B"]);
  });

  it("renders tables", () => {
    const html = renderAnswer("| Q | Rev |\n|---|---|\n| Q2 | $96.2B |", []);
    expect(dom(html).querySelectorAll("td")).toHaveLength(2);
  });
});

describe("renderMarkdown (a user's thesis)", () => {
  it("renders Markdown and sanitizes scripts, handlers, styles and javascript: links", () => {
    const html = renderMarkdown(
      "# Title\n\n**bold** <script>alert(1)</script><b onclick=\"x()\" style=\"color:red\">b</b>" +
        "\n\n[evil](javascript:alert(1)) [ok](https://example.com)\n\n<style>body{}</style><form><input></form>",
    );
    const d = dom(html);
    expect(d.querySelector("h1")?.textContent).toBe("Title");
    expect(d.querySelector("script, style, form, input")).toBeNull();
    expect(d.innerHTML).not.toMatch(/onclick|style=|javascript:/);
    const hrefs = [...d.querySelectorAll("a[href]")];
    expect(hrefs.map((a) => a.getAttribute("href"))).toEqual(["https://example.com"]);
    expect(hrefs[0]!.getAttribute("rel")).toBe("noopener noreferrer");
    expect(hrefs[0]!.getAttribute("target")).toBe("_blank");
  });

  it("leaves accession numbers as plain text (nothing came from a tool)", () => {
    const d = dom(renderMarkdown("See 0000320193-26-000020."));
    expect(d.querySelector(".accession, .unverified")).toBeNull();
    expect(d.textContent).toContain("0000320193-26-000020");
  });

  it("turns images into their alt text: nothing is loaded from other origins", () => {
    const d = dom(renderMarkdown("![Revenue chart](https://evil.example/pixel.png)"));
    expect(d.querySelector("img")).toBeNull();
    expect(d.textContent).toContain("[image: Revenue chart]");
  });
});
