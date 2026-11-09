import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { blockAt, blockUnits, dateHeads, readerBlocks, splitCells, type Block } from "./blocks";

// NVIDIA's FY2026 10-K, Item 15, as stored (production, parser rust-4): the end of the income
// statement through the start of the equity statement
const ITEM15 = readFileSync(`${process.cwd()}/src/reader/fixtures/nvda-10k-2026-item15.txt`, "utf8");
// the opening of its Item 1A, page break and all, as stored
const RISKS = "Risks Related to Demand, Supply, and Manufacturing\n•Long manufacturing lead times and uncertain supply and capacity availability, combined with a failure to estimate customer demand accurately has led and could lead to mismatches between supply and demand.\n12\nTable of Contents\n•Dependency on third-party suppliers reduces our control over product quantity.\nThe growing importance of AI could impinge on our ability to procure sufficient\n14\nTable of Contents\nfoundry capacity and scarce input materials during periods of demand.";

const tables = (bs: Block[]) => bs.filter((b): b is Extract<Block, { kind: "table" }> => b.kind === "table");
const row = (t: Extract<Block, { kind: "table" }>, label: string) => t.rows.find((r) => r.label === label);

describe("cells and headings", () => {
  it("reads trailing numeric cells the way EDGAR's HTML splits them", () => {
    expect(splitCells("Net income $ 120,067 $ 72,880 $ 29,760")).toEqual({ label: "Net income", cells: ["$120,067", "$72,880", "$29,760"] });
    expect(splitCells("Interest expense (259) (247) (257)")).toEqual({ label: "Interest expense", cells: ["(259)", "(247)", "(257)"] });
    expect(splitCells("Net change in unrealized gain (loss) 43 — (10)")).toEqual({ label: "Net change in unrealized gain (loss)", cells: ["43", "—", "(10)"] });
    expect(splitCells("Gross margin 71.1 % 75.0 %")).toEqual({ label: "Gross margin", cells: ["71.1%", "75.0%"] });
    expect(splitCells("Other ( 1,234 ) $ ( 5 )").cells).toEqual(["(1,234)", "$(5)"]);
    expect(splitCells("This sentence ends in 2026.")).toEqual({ label: "This sentence ends in 2026.", cells: [] });
  });

  it("knows a line of dates or years, with a short label", () => {
    expect(dateHeads("Jan 25, 2026 Jan 26, 2025")).toEqual({ label: "", cells: ["Jan 25, 2026", "Jan 26, 2025"] });
    expect(dateHeads("Year Ended 2026 2025 2024")?.cells).toEqual(["2026", "2025", "2024"]);
    expect(dateHeads("Fiscal Year 2026 Summary")).toBeNull();
  });
});

describe("#178: statements read as tables", () => {
  const blocks = readerBlocks(ITEM15);
  const [income, comprehensive, balance, equity] = tables(blocks);

  it("finds each statement, with its columns and date heads", () => {
    expect(tables(blocks)).toHaveLength(4);
    expect(income!.columns).toBe(3);
    expect(row(income!, "Net income")?.cells).toEqual(["$120,067", "$72,880", "$29,760"]);
    expect(comprehensive!.heads?.cells).toEqual(["Jan 25, 2026", "Jan 26, 2025", "Jan 28, 2024"]);
    expect(balance!.columns).toBe(2);
    expect(balance!.heads?.cells).toEqual(["Jan 25, 2026", "Jan 26, 2025"]);
    expect(row(balance!, "Cash and cash equivalents")?.cells).toEqual(["$10,605", "$8,589"]);
    expect(row(balance!, "Total assets")?.cells).toEqual(["$206,803", "$111,601"]);
    expect(row(balance!, "Short-term debt")?.cells).toEqual(["999", "—"]);
    expect(equity!.columns).toBe(6);
  });

  it("joins a label with the values its cell wrapped onto the next line", () => {
    expect(row(balance!, "Non-marketable equity securities")?.cells).toEqual(["22,251", "3,387"]);
    // a number at the end of such a label is the label's, not a column
    expect(row(balance!, "Commitments and contingencies - see Note 12")?.cells).toEqual(["—", "—"]);
    expect(row(equity!, "Balances as of Jan 29, 2023")?.cells).toEqual(["24,661", "$25", "$11,948", "$(43)", "$10,171", "$22,101"]);
    expect(row(equity!, "Shares repurchased")?.cells).toEqual(["(211)", "—", "(27)", "—", "(9,719)", "(9,746)"]);
  });

  it("keeps sub-headings as label rows and the sentence after a statement as text", () => {
    expect(row(balance!, "Current assets:")?.cells).toEqual([]);
    expect(row(income!, "Net income per share:")?.cells).toEqual([]);
    const after = blocks[blocks.indexOf(income!) + 1]!;
    expect(after).toMatchObject({ kind: "p", text: "See accompanying Notes to the Consolidated Financial Statements." });
  });

  it("offsets stay the stored text's", () => {
    const r = row(balance!, "Total assets")!;
    expect(ITEM15.slice(r.start, r.end)).toBe("Total assets $ 206,803 $ 111,601");
    expect(blockAt(blocks, r.start)).toBe(blocks.indexOf(balance!));
    expect(blockUnits(balance!)).toContain("$206,803");
  });
});

describe("#179: page debris", () => {
  it("drops page numbers and 'Table of Contents', and a sentence cut by a page rejoins", () => {
    const blocks = readerBlocks(RISKS);
    const text = blocks.map((b) => (b.kind === "p" ? b.text : "")).join("\n\n");
    expect(text).not.toContain("Table of Contents");
    expect(text).not.toMatch(/\n1[24]\n|demand\. 12/);
    expect(blocks.filter((b) => b.kind === "p").map((b) => (b as { text: string }).text.split("\n")[0]!.slice(0, 30))).toEqual([
      "Risks Related to Demand, Suppl",
      "•Long manufacturing lead times",
      "•Dependency on third-party sup",
      "The growing importance of AI c",
    ]);
    expect((blocks.at(-1) as { text: string }).text).toBe(
      "The growing importance of AI could impinge on our ability to procure sufficient\nfoundry capacity and scarce input materials during periods of demand.");
    expect(tables(blocks)).toEqual([]);
  });

  it("keeps a number that is a table's value, and one that isn't by a page break", () => {
    expect(readerBlocks("Employees at year end\n36\nMore text.").map((b) => b.kind)).toEqual(["p", "p"]);
    expect((readerBlocks("Employees at year end\n36\nMore text.")[0] as { text: string }).text).toBe("Employees at year end\n36");
    expect((readerBlocks("Our results improved.\n36")[0] as { text: string }).text).toBe("Our results improved.");
  });
});
