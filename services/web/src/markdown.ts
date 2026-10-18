import DOMPurify from "dompurify";
import { marked } from "marked";

import type { Citation } from "./types";

const ACCESSION = /\b\d{10}-\d{2}-\d{6}\b/;
const IS_ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const SEC = "https://www.sec.gov/";

/**
 * Model output → sanitized HTML. Accession numbers the agent's tools actually returned become
 * links to the filing on EDGAR; any other accession number, and any figure in `unverified`,
 * is marked unverified.
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
  const pattern = new RegExp([ACCESSION.source, ...figures].join("|"), "g");

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
        IS_ACCESSION.test(m[0]) ? citationElement(m[0], byAccession.get(m[0])) : flagged(m[0]),
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

function describe(c: Citation): string {
  return [c.form ?? "Source filing", c.filed && `filed ${c.filed}`].filter(Boolean).join(", ");
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function flagged(figure: string): HTMLElement {
  const span = document.createElement("span");
  span.className = "figure unverified";
  span.title = "Not found in any tool result";
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
  span.title = c ? describe(c) : "Not in any tool result";
  span.textContent = accession;
  return span;
}
