import { describe, expect, it } from "vitest";

import type { SqlResult } from "../types";
import {
  cellId,
  chartSeries,
  csvOf,
  defaultChart,
  lineOf,
  MAX_CELLS,
  moved,
  newCell,
  notebookState,
  queryName,
  restoreNotebook,
  resultLine,
  saveProblem,
} from "./notebook";

const RESULT: SqlResult = {
  columns: [{ name: "name", type: "text" }, { name: "period_end", type: "date" }, { name: "value", type: "number" }, { name: "up", type: "boolean" }],
  rows: [["Nvidia", "2026-04-26", "44062000000", true], ["Nvidia", "2026-01-25", "39331000000", false], ["Nvidia", "2026-07-26", null, null]],
  row_count: 3, truncated: null, max_rows: 10000, seconds: 0.04, cost: 300, reads: ["financials"], sql: "SELECT 1",
  budget: { used_seconds: 1, limit_seconds: 600 },
};

describe("cells", () => {
  it("ids are the API's shape and never repeat", () => {
    const seq = [0, 0, 0, 0, 0, 0, 0, 0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5];
    let i = 0;
    const id = cellId(["aaaaaaaa"], () => seq[i++]!);
    expect(id).toBe("ssssssss");
    expect(cellId()).toMatch(/^[a-z0-9]{8}$/);
  });

  it("a new chart draws the newest query by default; moves stay inside the list", () => {
    const cells = [newCell("sql", []), newCell("text", [])];
    const chart = newCell("chart", cells);
    expect(chart.chart!.source).toBe(cells[0]!.id);
    expect(moved([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
    expect(moved([1, 2, 3], 0, 1)).toEqual([2, 1, 3]);
    expect(queryName(cells, cells[0]!.id)).toBe("Query 1");
    expect(queryName(cells, "gone")).toBe("a deleted query");
  });
});

describe("saved state", () => {
  it("restores only well-formed cells, never results", () => {
    const nb = restoreNotebook({
      cells: [
        { id: "a1", kind: "sql", sql: "select 1", rows: [[1]] },
        { id: "a1", kind: "text", text: "repeated id" },
        { id: "BAD!", kind: "text", text: "x" },
        { id: "t1", kind: "text", text: 5 },
        { id: "c1", kind: "chart", chart: { source: "a1", type: "pie", x: "d", y: ["v", 3, "w"], title: "  " } },
        "nonsense",
      ],
    });
    expect(nb.cells).toEqual([
      { id: "a1", kind: "sql", sql: "select 1" },
      { id: "c1", kind: "chart", chart: { source: "a1", type: "line", x: "d", y: ["v", "w"] } },
    ]);
    expect(restoreNotebook(null)).toEqual({ cells: [] });
  });

  it("saves what the user wrote; an unfinished chart is left out", () => {
    const state = notebookState({ cells: [
      { id: "q", kind: "sql", sql: "select 1" },
      { id: "c", kind: "chart", chart: { source: "q", type: "bar", x: "", y: [] } },
      { id: "d", kind: "chart", chart: { source: "gone", type: "bar", x: "a", y: ["b"] } },
      { id: "e", kind: "chart", chart: { source: "q", type: "bar", x: "a", y: ["b"] } },
    ] });
    expect(state.cells.map((c) => c.id)).toEqual(["q", "e"]);
  });

  it("says why a notebook is too big to save", () => {
    expect(saveProblem({ cells: [] })).toBeNull();
    expect(saveProblem({ cells: Array.from({ length: MAX_CELLS + 1 }, (_, i) => ({ id: `c${i}`, kind: "text" as const, text: "" })) }))
      .toContain("at most 50 cells");
    expect(saveProblem({ cells: [{ id: "a", kind: "sql", sql: "x".repeat(20_001) }] })).toContain("Cell 1");
    const big = Array.from({ length: 4 }, (_, i) => ({ id: `c${i}`, kind: "sql" as const, sql: "x".repeat(19_000) }));
    expect(saveProblem({ cells: big })).toContain("at most 60 KB");
  });
});

describe("results", () => {
  it("a CSV keeps the exact values, quotes what needs it and defuses formulas", () => {
    const csv = csvOf({ ...RESULT, columns: [{ name: "a,b", type: "text" }, { name: "v", type: "number" }],
      rows: [["=HYPERLINK(1)", "-12.5"], ['say "hi"', null], ["-x", "1E+3"]] });
    expect(csv).toBe('"a,b",v\r\n\'=HYPERLINK(1),-12.5\r\n"say ""hi""",\r\n\'-x,1E+3\r\n');
  });

  it("says how many rows and why it stopped", () => {
    expect(resultLine(RESULT)).toBe("3 rows in 0.04 s");
    expect(resultLine({ ...RESULT, row_count: 10000, truncated: "rows", seconds: 0.001 }))
      .toBe("10,000 rows in under 0.01 s: the first 10,000; add a LIMIT or narrow the query");
  });

  it("a first chart is a date across and the first numbers drawn", () => {
    expect(defaultChart(RESULT, "q")).toEqual({ source: "q", type: "line", x: "period_end", y: ["value"] });
    expect(defaultChart({ ...RESULT, columns: [{ name: "up", type: "boolean" }], rows: [] }, "q")).toBeNull();
  });

  it("charts dates in order, keeps gaps, and says what's wrong instead of guessing", () => {
    const { series, problem } = chartSeries(RESULT, { source: "q", type: "line", x: "period_end", y: ["value"] });
    expect(problem).toBeNull();
    expect(series[0]!.points.map((p) => [p.period, p.y])).toEqual([
      ["2026-01-25", 39331000000], ["2026-04-26", 44062000000], ["2026-07-26", null]]);
    expect(chartSeries(RESULT, { source: "q", type: "line", x: "gone", y: ["value"] }).problem).toContain("no column gone");
    expect(chartSeries(RESULT, { source: "q", type: "line", x: "name", y: ["up"] }).problem).toBe("up isn't numbers.");
    // text across: the rows' order, a repeated x keeps its last row
    const byName = chartSeries(RESULT, { source: "q", type: "bar", x: "name", y: ["value"] });
    expect(byName.series[0]!.points).toHaveLength(1);
  });

  it("finds a syntax error's line and column", () => {
    expect(lineOf("select\nfrom where", 12)).toEqual({ line: 2, column: 6 });
  });
});
