import { describe, expect, it } from "vitest";

import { appendToSection, quoteBlock, quoteLines, quoteTable, thesisTemplate, type Passage } from "../analysis/project";
import { lineAt, lineOffset, moveUnit, quoteInfo, removeUnit, replaceRun, runText, segmentsOf, unitsOf } from "./blocks";
import { importMarkdown, thesisMarkdown } from "./model";

const CHART = "11111111-1111-4111-8111-111111111111";
const COMPS = "22222222-2222-4222-8222-222222222222";
const PASSAGE: Passage = {
  text: "Revenue grew 62% on Data Center demand.", companyId: "0001045810", accessionNo: "0001045810-26-000075",
  form: "10-Q", filed: "2026-08-27", period: "2026-07-26", section: "Item 2. MD&A",
};
const RELEASE: Passage = {
  ...PASSAGE, form: "8-K", accessionNo: "0001045810-26-000070", period: "2026-07-26", periodWord: "quarter",
  text: "Revenue is expected to be $54.0 billion, plus or minus 2%.\nGross margins are expected to be 73.3%.",
  section: "EX-99.1 Outlook",
};

/** A thesis as pins leave it: template, a chart and comps, a quote, a release outlook, a table. */
function pinned(): string {
  let md = thesisTemplate({ company: "NVIDIA (NVDA)", catalysts: [] });
  md = appendToSection(md, "pillars", `[[embed:${CHART}]]`);
  md = appendToSection(md, "pillars", quoteBlock(PASSAGE));
  md = appendToSection(md, "pillars", `[[embed:${COMPS}]]`);
  md = appendToSection(md, "catalysts", quoteLines(RELEASE));
  md = appendToSection(md, "risks", quoteTable({ ...RELEASE, text: "Revenue | 54.0 | ± 2%\nGross margin | 73.3% |" }));
  return md;
}

const joined = (md: string) => {
  const units = unitsOf(md);
  return units.map((u) => md.split("\n").slice(u.start, u.end).join("\n")).join("\n");
};

describe("thesis blocks", () => {
  it("cover every line in order, so the Markdown is unchanged byte for byte", () => {
    for (const md of ["", "\n", "x", "# T\n\n\n", pinned(), `a\n[[embed:${CHART}]]\n[[embed:${COMPS}]]\nb`, "```\n[[embed:x]]\n\n```\nafter"]) {
      expect(joined(md)).toBe(md);
      const units = unitsOf(md);
      units.forEach((u, i) => i && expect(u.start).toBe(units[i - 1]!.end));
    }
  });

  it("find embeds and pinned quotes; a typed quote and a fenced marker stay text", () => {
    const md = pinned();
    const cards = unitsOf(md).filter((u) => u.kind === "embed" || u.kind === "quote");
    expect(cards.map((u) => u.kind)).toEqual(["embed", "quote", "embed", "quote", "quote"]);
    expect(cards.map((u) => u.id ?? null)).toEqual([CHART, null, COMPS, null, null]);
    expect(unitsOf("> I typed this\n> myself").map((u) => u.kind)).toEqual(["text"]);
    expect(unitsOf(`\`\`\`\n[[embed:${CHART}]]\n\`\`\``).map((u) => u.kind)).toEqual(["text"]);
    expect(unitsOf("[[embed:not-a-uuid]]").map((u) => u.kind)).toEqual(["text"]);
  });

  it("say what a quote is: a 10-Q passage with its period, an 8-K earnings release", () => {
    const md = pinned();
    const quotes = unitsOf(md).filter((u) => u.kind === "quote");
    expect(quoteInfo(md, quotes[0]!)).toEqual({ form: "10-Q", earnings: false, period: "quarter ended 2026-07-26" });
    expect(quoteInfo(md, quotes[1]!)).toEqual({ form: "8-K", earnings: true, period: "quarter ended 2026-07-26" });
  });

  it("group text and blank lines into one text box between cards", () => {
    const md = `# T\n\nIntro.\n\n[[embed:${CHART}]]\n\nMore.\n`;
    const segs = segmentsOf(unitsOf(md));
    expect(segs.map((s) => s.kind)).toEqual(["run", "card", "run"]);
    const [first, , last] = segs as [{ start: number; end: number }, unknown, { start: number; end: number }];
    expect(runText(md, first.start, first.end)).toBe("# T\n\nIntro.\n");
    expect(runText(md, last.start, last.end)).toBe("\nMore.\n");
    expect(replaceRun(md, first.start, first.end, "# T\n\nIntro, edited.\n")).toBe(`# T\n\nIntro, edited.\n\n[[embed:${CHART}]]\n\nMore.\n`);
    expect(replaceRun(md, first.start, first.end, runText(md, first.start, first.end))).toBe(md);
  });

  it("move a card past the next paragraph or card, keeping the blank lines", () => {
    const md = `# T\n\nOne.\n\n[[embed:${CHART}]]\n\nTwo.\n\n[[embed:${COMPS}]]\n`;
    const units = unitsOf(md);
    const chart = units.findIndex((u) => u.id === CHART);
    const up = moveUnit(md, units, chart, -1)!;
    expect(up.markdown).toBe(`# T\n\n[[embed:${CHART}]]\n\nOne.\n\nTwo.\n\n[[embed:${COMPS}]]\n`);
    expect(up.markdown.split("\n")[up.line]).toBe(`[[embed:${CHART}]]`);
    const down = moveUnit(md, units, chart, 1)!;
    expect(down.markdown).toBe(`# T\n\nOne.\n\nTwo.\n\n[[embed:${CHART}]]\n\n[[embed:${COMPS}]]\n`);
    expect(down.markdown.split("\n")[down.line]).toBe(`[[embed:${CHART}]]`);
    // and back again
    const back = moveUnit(down.markdown, unitsOf(down.markdown), unitsOf(down.markdown).findIndex((u) => u.id === CHART), -1)!;
    expect(back.markdown).toBe(md);
    // at the ends: nothing
    const top = `[[embed:${CHART}]]\n\nText.`;
    expect(moveUnit(top, unitsOf(top), 0, -1)).toBeNull();
    expect(moveUnit(`Text.\n\n[[embed:${CHART}]]\n`, unitsOf(`Text.\n\n[[embed:${CHART}]]\n`), 2, 1)).toBeNull();
  });

  it("move a pinned quote whole", () => {
    const md = pinned();
    const units = unitsOf(md);
    const q = units.findIndex((u) => u.kind === "quote");
    const up = moveUnit(md, units, q, -1)!;
    const moved = unitsOf(up.markdown);
    expect(moved.findIndex((u) => u.kind === "quote")).toBeLessThan(moved.findIndex((u) => u.id === CHART));
    expect(up.markdown.length).toBe(md.length);
    expect(up.markdown).toContain(quoteBlock(PASSAGE));
  });

  it("remove a card and the blank line it leaves doubled", () => {
    const md = `One.\n\n[[embed:${CHART}]]\n\nTwo.`;
    const units = unitsOf(md);
    const out = removeUnit(md, units, units.findIndex((u) => u.id === CHART))!;
    expect(out.markdown).toBe("One.\n\nTwo.");
    expect(removeUnit(`[[embed:${CHART}]]`, unitsOf(`[[embed:${CHART}]]`), 0)!.markdown).toBe("");
    const tail = `One.\n\n[[embed:${CHART}]]\n`;
    expect(removeUnit(tail, unitsOf(tail), 2)!.markdown).toBe("One.\n");
  });

  it("map offsets to lines and back", () => {
    const md = "ab\ncd\n\nef";
    expect(lineOffset(md, 0)).toBe(0);
    expect(lineOffset(md, 1)).toBe(3);
    expect(lineOffset(md, 3)).toBe(7);
    expect(lineAt(md, 4)).toBe(1);
    expect(lineAt(md, 7)).toBe(3);
  });

  it("leave export and import exactly as they were", () => {
    const md = pinned();
    const items = [CHART, COMPS].map((id, i) => ({ item_id: id, kind: (i ? "comps" : "chart") as "comps" | "chart", name: i ? "Comps" : "Revenue", state: {} }));
    const exported = thesisMarkdown({ title: "NVDA", thesis: { companyId: "0001045810", markdown: md }, company: "NVIDIA (NVDA)", date: "2026-11-09", items });
    const back = importMarkdown(exported);
    expect(back.ok).toBe(true);
    // the cards' Markdown comes back as written (embed blocks as their lines)
    if (back.ok) expect(unitsOf(back.markdown).filter((u) => u.kind !== "text" && u.kind !== "blank").map((u) => u.kind))
      .toEqual(unitsOf(md).filter((u) => u.kind !== "text" && u.kind !== "blank").map((u) => u.kind));
  });
});
