/** The thesis editor's blocks (F6, wishlist B.8): the Markdown a thesis is saved as, read as a
 * list of units so the editor can draw embeds and pinned quotes as cards that move and go,
 * while the Markdown stays the one storage format (export, import, versions and drafts read and
 * write exactly what they did). Pure, tested without rendering.
 *
 * Units cover every line, in order, so `lines.join("\n")` is always the Markdown byte for byte:
 * - embed: a line that is exactly `[[embed:<item id>]]` (spaces around allowed, as
 *   thesisSegments reads it), one line;
 * - quote: a pinned filing passage (analysis/project.ts quoteBlock, quoteLines, quoteTable): a
 *   run of lines starting with ">" whose last line is the pin's citation ("> — 10-Q for the
 *   quarter ended …, filed …, Item 2. MD&A · [accession](EDGAR)"); a quote someone typed without
 *   that line is text;
 * - blank: a run of empty lines; text: a run of other lines (a paragraph, a heading, a list), a
 *   fenced code block whole, blank lines and all.
 * The editor shows each run of text and blank units as one text box and each embed or quote as a
 * card. Moving a card swaps it with the nearest non-blank unit above or below (a paragraph,
 * another card), keeping the blank lines between them where they were; removing one also drops
 * one blank line when that would leave two. Nothing else is rewritten. */

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const EMBED = new RegExp(`^\\[\\[embed:(${UUID})\\]\\]$`);
const QUOTE_LINE = /^ {0,3}>/;
// the pin's citation line (project.ts citationLine): form, "filed <date>", section, accession
const CITATION = /^ {0,3}>\s*— \S.*\bfiled \d{4}-\d{2}-\d{2}\b.* · (?:\[\d{10}-\d{2}-\d{6}\]\(https:\/\/www\.sec\.gov\/[^)\s]*\)|\d{10}-\d{2}-\d{6})\s*$/;
const FENCE = /^ {0,3}(```|~~~)/;

export type UnitKind = "text" | "blank" | "embed" | "quote";

export interface Unit {
  kind: UnitKind;
  /** lines [start, end) of the Markdown */
  start: number;
  end: number;
  /** an embed's item id */
  id?: string;
}

export const linesOf = (markdown: string): string[] => markdown.split("\n");

/** The units of a thesis's Markdown (the rules above). */
export function unitsOf(markdown: string): Unit[] {
  const lines = linesOf(markdown);
  const out: Unit[] = [];
  const push = (kind: UnitKind, start: number, end: number, id?: string) => {
    const last = out.at(-1);
    // runs of text lines (and of blank lines) are one unit each
    if (last && last.kind === kind && (kind === "text" || kind === "blank") && last.end === start) last.end = end;
    else out.push({ kind, start, end, ...(id ? { id } : {}) });
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (FENCE.test(line)) {
      // a fenced block is text to its closing fence (or the end), blank lines and all
      const mark = FENCE.exec(line)![1]!;
      let j = i + 1;
      while (j < lines.length && !lines[j]!.trimStart().startsWith(mark)) j++;
      push("text", i, Math.min(j + 1, lines.length));
      i = Math.min(j + 1, lines.length);
      continue;
    }
    const embed = EMBED.exec(line.trim());
    if (embed) {
      push("embed", i, i + 1, embed[1]);
      i++;
      continue;
    }
    if (QUOTE_LINE.test(line)) {
      let j = i;
      while (j < lines.length && QUOTE_LINE.test(lines[j]!)) j++;
      // a run of quote lines: each pinned passage ends at its citation line
      let from = i;
      for (let k = i; k < j; k++) {
        if (CITATION.test(lines[k]!)) {
          push("quote", from, k + 1);
          from = k + 1;
        }
      }
      if (from < j) push("text", from, j);
      i = j;
      continue;
    }
    push(line.trim() === "" ? "blank" : "text", i, i + 1);
    i++;
  }
  return out;
}

export type Segment =
  | { kind: "run"; start: number; end: number; ordinal: number }
  | { kind: "card"; unit: Unit; index: number; ordinal: number };

/** What the editor draws: runs of text and blank units (one text box each) and cards. */
export function segmentsOf(units: Unit[]): Segment[] {
  const out: Segment[] = [];
  let runs = 0;
  let cards = 0;
  units.forEach((u, index) => {
    if (u.kind === "embed" || u.kind === "quote") {
      out.push({ kind: "card", unit: u, index, ordinal: cards++ });
      return;
    }
    const last = out.at(-1);
    if (last?.kind === "run" && last.end === u.start) last.end = u.end;
    else out.push({ kind: "run", start: u.start, end: u.end, ordinal: runs++ });
  });
  return out;
}

export const runText = (markdown: string, start: number, end: number): string => linesOf(markdown).slice(start, end).join("\n");

/** The Markdown with lines [start, end) replaced by `text` (a text box's new value). */
export function replaceRun(markdown: string, start: number, end: number, text: string): string {
  const lines = linesOf(markdown);
  lines.splice(start, end - start, ...linesOf(text));
  return lines.join("\n");
}

/** Where line `n` starts, in characters. */
export function lineOffset(markdown: string, n: number): number {
  const lines = linesOf(markdown);
  let at = 0;
  for (let i = 0; i < n && i < lines.length; i++) at += lines[i]!.length + 1;
  return at;
}

/** The line a character offset is on. */
export function lineAt(markdown: string, offset: number): number {
  return markdown.slice(0, Math.max(0, offset)).split("\n").length - 1;
}

export interface Moved {
  markdown: string;
  /** the moved or removed card's first line now (where focus goes) */
  line: number;
}

/** A card (units[index]) one place up (-1) or down (1): swapped with the nearest unit that isn't
 * blank lines, the blank lines between them staying where they are. null at either end. */
export function moveUnit(markdown: string, units: Unit[], index: number, dir: -1 | 1): Moved | null {
  const card = units[index];
  if (!card) return null;
  let j = index + dir;
  while (units[j]?.kind === "blank") j += dir;
  const other = units[j];
  if (!other) return null;
  const lines = linesOf(markdown);
  const [a, b] = dir < 0 ? [other, card] : [card, other];
  const first = lines.slice(a.start, a.end);
  const between = lines.slice(a.end, b.start);
  const second = lines.slice(b.start, b.end);
  lines.splice(a.start, b.end - a.start, ...second, ...between, ...first);
  const line = dir < 0 ? a.start : a.start + second.length + between.length;
  return { markdown: lines.join("\n"), line };
}

/** A card removed, and one blank line with it when it sat between two (or at an end). */
export function removeUnit(markdown: string, units: Unit[], index: number): Moved | null {
  const card = units[index];
  if (!card) return null;
  const lines = linesOf(markdown);
  const before = card.start - 1;
  const after = card.end;
  const blank = (n: number) => n < 0 || n >= lines.length || lines[n]!.trim() === "";
  let from = card.start;
  let to = card.end;
  if (blank(before) && blank(after) && lines.length > to - from) {
    if (after < lines.length) to++;
    else if (before >= 0) from--;
  }
  lines.splice(from, to - from);
  return { markdown: lines.length ? lines.join("\n") : "", line: Math.min(from, Math.max(0, lines.length - 1)) };
}

/** What a pinned quote's citation says: its form, "earnings" for an 8-K release, the period. */
export function quoteInfo(markdown: string, unit: Unit): { form: string | null; earnings: boolean; period: string | null } {
  const last = linesOf(markdown)[unit.end - 1] ?? "";
  const m = /—\s*(\S+)\s/.exec(last);
  const form = m?.[1] ?? null;
  const period = /for the (quarter|fiscal year|period) ended (\d{4}-\d{2}-\d{2})/.exec(last);
  return { form, earnings: form === "8-K", period: period ? `${period[1]} ended ${period[2]}` : null };
}
