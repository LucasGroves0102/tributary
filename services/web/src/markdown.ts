import DOMPurify from "dompurify";
import { marked } from "marked";

import { day } from "./format";
import type { Citation } from "./types";

const ACCESSION = /\b\d{10}-\d{2}-\d{6}\b/;
const IS_ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const SEC = "https://www.sec.gov/";
/** What the model copies from a tool's JSON into its prose: a timestamp, a date, a field name, a
 * raw XBRL concept. They read as a person would write them; the answer's text is unchanged. */
const TIMESTAMP = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/;
const ISO_DATE = /\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b/;
const FIELD = /\bas_of\b/;
const XBRL_TAG = /\b(?:us-gaap|dei|srt|ifrs-full):[A-Z][A-Za-z0-9]+\b/;

/** us-gaap:PaymentsForRepurchaseOfCommonStock → Payments for repurchase of common stock. */
export function conceptWords(tag: string): string {
  const words = tag.slice(tag.indexOf(":") + 1).match(/[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+/g) ?? [];
  return words.map((w, i) => (i === 0 || /^[A-Z]{2,}$/.test(w) ? w : w.toLowerCase())).join(" ");
}

/** A piece of model text that isn't for a person as written, in words. */
function readable(raw: string): Node {
  if (raw === "as_of") return document.createTextNode("as of");
  if (XBRL_TAG.test(raw)) {
    const span = document.createElement("span");
    span.title = raw;
    span.textContent = conceptWords(raw);
    return span;
  }
  return document.createTextNode(day(raw, true));
}

/**
 * Model output → sanitized HTML. Accession numbers the agent's tools actually returned become
 * links to the filing on EDGAR; any other accession number, and any figure in `unverified`,
 * is marked unverified. Timestamps, field names and XBRL concepts the model copied from a tool
 * read as words.
 */
export function renderAnswer(
  markdown: string,
  citations: Citation[],
  unverified: string[] = [],
): string {
  const html = DOMPurify.sanitize(marked.parse(markdown, { async: false, gfm: true }));
  const byAccession = new Map(citations.map((c) => [c.accession_no, c]));
  const root = document.createElement("div");
  root.innerHTML = html;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);

  const figures = [...new Set(unverified)].sort((a, b) => b.length - a.length).map(escape);
  const flaggedFigures = new Set(unverified);
  const pattern = new RegExp([ACCESSION.source, ...figures, TIMESTAMP.source, ISO_DATE.source, FIELD.source, XBRL_TAG.source].join("|"), "g");

  for (const node of textNodes) {
    if (node.parentElement?.closest("a")) continue;
    const text = node.data;
    const matches = [...text.matchAll(pattern)];
    if (!matches.length) continue;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of matches) {
      frag.append(text.slice(last, m.index));
      frag.append(
        IS_ACCESSION.test(m[0]) ? citationElement(m[0], byAccession.get(m[0]))
          : flaggedFigures.has(m[0]) ? flagged(m[0])
          : readable(m[0]),
      );
      last = m.index! + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }

  root.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
  return root.innerHTML;
}

/**
 * The user's own Markdown (a thesis) → sanitized HTML: the same marked + DOMPurify pipeline,
 * without the citation check (nothing here came from a tool, so accession numbers stay text
 * rather than being flagged). Untrusted all the same: imported files and pasted text go
 * through DOMPurify, and links open in a new tab without an opener.
 */
export function renderMarkdown(markdown: string): string {
  const root = document.createElement("div");
  root.innerHTML = DOMPurify.sanitize(marked.parse(markdown, { async: false, gfm: true }), {
    // no inline styles (the CSP refuses them anyway) and no form controls in a note
    FORBID_TAGS: ["style", "form", "input", "button", "select", "textarea"],
    FORBID_ATTR: ["style"],
  });
  // the app loads nothing from other origins (CSP img-src 'self'): an image is its alt text
  root.querySelectorAll("img").forEach((img) => {
    const alt = img.getAttribute("alt")?.trim();
    img.replaceWith(document.createTextNode(alt ? `[image: ${alt}]` : "[image]"));
  });
  root.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
  return root.innerHTML;
}

function describe(c: Citation): string {
  return [c.form ?? "Source filing", c.filed && `filed ${c.filed}`].filter(Boolean).join(", ");
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function flagged(figure: string): HTMLElement {
  const span = document.createElement("span");
  span.className = "figure unverified";
  span.title = "Not found in anything the assistant read";
  span.textContent = figure;
  return span;
}

function citationElement(accession: string, c: Citation | undefined): HTMLElement {
  if (c?.url?.startsWith(SEC)) {
    const a = document.createElement("a");
    a.href = c.url;
    a.className = "accession";
    a.title = describe(c);
    a.textContent = accession;
    return a;
  }
  const span = document.createElement("span");
  span.className = c ? "accession" : "accession unverified";
  span.title = c ? describe(c) : "A filing the assistant didn't read";
  span.textContent = accession;
  return span;
}
