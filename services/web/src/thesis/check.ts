/** "Check my thesis" (agent-api /v1/thesis-checks, thesis_check.py): pure helpers for the
 * results panel. The check is written by the assistant, so every piece of its text is rendered
 * through markdown.ts `renderAnswer` (DOMPurify; accession numbers link only when a tool
 * returned them, unverified figures are marked) and quotes are matched as plain text. */

import { leftText, tightest } from "../assistant/useAllowance";
import { money } from "../format";
import type { Allowance, CheckCitation, CheckFigure, Citation, ClaimStatus, ModelMatch, ThesisCheckResult } from "../types";

export const STATUS_LABELS: Record<ClaimStatus, string> = {
  supported: "Supported",
  contradicted: "Contradicted",
  unverifiable: "Can't verify",
  model: "Your model",
};

/** What a check costs and how long it usually takes, said before it starts (analyst test 4,
 * finding 22), in agent-api's numbers (`/v1/allowance` check_questions, check_seconds): never a
 * second copy here. Without the allowance it says no number. */
export function checkCost(a: Allowance | null): string {
  if (!a) return "A check uses several of your assistant questions and takes a few minutes.";
  const minutes = a.check_seconds ? Math.max(1, Math.round(a.check_seconds / 60)) : null;
  const time = minutes ? ` and usually takes about ${minutes} minute${minutes === 1 ? "" : "s"}` : "";
  return `A check counts as ${a.check_questions} questions (you have ${leftText(a)})${time}.`;
}

/** The same, short, beside the button while no check panel shows: "Uses 4 of your 28 questions ·
 * about 2 min" (the tighter of the hour's and the day's allowance). Null without the allowance. */
export function checkCostShort(a: Allowance | null): string | null {
  if (!a) return null;
  const minutes = a.check_seconds ? Math.max(1, Math.round(a.check_seconds / 60)) : null;
  return `Uses ${a.check_questions} of your ${tightest(a).left} questions${minutes ? ` · about ${minutes} min` : ""}`;
}

/** "0:42" */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A `model` claim's figure as the model shows it: "FY2027E revenue", "$18.75B" (money in the
 * model's currency, per-share amounts to the cent), "21.0%", "29.2x". */
export function matchLabel(m: ModelMatch): string {
  return `${m.fy ? `FY${m.fy}E ` : ""}${m.line}`;
}

export function matchValue(m: ModelMatch, currency = "USD"): string {
  if (m.kind === "percent") return `${m.value.toFixed(1)}%`;
  if (m.kind === "multiple") return `${m.value.toFixed(1)}x`;
  return money(m.value, Math.abs(m.value) < 1000 ? `${currency}/shares` : currency);
}

/** The figure as the thesis wrote it, when that reads differently from the model's ("29x" for
 * 29.2x); null when it is the same number written another way ("$99.9B" for $99.90B). */
export function wroteOtherwise(m: ModelMatch, currency = "USD"): string | null {
  const parts = (s: string) => {
    const x = s.match(/([\d,]+(?:\.\d+)?)\s*([A-Za-z%]*)/);
    return x ? `${Number(x[1]!.replace(/,/g, ""))}${x[2]!.toLowerCase()}` : s;
  };
  return parts(m.figure) === parts(matchValue(m, currency)) ? null : m.figure;
}

/** What the stale notice compares (agent-api thesis_check.text_hash): SHA-256 of the company
 * id (or nothing), a newline and the Markdown, as UTF-8, in hex. */
export async function textHash(companyId: string | null, markdown: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${companyId ?? ""}\n${markdown}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Lower-case, curly quotes and dashes made plain, whitespace runs as one space; `at` maps each
 * kept character back to its index in the original. */
export function normalize(text: string): { text: string; at: number[] } {
  let out = "";
  const at: number[] = [];
  let space = false;
  for (let i = 0; i < text.length; i++) {
    let ch = text[i]!;
    if (/\s/.test(ch)) {
      if (!space && out) {
        out += " ";
        at.push(i);
      }
      space = true;
      continue;
    }
    space = false;
    if (ch === "‘" || ch === "’") ch = "'";
    else if (ch === "“" || ch === "”") ch = '"';
    else if (ch === "–" || ch === "—") ch = "-";
    out += ch.toLowerCase();
    at.push(i);
  }
  return { text: out, at };
}

/** Where a claim's quote is in the thesis text (start, end in the original), or null. Matching
 * ignores case, whitespace and curly quotes, like the agent's `quote_found`. */
export function findQuote(text: string, quote: string): { start: number; end: number } | null {
  const q = normalize(quote).text.trim();
  if (!q) return null;
  const n = normalize(text);
  const i = n.text.indexOf(q);
  if (i < 0) return null;
  return { start: n.at[i]!, end: n.at[i + q.length - 1]! + 1 };
}

/** Removes earlier highlights from `root`. */
export function clearHighlights(root: HTMLElement): void {
  for (const m of [...root.querySelectorAll("mark.claim-mark")]) {
    m.replaceWith(document.createTextNode(m.textContent ?? ""));
  }
  root.normalize();
}

/** Wraps the quote's text in `root` (across inline elements: **bold** in the middle is fine)
 * in <mark class="claim-mark">; returns the first mark, or null when it isn't there. Only text
 * nodes change; nothing is parsed as HTML. */
export function highlightQuote(root: HTMLElement, quote: string): HTMLElement | null {
  clearHighlights(root);
  const nodes: Text[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  const starts: number[] = [];
  let all = "";
  for (const t of nodes) {
    starts.push(all.length);
    all += t.data;
  }
  const hit = findQuote(all, quote);
  if (!hit) return null;
  let first: HTMLElement | null = null;
  nodes.forEach((t, k) => {
    const from = Math.max(hit.start, starts[k]!) - starts[k]!;
    const to = Math.min(hit.end, starts[k]! + t.data.length) - starts[k]!;
    if (to <= from || !t.data.slice(from, to).trim()) return;
    const range = document.createRange();
    range.setStart(t, from);
    range.setEnd(t, to);
    const mark = document.createElement("mark");
    mark.className = "claim-mark";
    range.surroundContents(mark);
    first ??= mark;
  });
  return first;
}

/** A claim's or point's citations as chat citations, for renderAnswer: only filings a tool
 * returned link to EDGAR; the rest stay flagged. */
export function asCitations(cites: CheckCitation[]): Citation[] {
  return cites
    .filter((c) => c.verified)
    .map((c) => ({
      accession_no: c.accession_no,
      form: c.form ?? null,
      filed: c.filed ?? null,
      company_id: c.company_id ?? null,
      url: c.url ?? null,
      sections: c.section_key ? [c.section_key] : [],
    }));
}

/** Figures in a piece of text that weren't found in the data (renderAnswer marks them). */
export const unverifiedFigures = (figures: CheckFigure[]) => figures.filter((f) => f.status === "unverified").map((f) => f.text);

/** "+15.0 pts", "−3.2 pts" */
export function points(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} pts`;
}

/** A quote from the thesis as it reads, without the Markdown it was written in ("**Export
 * controls** matter"). Display only: finding it in the thesis still uses the quote as given. */
export function plainQuote(quote: string): string {
  return quote
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[\s(])[*_]([^\s*_][^*_]*?)[*_](?=[\s).,;:!?]|$)/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*(#{1,6}\s+|>\s+|[-*+]\s+)/, "");
}

/** Problems worth a line under the result: unverified figures (each counted once, as the
 * warning names them) and unknown filings. */
export function problems(r: ThesisCheckResult): string[] {
  const bad = new Set(unverifiedFigures(r.verification.figures)).size;
  const unknown = r.verification.unverified_accessions.length;
  return [
    // the check's own explanations, not the thesis (Riley 12: "$1,332M not found" in a thesis that never wrote it)
    bad ? `${bad} figure${bad === 1 ? "" : "s"} in the check's explanations not found in the data` : "",
    unknown ? `${unknown} cited filing${unknown === 1 ? "" : "s"} the assistant never read` : "",
  ].filter(Boolean);
}
