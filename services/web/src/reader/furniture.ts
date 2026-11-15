/** Page furniture in a filing's stored text: page numbers, "Table of Contents" back-links and
 * running headers, the lines a printed page puts between two halves of a sentence. The stored text
 * keeps them (every offset into it stays: services/parser README "Page furniture"); what reads the
 * text leaves them out: the reader (blocks.ts rule 1), the Business step's paragraphs and every
 * pinned quote (analysis/project.ts). Search reads the same rule in SQL (migration 068
 * `gold.page_furniture_lines`); both are checked against `furniture.cases.json`.
 *
 * Over the text's non-blank lines (split at "\n", trimmed):
 *  1. A contents link: "Table of Contents", "Contents" or "Index", optionally after "Return to " or
 *     "Back to " and "the ", optionally ending in "." (any case).
 *  2. A page number: 1-3 digits ("12", "- 12 -", "Page 12", "F-12": up to two letters and a "-"
 *     before them, dashes around), or i-xxxxx in lower case; and the next line is a contents link,
 *     it is the first line, or the line before it ends a sentence (. ! ? : ; then any of " ” ’ ) ).
 *  3. A running header: any other line of 3-100 characters with an ASCII letter whose form with every
 *     run of digits as "#" occurs at least 3 times in the text, at least twice and at least half the
 *     time within two lines of a line of kinds 1-2.
 */

export const CONTENTS = /^(?:return to |back to )?(?:the )?(?:table of contents|contents|index)\.?$/i;
const PAGE = /^(?:page\s+)?(?:[-–—]\s*)?(?:[A-Z]{1,2}-)?[0-9]{1,3}(?:\s*[-–—])?$/i;
const LOWER_ROMAN = /^[ivx]{1,5}$/;
export const SENTENCE_END = /[.!?:;]["”’)]*$/;

/** Rule 2's shape of a page number, before its neighbours are looked at. */
export function isPage(text: string): boolean {
  return PAGE.test(text) || LOWER_ROMAN.test(text);
}

/** Which of these lines (already split; blank ones are never furniture) are page furniture. */
export function furnitureLines(lines: readonly string[]): boolean[] {
  const text = lines.map((l) => l.trim());
  const live = text.map((t, i) => (t ? i : -1)).filter((i) => i >= 0);
  const out = new Array<boolean>(lines.length).fill(false);
  // rules 1-2, neighbours among the non-blank lines
  const pageish = live.map((i, k) => {
    const t = text[i]!;
    if (CONTENTS.test(t)) return true;
    if (!isPage(t)) return false;
    const next = k + 1 < live.length ? text[live[k + 1]!]! : null;
    const prev = k > 0 ? text[live[k - 1]!]! : null;
    return (next !== null && CONTENTS.test(next)) || prev === null || SENTENCE_END.test(prev);
  });
  // rule 3: repeated lines that sit by a page break
  const seen = new Map<string, number>();
  const byPage = new Map<string, number>();
  const masked = live.map((i) => text[i]!.replace(/[0-9]+/g, "#"));
  live.forEach((_, k) => {
    const m = masked[k]!;
    seen.set(m, (seen.get(m) ?? 0) + 1);
    let near = false;
    for (let j = Math.max(0, k - 2); j <= Math.min(live.length - 1, k + 2); j++) near ||= pageish[j]!;
    if (near) byPage.set(m, (byPage.get(m) ?? 0) + 1);
  });
  live.forEach((i, k) => {
    if (pageish[k]) {
      out[i] = true;
      return;
    }
    const t = text[i]!;
    const m = masked[k]!;
    const n = seen.get(m) ?? 0;
    const b = byPage.get(m) ?? 0;
    out[i] = t.length >= 3 && t.length <= 100 && /[A-Za-z]/.test(t) && n >= 3 && b >= 2 && 2 * b >= n;
  });
  return out;
}

/** [start, end) of each furniture line of a text, in order. */
export function furnitureSpans(text: string): [number, number][] {
  const lines = text.split("\n");
  const flags = furnitureLines(lines);
  const out: [number, number][] = [];
  let at = 0;
  lines.forEach((l, i) => {
    if (flags[i]) out.push([at, at + l.length]);
    at += l.length + 1;
  });
  return out;
}

/** The text without its furniture lines (the others joined by "\n" as before). */
export function withoutFurniture(text: string): string {
  const lines = text.split("\n");
  const flags = furnitureLines(lines);
  return lines.filter((_, i) => !flags[i]).join("\n");
}
