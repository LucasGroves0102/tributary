/** Theses: a research note the user writes in Markdown, saved as a workspace item of kind
 * "thesis" (app DB 009, validated by query_api/workspaces.py ThesisState). The item's name is
 * its title; the state is `{companyId?, markdown}`.
 *
 * Embeds reuse the reports' system: a line `[[embed:<item id>]]` draws an item saved in the
 * same workspace (a chart, comps set, screen or model) in place, from its saved state, reading
 * the data through the Query API as the viewer. Anything else (an id that isn't in this
 * workspace, another kind, a malformed marker) is dropped, never shown or fetched. The text is
 * rendered only through markdown.ts (marked + DOMPurify): a thesis is untrusted like any
 * imported file. Pure functions, tested without rendering. */

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

/** The thesis as a Markdown file: a front-matter header (title, company, date), then the text
 * with each embed as a readable line `[Chart: <name>]` (unknown embeds dropped, as on screen). */
export function thesisMarkdown(opts: {
  title: string;
  thesis: Thesis;
  /** "NVIDIA (NVDA)" or the like, when the thesis names a company */
  company: string | null;
  /** YYYY-MM-DD */
  date: string;
  items: EmbedItem[];
}): string {
  const { title, thesis, company, date, items } = opts;
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
    .map((s) => (s.type === "text" ? s.markdown : `[${EMBED_LABELS[s.item.kind] ?? "Item"}: ${s.item.name}]`))
    .join("\n\n");
  return `${head}\n\n${body}\n`;
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
  if (text.length > MAX_MARKDOWN) {
    return { ok: false, error: `That file is too long: a thesis holds up to ${MAX_MARKDOWN.toLocaleString("en-US")} characters.` };
  }
  return { ok: true, markdown: text, title };
}
