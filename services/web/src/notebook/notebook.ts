/** A notebook (#/notebook, B3a): ordered cells of SQL, the user's Markdown and charts over a SQL
 * cell's result, saved as a workspace item of kind `notebook` (query-api workspaces.py
 * NotebookState: these limits are its). A notebook saves only what the user wrote: results live in
 * the page while it is open and are read again, as whoever opens it, through POST /v1/sql. Pure,
 * tested without rendering. */

import type { Point, Series } from "../chart/series";
import type { NotebookCell, NotebookChart, NotebookState, SqlColumn, SqlResult, SqlValue } from "../types";

/** query-api workspaces.py: MAX_NOTEBOOK_CELLS, MAX_CELL_TEXT, MAX_CHART_SERIES, MAX_NOTEBOOK_BYTES */
export const MAX_CELLS = 50;
export const MAX_CELL_TEXT = 20_000;
export const MAX_SERIES = 6;
export const MAX_BYTES = 60_000;
const CELL_ID = /^[a-z0-9]{1,16}$/;
const COLUMN = 63;
/** where notebooks are saved (found by name, made on first use), like Your data's metrics */
export const NOTEBOOKS_WORKSPACE = "Notebooks";

/** A new cell id: eight random letters and digits (the API's pattern), never one in use. */
export function cellId(taken: Iterable<string> = [], random: () => number = Math.random): string {
  const used = new Set(taken);
  for (;;) {
    const id = Array.from({ length: 8 }, () => "abcdefghijklmnopqrstuvwxyz0123456789"[Math.floor(random() * 36)]).join("");
    if (!used.has(id)) return id;
  }
}

export function newCell(kind: NotebookCell["kind"], cells: NotebookCell[], body: Partial<NotebookCell> = {}): NotebookCell {
  const id = cellId(cells.map((c) => c.id));
  if (kind === "sql") return { id, kind, sql: body.sql ?? "" };
  if (kind === "text") return { id, kind, text: body.text ?? "" };
  const source = [...cells].reverse().find((c) => c.kind === "sql");
  return { id, kind, chart: body.chart ?? { source: source?.id ?? "", type: "line", x: "", y: [] } };
}

/** What a new notebook starts with: a line of text and one query to run. */
export function starter(example?: { title: string; sql: string }): NotebookState {
  const cells: NotebookCell[] = [];
  cells.push({ id: cellId(), kind: "text", text: example ? `## ${example.title}` : "## Notes" });
  cells.push({ id: cellId(cells.map((c) => c.id)), kind: "sql", sql: example?.sql ?? "SELECT name, tickers, sector\nFROM companies\nWHERE 'NVDA' = ANY (tickers)" });
  return { cells };
}

const text = (v: unknown, max: number): string | null => (typeof v === "string" && v.length <= max ? v : null);

/** A saved state as the page edits it: anything out of shape is dropped (a cell, a chart's
 * column), never trusted more than the API's own validation. */
export function restoreNotebook(raw: unknown): NotebookState {
  const cells: NotebookCell[] = [];
  const seen = new Set<string>();
  const input = raw && typeof raw === "object" && Array.isArray((raw as { cells?: unknown }).cells) ? (raw as { cells: unknown[] }).cells : [];
  for (const c of input.slice(0, MAX_CELLS)) {
    if (!c || typeof c !== "object") continue;
    const o = c as Record<string, unknown>;
    const id = typeof o.id === "string" && CELL_ID.test(o.id) && !seen.has(o.id) ? o.id : null;
    if (!id) continue;
    if (o.kind === "sql" && text(o.sql, MAX_CELL_TEXT) !== null) cells.push({ id, kind: "sql", sql: o.sql as string });
    else if (o.kind === "text" && text(o.text, MAX_CELL_TEXT) !== null) cells.push({ id, kind: "text", text: o.text as string });
    else if (o.kind === "chart" && o.chart && typeof o.chart === "object") {
      const ch = o.chart as Record<string, unknown>;
      const ys = Array.isArray(ch.y) ? ch.y.filter((y): y is string => typeof y === "string" && y.length > 0 && y.length <= COLUMN) : [];
      const chart: NotebookChart = {
        source: typeof ch.source === "string" ? ch.source : "",
        type: ch.type === "bar" ? "bar" : "line",
        x: text(ch.x, COLUMN) ?? "",
        y: ys.slice(0, MAX_SERIES),
        ...(typeof ch.title === "string" && ch.title.trim() ? { title: ch.title.slice(0, 120) } : {}),
      };
      cells.push({ id, kind: "chart", chart });
    } else continue;
    seen.add(id);
  }
  return { cells };
}

/** The state to save: a chart that isn't finished (no source query, x or y) is kept as the user
 * left it only when the API would take it; otherwise it is saved without its columns. */
export function notebookState(nb: NotebookState): NotebookState {
  const queries = new Set(nb.cells.filter((c) => c.kind === "sql").map((c) => c.id));
  return {
    cells: nb.cells.flatMap((c): NotebookCell[] => {
      if (c.kind === "sql") return [{ id: c.id, kind: "sql", sql: c.sql ?? "" }];
      if (c.kind === "text") return [{ id: c.id, kind: "text", text: c.text ?? "" }];
      const ch = c.chart;
      if (!ch || !queries.has(ch.source) || !ch.x || !ch.y.length) return []; // an unfinished chart isn't saved
      return [{ id: c.id, kind: "chart", chart: { source: ch.source, type: ch.type, x: ch.x, y: ch.y.slice(0, MAX_SERIES), ...(ch.title ? { title: ch.title } : {}) } }];
    }),
  };
}

/** Why the notebook can't be saved as it is (null: it can). */
export function saveProblem(nb: NotebookState): string | null {
  if (nb.cells.length > MAX_CELLS) return `A notebook holds at most ${MAX_CELLS} cells.`;
  const long = nb.cells.findIndex((c) => (c.sql ?? c.text ?? "").length > MAX_CELL_TEXT);
  if (long >= 0) return `Cell ${long + 1} is longer than ${MAX_CELL_TEXT.toLocaleString("en-US")} characters.`;
  const bytes = new TextEncoder().encode(JSON.stringify(notebookState(nb))).length;
  if (bytes > MAX_BYTES) return `The notebook is ${Math.ceil(bytes / 1000)} KB; it can be at most ${MAX_BYTES / 1000} KB. Split it in two.`;
  return null;
}

/** "Query 2": the number among the notebook's queries, as cells name their sources. */
export function queryName(cells: NotebookCell[], id: string): string {
  const n = cells.filter((c) => c.kind === "sql").findIndex((c) => c.id === id);
  return n >= 0 ? `Query ${n + 1}` : "a deleted query";
}

/** Moves the cell at `from` by `by` places (within the notebook). */
export function moved<T>(list: T[], from: number, by: number): T[] {
  const to = from + by;
  if (from < 0 || from >= list.length || to < 0 || to >= list.length) return list;
  const out = [...list];
  const [it] = out.splice(from, 1);
  out.splice(to, 0, it!);
  return out;
}

export const isNumberColumn = (c: SqlColumn) => c.type === "number";

/** A first chart for a result: x the first date (else text) column, y the first numeric one. */
export function defaultChart(result: SqlResult, source: string): NotebookChart | null {
  const x = result.columns.find((c) => c.type === "date" || c.type === "timestamp") ?? result.columns.find((c) => c.type === "text");
  const y = result.columns.find((c) => isNumberColumn(c) && c.name !== x?.name);
  return x && y ? { source, type: x.type === "text" ? "bar" : "line", x: x.name, y: [y.name] } : null;
}

/** A value as a cell shows it (the API's exact text; booleans and lists in words). */
export function shown(v: SqlValue): string {
  if (v === null) return "";
  if (Array.isArray(v)) return `{${v.map(shown).join(", ")}}`;
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

const parsed = (v: SqlValue): number | null => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

/** What a chart draws: one series per y column over the x column's values, in x order for dates
 * and numbers, as the rows came for anything else (the query's ORDER BY). A repeated x keeps its
 * last row. Problems (a column the result no longer has, a y that isn't numeric) are said, never
 * guessed around. */
export function chartSeries(result: SqlResult, chart: NotebookChart): { series: Series[]; problem: string | null } {
  const xi = result.columns.findIndex((c) => c.name === chart.x);
  if (xi < 0) return { series: [], problem: `The result has no column ${chart.x || "for the x axis"}: pick one.` };
  const xcol = result.columns[xi]!;
  const ys = chart.y.map((y) => ({ y, i: result.columns.findIndex((c) => c.name === y) }));
  const missing = ys.filter((y) => y.i < 0).map((y) => y.y);
  if (!ys.length) return { series: [], problem: "Pick a column of numbers to draw." };
  if (missing.length) return { series: [], problem: `The result has no column ${missing.join(", ")}: pick again.` };
  const notNumbers = ys.filter((y) => !isNumberColumn(result.columns[y.i]!)).map((y) => y.y);
  if (notNumbers.length) return { series: [], problem: `${notNumbers.join(", ")} ${notNumbers.length === 1 ? "isn't" : "aren't"} numbers.` };
  const ordered = xcol.type === "date" || xcol.type === "timestamp" || xcol.type === "number";
  const byX = new Map<string, SqlValue[]>();
  for (const row of result.rows) {
    const x = row[xi];
    if (x === null || x === undefined) continue;
    byX.set(shown(x), row);
  }
  let keys = [...byX.keys()];
  if (ordered) keys = keys.sort((a, b) => (xcol.type === "number" ? Number(a) - Number(b) : a < b ? -1 : a > b ? 1 : 0));
  const series = ys.map(({ y, i }, n): Series => ({
    id: n + 1,
    name: y,
    unit: "number",
    color: n,
    type: chart.type,
    axis: "L",
    points: keys.map((x, key): Point => {
      const v = byX.get(x)![i]!;
      return { key, period: x, periodEnd: x, y: parsed(v), raw: shown(v), sources: [] };
    }),
  }));
  return { series, problem: keys.length ? null : "The result has no rows to draw." };
}

/** The result as CSV (RFC 4180): the API's exact values, a header of column names. */
export function csvOf(result: SqlResult): string {
  const field = (s: string) => (/[",\r\n]/.test(s) || /^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  // a value a spreadsheet would run as a formula is written as text (CSV injection)
  const safe = (s: string) => (/^[=+\-@\t\r]/.test(s) && Number.isNaN(Number(s)) ? `'${s}` : s);
  const lines = [result.columns.map((c) => field(c.name)).join(",")];
  for (const row of result.rows) lines.push(row.map((v) => field(safe(shown(v)))).join(","));
  return lines.join("\r\n") + "\r\n";
}

/** "1,204 rows in 0.08 s" and why it stopped, as one line. */
export function resultLine(r: SqlResult): string {
  const rows = `${r.row_count.toLocaleString("en-US")} ${r.row_count === 1 ? "row" : "rows"}`;
  const time = `${r.seconds < 0.01 ? "under 0.01" : r.seconds.toFixed(2)} s`;
  const cut = r.truncated === "rows" ? `: the first ${r.max_rows.toLocaleString("en-US")}; add a LIMIT or narrow the query`
    : r.truncated === "size" ? ": as many as fit in one answer; select fewer columns" : "";
  return `${rows} in ${time}${cut}`;
}

/** What a refusal's code means for the user, before the API's own words. */
export const REFUSAL_WORDS: Record<string, string> = {
  refused: "This query can't run here",
  sql_error: "Postgres couldn't run it",
  timeout: "It took too long",
  too_costly: "It would read too much",
  too_large: "It needed too much space",
  too_wide: "Too many columns",
  busy: "A table was busy",
  busy_user: "One query at a time",
  busy_slots: "The workspace is busy",
  budget: "Today's query time is used",
};

/** The line of the query a syntax error's 0-based position falls on (1-based), and its column. */
export function lineOf(sql: string, position: number): { line: number; column: number } {
  const before = sql.slice(0, Math.max(0, position));
  const lines = before.split("\n");
  return { line: lines.length, column: lines.at(-1)!.length + 1 };
}
