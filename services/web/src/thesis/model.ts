/** Theses: a research note the user writes in Markdown, saved as a workspace item of kind
 * "thesis" (app DB 009, validated by query_api/workspaces.py ThesisState). The item's name is
 * its title; the state is `{companyId?, markdown}`.
 *
 * Embeds reuse the reports' system: a line `[[embed:<item id>]]` draws an item saved in the
 * same workspace (a chart, comps set, screen or model) in place, from its saved state, reading
 * the data through the Query API as the viewer. Anything else (an id that isn't in this
 * workspace, another kind, a malformed marker) is dropped, never shown or fetched. The text is
 * rendered only through markdown.ts (marked + DOMPurify): a thesis is untrusted like any
 * imported file. Pure functions, tested without rendering.
 *
 * Export and import round-trip (#41, M6): `thesisMarkdown` writes each embed as a readable block
 * (what it is and what it shows, from its saved state) between `<!-- tributary:embed <id> -->`
 * and `<!-- /tributary:embed -->`, and the last check (date, stale or not, each claim's status,
 * explanation and filing) between `<!-- tributary:check -->` and `<!-- /tributary:check -->`.
 * `importMarkdown` turns each embed block back into its `[[embed:<id>]]` line and drops the
 * check block (it's the check's, not the user's text). HTML comments don't show in a Markdown
 * viewer; text written into the blocks has "<!--" and "-->" removed so it can't close them. An
 * embed only draws when its id is an item of the same workspace, as for anything typed. */

import type { ItemKind } from "../types";

export const MAX_MARKDOWN = 100_000;
/** an imported file larger than this isn't read at all (a thesis is at most MAX_MARKDOWN
 * characters; UTF-8 can take up to 4 bytes each, but Markdown is mostly ASCII) */
export const MAX_IMPORT_BYTES = 400_000;
export const EMBEDDABLE: ReadonlySet<ItemKind> = new Set<ItemKind>(["chart", "comps", "screen", "model"]);
export const EMBED_LABELS: Partial<Record<ItemKind, string>> = { chart: "Chart", comps: "Comps", screen: "Screen", model: "Model" };

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const EMBED = new RegExp(`^\\[\\[embed:(${UUID})\\]\\]$`);
const CIK = /^\d{10}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f]/g;

export interface Thesis {
  companyId: string | null;
  markdown: string;
}

/** An item a thesis can embed: one saved in the same workspace, with its state. */
export interface EmbedItem {
  item_id: string;
  kind: ItemKind;
  name: string;
  state: Record<string, unknown>;
}

export type Segment = { type: "text"; markdown: string } | { type: "embed"; item: EmbedItem };

/** A thesis's state from the API (untrusted). */
export function restoreThesis(raw: unknown): Thesis {
  const o = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return {
    companyId: typeof o.companyId === "string" && CIK.test(o.companyId) ? o.companyId : null,
    markdown: typeof o.markdown === "string" ? cleanText(o.markdown).slice(0, MAX_MARKDOWN) : "",
  };
}

/** The state as saved. */
export function thesisState(t: Thesis): Record<string, unknown> {
  return { ...(t.companyId ? { companyId: t.companyId } : {}), markdown: t.markdown };
}

/** Newlines as \n, no control characters but newlines and tabs (what the API accepts). */
export function cleanText(text: string): string {
  return text.replace(/^﻿/, "").replace(/\r\n?/g, "\n").replace(CONTROL, "");
}

export const embedMarker = (itemId: string) => `[[embed:${itemId}]]`;

/** The Markdown split at its embed lines. An embed resolves only to an embeddable item of this
 * workspace (`items`), each at most once; any other marker line is dropped. */
export function thesisSegments(markdown: string, items: EmbedItem[]): Segment[] {
  const byId = new Map(items.filter((i) => EMBEDDABLE.has(i.kind)).map((i) => [i.item_id, i]));
  const out: Segment[] = [];
  const seen = new Set<string>();
  let text: string[] = [];
  const flush = () => {
    const md = text.join("\n").trim();
    if (md) out.push({ type: "text", markdown: md });
    text = [];
  };
  for (const line of markdown.split("\n")) {
    const trimmed = line.trim();
    const m = EMBED.exec(trimmed);
    if (!m) {
      // a marker-looking line that isn't a valid embed is dropped too, not shown as text
      if (!/^\[\[\s*embed\s*:/i.test(trimmed)) text.push(line);
      continue;
    }
    const item = byId.get(m[1]!);
    if (!item || seen.has(item.item_id)) continue;
    seen.add(item.item_id);
    flush();
    out.push({ type: "embed", item });
  }
  flush();
  return out;
}

/** Inserts an embed marker on a line of its own at the cursor; returns the text and where the
 * cursor goes (after the marker's line). */
export function insertEmbed(text: string, at: number, itemId: string): { text: string; cursor: number } {
  const pos = Math.max(0, Math.min(at, text.length));
  const before = text.slice(0, pos);
  const after = text.slice(pos);
  const lead = before === "" || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
  const trail = after.startsWith("\n\n") ? "" : after.startsWith("\n") ? "\n" : "\n\n";
  const inserted = `${lead}${embedMarker(itemId)}${trail}`;
  return { text: before + inserted + after, cursor: pos + inserted.length };
}

/** The ids embedded in the text, in order (valid markers only). */
export function embeddedIds(markdown: string): string[] {
  return markdown.split("\n").flatMap((l) => {
    const m = EMBED.exec(l.trim());
    return m ? [m[1]!] : [];
  });
}

const yamlString = (s: string) => `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, " ")}"`;

/** Text written into an export block: one line, and nothing that opens or closes a comment. */
export const blockText = (s: string) => s.replace(/<!--|-->/g, "").replace(/\s+/g, " ").trim();

const EMBED_OPEN = (id: string) => `<!-- tributary:embed ${id} -->`;
const EMBED_CLOSE = "<!-- /tributary:embed -->";
const CHECK_OPEN = "<!-- tributary:check -->";
const CHECK_CLOSE = "<!-- /tributary:check -->";
const EMBED_BLOCK = new RegExp(`^<!-- tributary:embed (${UUID}) -->\\n[\\s\\S]*?^<!-- /tributary:embed -->[ \\t]*$`, "gm");
const CHECK_BLOCK = /^<!-- tributary:check -->\n[\s\S]*?^<!-- \/tributary:check -->[ \t]*\n?/m;

/** The thesis as a Markdown file: a front-matter header (title, company, date), then the text
 * with each embed as a readable block that imports back as the embed (unknown embeds dropped, as
 * on screen), then the last check when there is one. */
export function thesisMarkdown(opts: {
  title: string;
  thesis: Thesis;
  /** "NVIDIA (NVDA)" or the like, when the thesis names a company */
  company: string | null;
  /** YYYY-MM-DD */
  date: string;
  items: EmbedItem[];
  /** what an embedded item shows, a line each (thesis/export.ts `embedSummary`) */
  describe?: (item: EmbedItem) => string[];
  /** the last check, as lines of Markdown (thesis/export.ts `checkSection`) */
  check?: string[] | null;
}): string {
  const { title, thesis, company, date, items, describe, check } = opts;
  const head = [
    "---",
    `title: ${yamlString(title)}`,
    ...(company ? [`company: ${yamlString(company)}`] : []),
    ...(thesis.companyId ? [`cik: "${thesis.companyId}"`] : []),
    `date: ${date}`,
    "source: Tributary thesis",
    "---",
  ].join("\n");
  const body = thesisSegments(thesis.markdown, items)
    .map((s) => {
      if (s.type === "text") return s.markdown;
      const lines = [`**${EMBED_LABELS[s.item.kind] ?? "Item"}: ${blockText(s.item.name)}**`, ...(describe?.(s.item) ?? []).map(blockText)];
      return [EMBED_OPEN(s.item.item_id), ...lines.map((l, i) => `> ${l}${i < lines.length - 1 ? "  " : ""}`), EMBED_CLOSE].join("\n");
    })
    .join("\n\n");
  const tail = check?.length ? `\n\n${CHECK_OPEN}\n${check.join("\n")}\n${CHECK_CLOSE}` : "";
  return `${head}\n\n${body}${tail}\n`;
}

export type Imported = { ok: true; markdown: string; title: string | null } | { ok: false; error: string };

/** A file's text as a thesis body: plain text only (a NUL byte means it isn't), newlines
 * normalized, control characters dropped, a leading front-matter block (as Export writes)
 * removed and its title offered. Rendering still goes through DOMPurify, so HTML in the file is
 * sanitized like anything typed. */
export function importMarkdown(raw: string): Imported {
  if (raw.includes("\u0000") || raw.includes("�")) {
    return { ok: false, error: "That file isn't plain text. Import a .md or .txt file." };
  }
  let text = cleanText(raw);
  let title: string | null = null;
  const fm = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (fm && fm[1]!.split("\n").length <= 40) {
    const t = /^title:\s*(.*)$/m.exec(fm[1]!)?.[1]?.trim();
    if (t) title = t.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1").replace(/\\"/g, '"').replace(/\\\\/g, "\\").trim().slice(0, 120) || null;
    text = text.slice(fm[0].length).replace(/^\n+/, "");
  }
  // an export's own blocks: embeds back to their lines, the check (not the user's text) dropped
  if (CHECK_BLOCK.test(text)) text = `${text.replace(CHECK_BLOCK, "").trimEnd()}\n`;
  text = text.replace(EMBED_BLOCK, (_, id: string) => embedMarker(id));
  if (text.length > MAX_MARKDOWN) {
    return { ok: false, error: `That file is too long: a thesis holds up to ${MAX_MARKDOWN.toLocaleString("en-US")} characters.` };
  }
  return { ok: true, markdown: text, title };
}
