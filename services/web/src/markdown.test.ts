import { describe, expect, it } from "vitest";

import { renderAnswer } from "./markdown";
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

  it("renders tables", () => {
    const html = renderAnswer("| Q | Rev |\n|---|---|\n| Q2 | $96.2B |", []);
    expect(dom(html).querySelectorAll("td")).toHaveLength(2);
  });
});
