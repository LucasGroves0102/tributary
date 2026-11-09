/** A section's stored text as the reader shows it: paragraphs and tables, without page debris
 * (#178, #179). Pure, display only: offsets index into the stored text, which is unchanged (the
 * parser's html_to_text puts one table row per line, its cells joined by a space, so a table can be
 * read back from the text without a reparse; services/parser README "Tables in the text").
 *
 * The rules, in order, over the text's lines (split at "\n", each with its offsets):
 *  1. Debris: a line that is only "Table of Contents" (or "Index", "Return to Table of Contents",
 *     "Back to Contents"), and a page number: a line of 1-3 digits ("12", "- 12 -", "Page 12",
 *     "F-12", or i-xx in lower case) when the next line is such a contents link, the previous line
 *     ends a sentence (. ! ? : ;) or there is no previous line. Both are dropped; text around them
 *     joins up again by rule 7 ("procure sufficient" / "foundry capacity").
 *  2. Cells: from a line's end, numeric cells: a number (1,234 / 4.93 / (259) / -3 / .5) with an
 *     optional "$" or "($" token before it and ")", "%" or "pts" tokens after it (the HTML often puts
 *     each in its own cell), or a dash (— – -), n/a, nm, or a change in words (Up, Down, Flat). What
 *     is left in front is the label.
 *  3. A date heading: a line made of 1-6 dates ("Jan 25, 2026") or years ("2025"), possibly after a
 *     label of at most 40 characters ("Year Ended").
 *  4. Rows: a line whose next line is only cells (a label too long for its cell wraps the values onto
 *     the next line) is one row: the whole line is the label ("Balances as of Jan 29, 2023" too; only
 *     a date heading without a label stays a heading). Otherwise a line with cells and a label
 *     of at most 160 characters not ending in "." is a row. A line of cells alone, not taken by a
 *     label, is a row without a label.
 *  5. Tables: a run of rows, date headings, debris and short lines (at most 120 characters, not
 *     ending in "." and not a bullet) with at least 3 rows, ending at its last row; a short line or
 *     heading inside it must have a row after it before any other line, and three lines in a row
 *     without figures end it (an MD&A's next table and its captions; a statement's sub-headings come
 *     in ones and twos: "Liabilities and Shareholders' Equity", "Current liabilities:"). Up to 4 short lines or
 *     headings straight before its first row belong to it (the statement's name, "(In millions)").
 *  6. Columns: the commonest number of cells among the table's rows (the larger on a tie). A row with
 *     more moves its first extra cells back into its label ("Balance at January 26, 2025 $ 1 $ 2" is
 *     two cells, not three); a row with fewer is aligned to the right. A date heading with that many
 *     dates is the table's column heads; any other heading or short line is a row with a label only.
 *  7. Everything else is paragraphs, as before tables (`text.ts` `paragraphs`): a new one at a blank
 *     line or a line starting with a capital or a bullet; any other line joins the paragraph before.
 */

import { countMatches } from "./text";

export interface TableRow {
  start: number;
  end: number;
  label: string;
  /** numeric cells, `columns` of them, "" where a short row has none; none: a label-only row */
  cells: string[];
}

export type Block =
  | { kind: "p"; start: number; end: number; text: string }
  | { kind: "table"; start: number; end: number; columns: number; heads: { label: string; cells: string[] } | null; rows: TableRow[] };

interface Line {
  start: number;
  end: number;
  text: string;
}

const CONTENTS = /^(?:return to |back to )?(?:the )?(?:table of contents|contents|index)\.?$/i;
const PAGE = /^(?:(?:page\s+)?(?:[-–—]\s*)?(?:[A-Z]{1,2}-)?\d{1,3}(?:\s*[-–—])?|[ivx]{1,5})$/i;
const LOWER_ROMAN = /^[ivx]{1,5}$/;
const SENTENCE_END = /[.!?:;]["”’)]*$/;
const NUMBER = /^\(?[-–—]?\$?\(?\d[\d,]*(?:\.\d+)?\)?%?$|^\(?\$?\.\d+\)?%?$/;
const DASH = /^(?:[-–—]+|n\/?a|nm|up|down|flat)$/i;
const AFTER = /^(?:\)|%|\)%|%\)|pts?)$/;
const BEFORE = /^(?:\$|\(\$|\$\(|\()$/;
const DATE = /(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\b(?:19|20)\d{2}\b/g;

function lines(text: string): Line[] {
  const out: Line[] = [];
  let at = 0;
  for (const raw of text.split("\n")) {
    out.push({ start: at, end: at + raw.length, text: raw.trim() });
    at += raw.length + 1;
  }
  return out;
}

function isPage(text: string): boolean {
  if (!PAGE.test(text)) return false;
  // roman numerals only in lower case ("ii"), never a word like "Mix" or "VI" heading
  return /\d/.test(text) || LOWER_ROMAN.test(text);
}

/** Rule 2: a line's trailing numeric cells and the label in front of them. */
export function splitCells(text: string): { label: string; cells: string[] } {
  const tokens = text.split(" ").filter(Boolean);
  const cells: string[] = [];
  let i = tokens.length - 1;
  while (i >= 0) {
    let j = i;
    let after = "";
    while (j >= 0 && AFTER.test(tokens[j]!)) after = tokens[j--]! + after;
    if (j < 0) break;
    const t = tokens[j]!;
    if (!NUMBER.test(t) && !DASH.test(t)) break;
    let cell = t + (after.startsWith("pt") ? ` ${after}` : after);
    j--;
    // "$", "(" or both, each in its own cell before the number
    for (let k = 0; k < 2 && j >= 0 && BEFORE.test(tokens[j]!) && NUMBER.test(t); k++) cell = tokens[j--]! + cell;
    cells.unshift(cell);
    i = j;
  }
  return { label: tokens.slice(0, i + 1).join(" "), cells };
}

/** Rule 3: the dates or years a heading line names, and the label before them; null: not one. */
export function dateHeads(text: string): { label: string; cells: string[] } | null {
  const found = [...text.matchAll(DATE)];
  if (!found.length || found.length > 6) return null;
  const first = found[0]!.index!;
  const label = text.slice(0, first).trim();
  let rest = text.slice(first);
  for (const m of found) rest = rest.replace(m[0], "");
  if (rest.trim() || label.length > 40) return null;
  return { label, cells: found.map((m) => m[0].replace(/\s+/g, " ")) };
}

type Item =
  | { t: "row"; start: number; end: number; label: string; cells: string[] }
  | { t: "head"; line: Line; heads: { label: string; cells: string[] } }
  | { t: "short"; line: Line }
  | { t: "text"; line: Line }
  | { t: "blank"; line: Line };

const isShort = (s: string) => s.length <= 120 && !/[.]$/.test(s) && !s.startsWith("•");

export function readerBlocks(text: string): Block[] {
  const ls = lines(text);
  // rule 1 (neighbours skip blank lines)
  const keep = ls.map((l, i) => {
    if (CONTENTS.test(l.text)) return false;
    if (!isPage(l.text)) return true;
    let n = i + 1;
    while (n < ls.length && !ls[n]!.text) n++;
    let p = i - 1;
    while (p >= 0 && !ls[p]!.text) p--;
    return !((n < ls.length && CONTENTS.test(ls[n]!.text)) || p < 0 || SENTENCE_END.test(ls[p]!.text));
  });
  const live = ls.filter((_, i) => keep[i]);
  // rules 2-4
  const items: Item[] = [];
  for (let i = 0; i < live.length; i++) {
    const l = live[i]!;
    if (!l.text) {
      items.push({ t: "blank", line: l });
      continue;
    }
    const heads = dateHeads(l.text);
    const own = splitCells(l.text);
    const next = live[i + 1];
    const nextCells = next?.text ? splitCells(next.text) : null;
    const plainHeads = heads && !heads.label;
    if (!plainHeads && nextCells && !nextCells.label && nextCells.cells.length && !/[.]$/.test(l.text) && l.text.length <= 300) {
      items.push({ t: "row", start: l.start, end: next!.end, label: l.text, cells: nextCells.cells });
      i++;
    } else if (heads) {
      items.push({ t: "head", line: l, heads });
    } else if (own.cells.length && (!own.label || (own.label.length <= 160 && !/[.]$/.test(own.label)))) {
      items.push({ t: "row", start: l.start, end: l.end, label: own.label, cells: own.cells });
    } else {
      items.push(isShort(l.text) ? { t: "short", line: l } : { t: "text", line: l });
    }
  }
  // rule 5: which items are tables
  const inTable = new Array<number>(items.length).fill(-1);
  let tableNo = 0;
  for (let i = 0; i < items.length; i++) {
    if (items[i]!.t !== "row" || inTable[i]! >= 0) continue;
    let last = i;
    let rows = 0;
    let between = 0; // lines without figures since the last row
    for (let j = i; j < items.length; j++) {
      const it = items[j]!;
      if (it.t === "row") {
        rows++;
        last = j;
        between = 0;
      } else if ((it.t !== "short" && it.t !== "head") || ++between >= 3) break;
    }
    if (rows < 3) continue;
    let first = i;
    for (let k = i - 1; k >= 0 && i - k <= 4; k--) {
      const t = items[k]!.t;
      if (t !== "short" && t !== "head") break;
      first = k;
    }
    for (let k = first; k <= last; k++) inTable[k] = tableNo;
    tableNo++;
    i = last;
  }
  // rules 6-7
  const out: Block[] = [];
  let para: Line[] = [];
  const flush = () => {
    if (!para.length) return;
    out.push({ kind: "p", start: para[0]!.start, end: para.at(-1)!.end, text: para.map((l) => l.text).join("\n") });
    para = [];
  };
  for (let i = 0; i < items.length; ) {
    const it = items[i]!;
    if (inTable[i]! < 0) {
      const line = it.t === "row" ? { start: it.start, end: it.end, text: text.slice(it.start, it.end).trim() } : it.line;
      if (it.t === "blank") flush();
      else {
        if (para.length && /^[A-Z•]/.test(line.text)) flush();
        para.push(line);
      }
      i++;
      continue;
    }
    flush();
    const no = inTable[i]!;
    const group: Item[] = [];
    while (i < items.length && inTable[i] === no) group.push(items[i++]!);
    out.push(table(group));
  }
  flush();
  return out;
}

function table(group: Item[]): Block {
  const counts = new Map<number, number>();
  for (const it of group) if (it.t === "row" && it.cells.length) counts.set(it.cells.length, (counts.get(it.cells.length) ?? 0) + 1);
  const columns = [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ?? 1;
  let heads: { label: string; cells: string[] } | null = null;
  const rows: TableRow[] = [];
  const start = group[0]!.t === "row" ? group[0]!.start : group[0]!.line.start;
  const lastItem = group.at(-1)!;
  const end = lastItem.t === "row" ? lastItem.end : lastItem.line.end;
  for (const it of group) {
    if (it.t === "row") {
      let { label, cells } = it;
      if (cells.length > columns) {
        const extra = cells.length - columns;
        label = [label, ...cells.slice(0, extra)].filter(Boolean).join(" ");
        cells = cells.slice(extra);
      }
      rows.push({ start: it.start, end: it.end, label, cells: [...new Array<string>(columns - cells.length).fill(""), ...cells] });
    } else if (it.t === "head" && !heads && !rows.some((r) => r.cells.length) && it.heads.cells.length === columns) {
      heads = it.heads;
    } else if (it.t === "head" || it.t === "short") {
      rows.push({ start: it.line.start, end: it.line.end, label: it.line.text, cells: [] });
    }
  }
  return { kind: "table", start, end, columns, heads, rows };
}

/** The pieces of a block the reader highlights in turn: a paragraph's text; a table's heads, then
 * each row's label and cells. Counting and stepping through highlights both read these. */
export function blockUnits(b: Block): string[] {
  if (b.kind === "p") return [b.text];
  return [...(b.heads ? [b.heads.label, ...b.heads.cells] : []), ...b.rows.flatMap((r) => [r.label, ...r.cells])];
}

/** The block holding a section-relative offset, or the nearest after it; -1 past the end. */
export function blockAt(blocks: Block[], offset: number): number {
  return blocks.findIndex((b) => offset < b.end);
}

/** Highlights in a block, as `highlight` marks them unit by unit. */
export function blockMatches(b: Block, terms: string[]): number {
  return blockUnits(b).reduce((n, u) => n + countMatches(u, terms), 0);
}

/** The index (among all highlights of these blocks) of the first highlight in block `target` or
 * after it: where a cited passage opens. 0 without a target or a highlight after it. */
export function firstBlockMatch(blocks: Block[], terms: string[], target: number): number {
  if (target < 0) return 0;
  let seen = 0;
  for (let i = 0; i < blocks.length; i++) {
    const here = blockMatches(blocks[i]!, terms);
    if (i >= target && here > 0) return seen;
    seen += here;
  }
  return 0;
}
