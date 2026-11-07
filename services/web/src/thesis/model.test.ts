import { describe, expect, it } from "vitest";

import {
  embeddedIds,
  importMarkdown,
  insertEmbed,
  MAX_MARKDOWN,
  restoreThesis,
  thesisMarkdown,
  thesisSegments,
  thesisState,
  type EmbedItem,
} from "./model";

const CHART_ID = "11111111-1111-4111-8111-111111111111";
const MODEL_ID = "22222222-2222-4222-8222-222222222222";
const REPORT_ID = "33333333-3333-4333-8333-333333333333";
const FOREIGN = "44444444-4444-4444-8444-444444444444";
const NVDA = "0001045810";

const ITEMS: EmbedItem[] = [
  { item_id: CHART_ID, kind: "chart", name: "NVDA revenue", state: { layers: [] } },
  { item_id: MODEL_ID, kind: "model", name: "NVDA model", state: { companyId: NVDA } },
  { item_id: REPORT_ID, kind: "report", name: "A report", state: {} },
];

describe("thesisSegments", () => {
  it("splits the text at embeds of this workspace's items", () => {
    const md = `# Why\n\nIntro.\n\n[[embed:${CHART_ID}]]\n\nMiddle.\n  [[embed:${MODEL_ID}]]  \nEnd.`;
    const s = thesisSegments(md, ITEMS);
    expect(s.map((x) => (x.type === "text" ? x.markdown : `<${x.item.name}>`))).toEqual([
      "# Why\n\nIntro.",
      "<NVDA revenue>",
      "Middle.",
      "<NVDA model>",
      "End.",
    ]);
  });

  it("drops unknown, foreign, non-embeddable, repeated and malformed markers", () => {
    const md = [
      "a",
      `[[embed:${FOREIGN}]]`, // another workspace's (or another user's) item
      `[[embed:${REPORT_ID}]]`, // a report can't be embedded
      "[[embed:item-1]]", // a report's ref, not an id
      `[[ embed : ${CHART_ID} ]]`, // malformed
      `[[embed:${CHART_ID}0]]`,
      `[[embed:${CHART_ID}]]`,
      `[[embed:${CHART_ID}]]`, // only once
      `inline [[embed:${MODEL_ID}]] stays text`,
      "b",
    ].join("\n");
    const s = thesisSegments(md, ITEMS);
    expect(s.filter((x) => x.type === "embed").map((x) => x.type === "embed" && x.item.item_id)).toEqual([CHART_ID]);
    expect(s.map((x) => (x.type === "text" ? x.markdown : "<embed>"))).toEqual(["a", "<embed>", `inline [[embed:${MODEL_ID}]] stays text\nb`]);
    expect(embeddedIds(md)).toEqual([FOREIGN, REPORT_ID, CHART_ID, CHART_ID]);
  });
});

describe("insertEmbed", () => {
  it("puts the marker on a line of its own at the cursor", () => {
    const m = `[[embed:${CHART_ID}]]`;
    expect(insertEmbed("", 0, CHART_ID)).toEqual({ text: `${m}\n\n`, cursor: m.length + 2 });
    const r = insertEmbed("Para one.Para two.", 9, CHART_ID);
    expect(r.text).toBe(`Para one.\n\n${m}\n\nPara two.`);
    expect(r.text.slice(r.cursor)).toBe("Para two.");
    expect(insertEmbed("A\n\nB", 3, CHART_ID).text).toBe(`A\n\n${m}\n\nB`);
    expect(insertEmbed("A\n", 2, CHART_ID).text).toBe(`A\n\n${m}\n\n`);
    expect(insertEmbed("A", 99, CHART_ID).text).toBe(`A\n\n${m}\n\n`);
  });
});

describe("restoreThesis / thesisState", () => {
  it("keeps a well-formed state and cleans the rest", () => {
    expect(restoreThesis({ companyId: NVDA, markdown: "x\r\ny\u0007" })).toEqual({ companyId: NVDA, markdown: "x\ny" });
    expect(restoreThesis({ companyId: "NVDA", markdown: 5 })).toEqual({ companyId: null, markdown: "" });
    expect(restoreThesis(null)).toEqual({ companyId: null, markdown: "" });
    expect(thesisState({ companyId: null, markdown: "m" })).toEqual({ markdown: "m" });
    expect(thesisState({ companyId: NVDA, markdown: "" })).toEqual({ companyId: NVDA, markdown: "" });
  });
});

describe("thesisMarkdown (export)", () => {
  it("writes a header and turns embeds into readable lines", () => {
    const md = thesisMarkdown({
      title: 'NVDA: "the" build-out',
      thesis: { companyId: NVDA, markdown: `Intro.\n\n[[embed:${CHART_ID}]]\n\n[[embed:${FOREIGN}]]\n\n[[embed:${MODEL_ID}]]\n\nEnd.` },
      company: "NVIDIA (NVDA)",
      date: "2026-11-02",
      items: ITEMS,
    });
    expect(md).toBe(
      [
        "---",
        'title: "NVDA: \\"the\\" build-out"',
        'company: "NVIDIA (NVDA)"',
        `cik: "${NVDA}"`,
        "date: 2026-11-02",
        "source: Tributary thesis",
        "---",
        "",
        "Intro.",
        "",
        "[Chart: NVDA revenue]",
        "",
        "[Model: NVDA model]",
        "",
        "End.",
        "",
      ].join("\n"),
    );
  });

  it("names no company when the thesis has none", () => {
    const md = thesisMarkdown({ title: "Ideas", thesis: { companyId: null, markdown: "x" }, company: null, date: "2026-11-02", items: [] });
    expect(md).not.toMatch(/company:|cik:/);
    // a name not known yet is left out: the id goes only in its labelled field
    const unnamed = thesisMarkdown({ title: "T", thesis: { companyId: NVDA, markdown: "x" }, company: null, date: "2026-11-02", items: [] });
    expect(unnamed).not.toContain("company:");
    expect(unnamed).toContain(`cik: "${NVDA}"`);
  });
});

describe("importMarkdown", () => {
  it("reads plain text, normalizes newlines, drops control characters", () => {
    expect(importMarkdown("﻿# T\r\nline\u0001\r\n")).toEqual({ ok: true, markdown: "# T\nline\n", title: null });
  });

  it("takes the title from a front-matter header and drops the header", () => {
    const exported = thesisMarkdown({ title: 'My "big" idea', thesis: { companyId: NVDA, markdown: "Body." }, company: "NVIDIA", date: "2026-11-02", items: [] });
    expect(importMarkdown(exported)).toEqual({ ok: true, markdown: "Body.\n", title: 'My "big" idea' });
  });

  it("refuses binary files and files over the size limit", () => {
    expect(importMarkdown("PK\u0003\u0004\u0000\u0000")).toMatchObject({ ok: false });
    expect(importMarkdown("bad � bytes")).toMatchObject({ ok: false });
    expect(importMarkdown("x".repeat(MAX_MARKDOWN + 1))).toMatchObject({ ok: false, error: expect.stringContaining("100,000") });
    expect(importMarkdown("x".repeat(MAX_MARKDOWN))).toMatchObject({ ok: true });
  });

  it("keeps HTML as text for the renderer to sanitize", () => {
    const r = importMarkdown("<script>alert(1)</script>");
    expect(r).toEqual({ ok: true, markdown: "<script>alert(1)</script>", title: null });
  });
});
