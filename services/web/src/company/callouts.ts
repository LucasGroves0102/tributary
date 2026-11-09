/** Statement callouts in words (F5, #106, #112). The API decides which quarters are called out
 * (query_api/notes.py `callouts`: 041's outlier and sign_flip tests, the newest quarter against the two
 * before it, `seasonal` by the data notes' rule); this file only words them, never re-derives one.
 *
 * Wording is neutral: a callout says what a figure is next to and where to read why, never that it is
 * wrong. Figures read with the statement's own sign (an outflow line shows them negative). A row whose
 * sign changes in SWING or more of the shown quarters is said once, for the row (JPMorgan's operating
 * cash flow), instead of once per quarter. */

import { money } from "../format";
import type { Callout } from "../types";
import type { MetricTable, TableCell } from "./data";

/** sign changes in this many shown quarters of one row read as a row that swings */
export const SWING = 3;

export interface CalloutLine {
  key: string;
  metric: string;
  text: string;
  /** the cells it is about, newest last: each value opens its filing */
  cells: { label: string; cell: TableCell }[];
}

const times = (f: number | null) => (f === null ? "" : `${f >= 10 ? Math.round(f) : f.toFixed(1)} times`);

/** One callout as a sentence, for the cell's hover and the list under the table. `sign` is the row's
 * display sign (-1 on an outflow line). */
export function calloutText(c: Callout, label: string, period: string, sign = 1): string {
  const v = (x: number) => money(sign * x, "USD");
  const [a, b] = c.beside.map((x) => v(x.value));
  const against = c.edge ? "the two quarters before it" : "the quarters either side";
  if (c.kind === "outlier") {
    return `${label}, ${period}: ${v(c.value)}, ${times(c.factor)} ${against} (${a} and ${b}).` +
      (c.seasonal ? " The same quarter peaks in other years too: it's the company's pattern." : " The filing says why.");
  }
  return `${label}, ${period}: ${v(c.value)}, the other sign from ${against} (${a} and ${b}). The filing says why.`;
}

/** The callouts of a table's shown cells, one line each, or one for a row that swings. */
export function calloutLines(table: MetricTable): CalloutLine[] {
  const out: CalloutLine[] = [];
  for (const row of table.rows) {
    const sign = row.opts?.outflow ? -1 : 1;
    const marked = row.cells.flatMap((cell, i) => (cell?.callout ? [{ cell, i }] : []));
    const flips = marked.filter((m) => m.cell.callout!.kind === "sign");
    const swings = flips.length >= SWING;
    if (swings) {
      const shown = row.cells.flatMap((c) => (c && Number.isFinite(Number(c.raw)) && c.raw !== "" ? [Number(c.raw)] : []));
      const lo = Math.min(...shown);
      const hi = Math.max(...shown);
      out.push({
        key: `${row.metric}|swings`,
        metric: row.metric,
        text: `${row.label} changes sign in ${flips.length} of these ${row.cells.filter(Boolean).length} quarters, ` +
          `from ${money(lo, "USD")} to ${money(hi, "USD")}: read any one quarter with the ones beside it.`,
        cells: flips.map(({ cell, i }) => ({ label: table.columns[i]!.label, cell })),
      });
    }
    for (const { cell, i } of marked) {
      if (swings && cell.callout!.kind === "sign") continue;
      out.push({
        key: `${row.metric}|${table.columns[i]!.periodEnd}`,
        metric: row.metric,
        text: calloutText(cell.callout!, row.label, table.columns[i]!.label, sign),
        cells: [{ label: table.columns[i]!.label, cell }],
      });
    }
  }
  return out;
}
