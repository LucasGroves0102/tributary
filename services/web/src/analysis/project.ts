/** Analysis projects (the Analysis tab): one workspace bound to one company, with the guided
 * steps an analyst takes from filings to a thesis (business → numbers → earnings → what changed →
 * peers → model → thesis → monitor). The project itself is `workspaces.project` (app DB 012, validated
 * by query_api/workspaces.py ProjectState): `{companyId, peers, notes, done}`; its charts,
 * models and thesis are ordinary items in the workspace. Pure, tested without rendering.
 *
 * - Steps: STEPS (order, labels, guidance). The project header's tabs and the rail are the same
 *   eight steps under the same names (a tab always opens its own step). Earnings (F1) sits after
 *   Numbers: the release is management's account of the figures just read (its outlook, its own
 *   adjustments). The 8-Ks filed since the last report open What changed: they are what changed
 *   after the report's period end, read beside the filing's rewritten paragraphs.
 * - Done: a step is done only when the user marked it (`done`): a box they can always tick and
 *   untick. Work in a step (`hasWork`: Business = notes written; Numbers = a chart saved in the
 *   project; Peers = at least one peer; Model = a model in the project; Thesis = the saved
 *   thesis's Core thesis section has text of the user's own; Monitor = the thesis was checked)
 *   is a hint beside the box, never a tick: peers suggested at creation, one pinned chart or a
 *   stub save don't make a step done, and nothing locks.
 * - Thesis template (`thesisTemplate`): plain Markdown headings (THESIS_SECTIONS) with the
 *   guidance under each as an HTML comment (invisible in the preview, one line to delete);
 *   Catalysts prefilled with the company's expected 10-Q/10-K dates (estimates, labelled so).
 *   `hasOwnText` tells a thesis with writing from an untouched template (the check is
 *   disabled until then).
 * - Pins ("📌 Add to thesis"): `appendToSection` puts a block at the end of a section (before
 *   the next heading of the same or a higher level), creating the section if it's gone; a chart
 *   is pinned as its `[[embed:<item id>]]` line (the chart is saved in the project first), a
 *   filing passage as `quoteBlock`: a Markdown blockquote and a citation line (form, the period
 *   it reports on, filed date, section, and an EDGAR link built only from the accession number the
 *   API returned); a release's table (a guidance table, a reconciliation) as `quoteTable`, its
 *   lines kept in a code block inside the quote. */

import type { ExpectedFiling, ProjectState, ProjectStep } from "../types";

export interface StepInfo {
  key: ProjectStep;
  label: string;
  /** one line under the step's title: what this step is for */
  why: string;
}

export const STEPS: StepInfo[] = [
  { key: "business", label: "Business", why: "What the company sells, to whom, and how it makes money: the 10-K's Item 1 and its segments." },
  { key: "numbers", label: "Numbers", why: "Five to ten years of revenue and growth, margins, free cash flow, receivables against revenue, and debt." },
  { key: "earnings", label: "Earnings", why: "Management's own account of each quarter: the earnings release (8-K Item 2.02) beside the reported figures, the outlook it states and the adjustments it makes." },
  { key: "changes", label: "What changed", why: "What the company filed since its last 10-Q or 10-K (deals, debt, leadership, guidance), and new, removed and rewritten paragraphs in Risk Factors and MD&A." },
  { key: "peers", label: "Peers", why: "The same numbers for comparable companies: is this business better or worse than its peers?" },
  { key: "model", label: "Model", why: "Revenue and operating income forward: base, bear and bull cases next to the company's own history." },
  { key: "thesis", label: "Thesis", why: "Your argument in plain words: core thesis, what the market misses, pillars with evidence, risks." },
  { key: "monitor", label: "Monitor", why: "New 10-K, 10-Q and material 8-K filings as they arrive, with their key figures and your red lines checked on each; and your thesis checked against the filings when you choose." },
];

export const STEP_KEYS = STEPS.map((s) => s.key);
export const stepInfo = (key: ProjectStep): StepInfo => STEPS.find((s) => s.key === key)!;
export const stepNumber = (key: ProjectStep): number => STEP_KEYS.indexOf(key) + 1;
export const nextStep = (key: ProjectStep): ProjectStep | null => STEP_KEYS[STEP_KEYS.indexOf(key) + 1] ?? null;


export const MAX_PEERS = 20;
export const MAX_NOTES = 4000;
const CIK = /^\d{10}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f]/g;

/** A project from the API (untrusted) as a ProjectState, or null when it isn't one. */
export function restoreProject(raw: unknown): ProjectState | null {
  const o = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
  if (!o || typeof o.companyId !== "string" || !CIK.test(o.companyId)) return null;
  const peers = [...new Set((Array.isArray(o.peers) ? o.peers : []).filter((p): p is string => typeof p === "string" && CIK.test(p)))]
    .filter((p) => p !== o.companyId)
    .slice(0, MAX_PEERS);
  const done = [...new Set((Array.isArray(o.done) ? o.done : []).filter((d): d is ProjectStep => STEP_KEYS.includes(d as ProjectStep)))];
  const notes = typeof o.notes === "string" ? o.notes.replace(/\r\n?/g, "\n").replace(CONTROL, "").slice(0, MAX_NOTES) : "";
  return { companyId: o.companyId, peers, notes, done };
}

/** The user marked a step done, or unmarked it. */
export function toggleDone(p: ProjectState, step: ProjectStep): ProjectState {
  const done = p.done.includes(step) ? p.done.filter((d) => d !== step) : STEP_KEYS.filter((k) => k === step || p.done.includes(k));
  return { ...p, done };
}

/** What the project holds, for the done markers. */
export interface Facts {
  charts: number;
  models: number;
  /** the saved thesis's Core thesis section has the user's own text */
  thesisWritten: boolean;
  /** checks of the thesis (Check my thesis) */
  checks: number;
}

/** The step has work in the project (the rules above): a hint, not done. */
export function hasWork(step: ProjectStep, p: ProjectState, f: Facts): boolean {
  switch (step) {
    case "business":
      return p.notes.trim().length > 0;
    case "numbers":
      return f.charts > 0;
    case "earnings":
    case "changes":
      return false;
    case "peers":
      return p.peers.length > 0;
    case "model":
      return f.models > 0;
    case "thesis":
      return f.thesisWritten;
    case "monitor":
      return f.checks > 0;
  }
}

/** Done means the user marked it. */
export const stepDone = (step: ProjectStep, p: ProjectState): boolean => p.done.includes(step);

/** What the box beside a step says when its work exists but it isn't marked done. */
export function workHint(step: ProjectStep, p: ProjectState, f: Facts): string | null {
  if (!hasWork(step, p, f) || stepDone(step, p)) return null;
  switch (step) {
    case "business":
      return "Notes written";
    case "numbers":
      return `${f.charts} chart${f.charts === 1 ? "" : "s"} saved here`;
    case "peers":
      return `${p.peers.length} peer${p.peers.length === 1 ? "" : "s"} picked`;
    case "model":
      return `${f.models} model${f.models === 1 ? "" : "s"} here`;
    case "thesis":
      return "Core thesis written";
    case "monitor":
      return `Checked ${f.checks} time${f.checks === 1 ? "" : "s"}`;
    case "earnings":
    case "changes":
      return null;
  }
}

/** Where a project opens: the first step the user hasn't marked done (the last one when all are). */
export function firstOpenStep(p: ProjectState): ProjectStep {
  return STEP_KEYS.find((s) => !stepDone(s, p)) ?? "monitor";
}

// --- the thesis template ----------------------------------------------------------------

export type SectionKey = "core" | "variant" | "pillars" | "catalysts" | "risks" | "wrong" | "kpis";

export interface ThesisSection {
  key: SectionKey;
  heading: string;
  guidance: string;
}

export const THESIS_SECTIONS: ThesisSection[] = [
  { key: "core", heading: "Core thesis", guidance: "3-5 sentences: what you believe about the business, why the price doesn't reflect it yet, and over what horizon." },
  { key: "variant", heading: "Variant perception", guidance: "What does the market believe, and where exactly do you disagree? Without a difference from consensus there's no edge." },
  { key: "pillars", heading: "Pillars", guidance: "2-4 reasons the thesis holds, each with its evidence: a chart, a figure, a filing passage. “Add to thesis” on any step pins evidence here." },
  { key: "catalysts", heading: "Catalysts", guidance: "Events that will show whether you're right, with dates. The next expected 10-Q/10-K filings are listed (estimated dates)." },
  { key: "risks", heading: "Risks and mitigants", guidance: "What could go wrong, how likely it is, and what limits the damage. Read Risk Factors and what changed in them." },
  { key: "wrong", heading: "What would prove me wrong", guidance: "Concrete signals that would make you change your mind or sell. Decide them now, before the news arrives." },
  { key: "kpis", heading: "KPIs to watch", guidance: "5-7 numbers to check every quarter (revenue growth, gross margin, receivables vs revenue, …), each with the level that would worry you." },
];
/** Where a pin goes unless the user picks another section. */
export const DEFAULT_PIN_SECTION: SectionKey = "pillars";
export const sectionOf = (key: SectionKey): ThesisSection => THESIS_SECTIONS.find((s) => s.key === key)!;

const ESTIMATED = "(estimated date)";

/** The Catalysts list's prefilled lines: the company's next expected 10-Q/10-K filings
 * (estimates from the public filing index), soonest first. */
export function catalystLines(expected: ExpectedFiling[]): string[] {
  return [...expected]
    .sort((a, b) => (a.expected_date ?? "9999").localeCompare(b.expected_date ?? "9999"))
    .map((e) => e.expected_date
      ? `- ~${e.expected_date}: ${e.form} for the period ending ${e.period_end} ${ESTIMATED}`
      : `- ${e.form} for the period ending ${e.period_end}: no date estimate yet ${ESTIMATED}`);
}

/** A new project thesis: a title, then each section's heading with its guidance as an HTML
 * comment; Pillars has a first sub-heading, Catalysts the expected filings. */
export function thesisTemplate(opts: { company: string; catalysts: string[] }): string {
  const parts = [`# ${opts.company.replace(/[\r\n]+/g, " ").trim() || "Company"}: thesis`];
  for (const s of THESIS_SECTIONS) {
    const body = [`<!-- ${s.guidance} -->`];
    if (s.key === "pillars") body.push("", "### Pillar 1");
    if (s.key === "catalysts" && opts.catalysts.length) body.push("", ...opts.catalysts);
    parts.push(`## ${s.heading}\n\n${body.join("\n")}`);
  }
  return `${parts.join("\n\n")}\n`;
}

const COMMENT = /<!--[\s\S]*?(-->|$)/g;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const PREFILLED = /^- .*\(estimated date\)\s*$/;

/** Whether the thesis has writing of the user's own: anything but headings, the guidance
 * comments and the prefilled catalyst lines. */
export function hasOwnText(markdown: string): boolean {
  return markdown
    .replace(COMMENT, "")
    .split("\n")
    .some((l) => {
      const t = l.trim();
      return t !== "" && !HEADING.test(t) && !PREFILLED.test(t);
    });
}

interface Span {
  /** the heading's line index; the section runs to `end` (exclusive) */
  start: number;
  end: number;
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

/** The section whose heading starts with the section's name (any level), to the next heading
 * of the same or a higher level. */
function findSection(lines: string[], key: SectionKey): Span | null {
  const want = norm(sectionOf(key).heading);
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i]!)) inFence = !inFence;
    const m = inFence ? null : HEADING.exec(lines[i]!.trim());
    if (!m || !norm(m[2]!).startsWith(want)) continue;
    const level = m[1]!.length;
    let end = lines.length;
    let fence = false;
    for (let j = i + 1; j < lines.length; j++) {
      if (/^\s*(```|~~~)/.test(lines[j]!)) fence = !fence;
      const n = fence ? null : HEADING.exec(lines[j]!.trim());
      if (n && n[1]!.length <= level) {
        end = j;
        break;
      }
    }
    return { start: i, end };
  }
  return null;
}

/** The text of a section, without its heading and the guidance comments. */
export function sectionText(markdown: string, key: SectionKey): string {
  const lines = markdown.split("\n");
  const s = findSection(lines, key);
  return s ? lines.slice(s.start + 1, s.end).join("\n").replace(COMMENT, "").trim() : "";
}

/** The Core thesis section has text of the user's own (the Thesis step's done marker). */
export const coreWritten = (markdown: string): boolean => sectionText(markdown, "core").length > 0;

/** `block` added at the end of a section, on lines of its own with a blank line around it; a
 * missing section is added at the end of the thesis with its heading. */
export function appendToSection(markdown: string, key: SectionKey, block: string): string {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const s = findSection(lines, key);
  const body = block.trim().split("\n");
  if (!s) {
    const head = markdown.replace(/\s+$/, "");
    return `${head}${head ? "\n\n" : ""}## ${sectionOf(key).heading}\n\n${body.join("\n")}\n`;
  }
  return insertAtEnd(lines, s, body);
}

/** `body` after the last text of a span, a blank line around it. */
function insertAtEnd(lines: string[], s: Span, body: string[]): string {
  let at = s.end;
  while (at > s.start + 1 && lines[at - 1]!.trim() === "") at--; // after the span's last text
  const before = lines.slice(0, at);
  const after = lines.slice(s.end);
  return [...before, "", ...body, ...(after.length ? ["", ...after] : [""])].join("\n");
}

// --- pins: which pillar (L5) ---------------------------------------------------------------

/** Where a pin goes: a section; under Pillars, one pillar (its sub-heading's text) or a new one. */
export interface PinTarget {
  section: SectionKey;
  pillar?: string;
  newPillar?: boolean;
}

/** The pillars of a thesis, each a sub-heading of its Pillars section, with where each runs. */
function pillarSpans(lines: string[]): { name: string; level: number; span: Span }[] {
  const s = findSection(lines, "pillars");
  if (!s) return [];
  const level = HEADING.exec(lines[s.start]!.trim())![1]!.length;
  const heads: { name: string; level: number; at: number }[] = [];
  let fence = false;
  for (let i = s.start + 1; i < s.end; i++) {
    if (/^\s*(```|~~~)/.test(lines[i]!)) fence = !fence;
    const m = fence ? null : HEADING.exec(lines[i]!.trim());
    if (m && m[1]!.length > level && m[2]!.trim()) heads.push({ name: m[2]!.trim(), level: m[1]!.length, at: i });
  }
  const top = Math.min(...heads.map((h) => h.level));
  const pillars = heads.filter((h) => h.level === top);
  return pillars.map((h, k) => ({ name: h.name, level: h.level, span: { start: h.at, end: pillars[k + 1]?.at ?? s.end } }));
}

/** The pillars a thesis has, in order ("Pillar 1", or what the user renamed them). */
export const pillarsOf = (markdown: string): string[] =>
  pillarSpans(markdown.replace(/\r\n?/g, "\n").split("\n")).map((p) => p.name);

/** The next "Pillar N" no pillar has. */
export function nextPillarName(markdown: string): string {
  const names = new Set(pillarsOf(markdown).map((n) => n.toLowerCase()));
  let n = names.size + 1;
  while (names.has(`pillar ${n}`)) n++;
  return `Pillar ${n}`;
}

/** `block` at a pin target: a section's end (appendToSection); a pillar's end, under its own
 * sub-heading; or a new pillar ("### Pillar N", one level under Pillars) at the end of Pillars. A
 * pillar the text no longer has goes at the end of Pillars. */
export function placePin(markdown: string, target: PinTarget, block: string): string {
  if (target.section !== "pillars" || (!target.pillar && !target.newPillar)) return appendToSection(markdown, target.section, block);
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const body = block.trim().split("\n");
  if (target.newPillar) {
    const s = findSection(lines, "pillars");
    const level = s ? HEADING.exec(lines[s.start]!.trim())![1]!.length + 1 : 3;
    return appendToSection(markdown, "pillars", `${"#".repeat(Math.min(level, 6))} ${nextPillarName(markdown)}\n\n${block.trim()}`);
  }
  const p = pillarSpans(lines).find((x) => x.name === target.pillar);
  return p ? insertAtEnd(lines, p.span, body) : appendToSection(markdown, "pillars", block);
}

// --- pins: filing passages ----------------------------------------------------------------

const ACCESSION = /^(\d{10})-(\d{2})-(\d{6})$/;
export const MAX_QUOTE = 1500;

/** The filing's index page on EDGAR, built only from a company id and an accession number the
 * API returned (both checked by pattern); null for anything else. */
export function edgarFilingUrl(companyId: string, accessionNo: string): string | null {
  if (!CIK.test(companyId) || !ACCESSION.test(accessionNo)) return null;
  return `https://www.sec.gov/Archives/edgar/data/${Number(companyId)}/${accessionNo.replace(/-/g, "")}/${accessionNo}-index.htm`;
}

/** Filing text as one Markdown paragraph that renders as written: whitespace collapsed,
 * Markdown and HTML punctuation escaped, cut at a word near MAX_QUOTE characters. */
export function quoteText(text: string, max = MAX_QUOTE): string {
  let t = text.replace(/\s+/g, " ").trim();
  if (t.length > max) {
    const cut = t.slice(0, max);
    const sp = cut.lastIndexOf(" ");
    t = `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,;:]+$/, "")} …`;
  }
  return t.replace(/([\\`*_[\]<>#|~])/g, "\\$1");
}

export interface Passage {
  text: string;
  companyId: string;
  accessionNo: string;
  form: string;
  /** YYYY-MM-DD */
  filed: string;
  /** the period the filing reports on (YYYY-MM-DD), so the quote reads in its period (H13) */
  period?: string | null;
  /** what `period` ends ("quarter" for an 8-K earnings release); else the form's (10-Q quarter,
   * 10-K fiscal year) */
  periodWord?: string;
  /** "Item 1A. Risk Factors", "Item 1" */
  section: string;
  /** what-changed: "new", "rewritten" or "removed" (removed text is the earlier filing's) */
  change?: "new" | "rewritten" | "removed";
}

const CHANGE_NOTE = { new: "new in this filing", rewritten: "rewritten in this filing", removed: "removed since this filing" } as const;
const PERIOD_WORD: Record<string, string> = { "10-Q": "quarter", "10-K": "fiscal year" };
const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** A filing passage pinned into a thesis: a blockquote, then its citation (form, the period it
 * reports on when known, filed date, section, the accession linked to EDGAR when the link can be
 * built). A 10-Q's MD&A can speak of six or nine months: the period says which filing it is. */
export function quoteBlock(p: Passage): string {
  return `> ${quoteText(p.text)}\n>\n> — ${citationLine(p)}`;
}

function citationLine(p: Passage): string {
  const url = edgarFilingUrl(p.companyId, p.accessionNo);
  const acc = url ? `[${p.accessionNo}](${url})` : p.accessionNo;
  const section = quoteText(p.section, 120);
  const note = p.change ? ` (${CHANGE_NOTE[p.change]})` : "";
  const word = p.periodWord ?? PERIOD_WORD[p.form] ?? "period";
  const when = p.period && ISO.test(p.period)
    ? `${p.form} for the ${word} ended ${p.period}, filed ${p.filed}`
    : `${p.form} filed ${p.filed}`;
  return `${when}, ${section}${note} · ${acc}`;
}

export const MAX_TABLE_LINES = 40;

/** A passage of several lines (a release's outlook: its bullets, a small table of ranges) as a
 * blockquote that keeps its lines (hard breaks), each line escaped like quoteText and kept from
 * reading as a Markdown list; at most MAX_TABLE_LINES lines and twice MAX_QUOTE characters, "…"
 * when cut. Then the citation line, as quoteBlock. */
export function quoteLines(p: Passage): string {
  const out: string[] = [];
  let total = 0;
  for (const raw of p.text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.replace(/\s+/g, " ").trim();
    if (!line) continue;
    if (out.length === MAX_TABLE_LINES || total + line.length > MAX_QUOTE * 2) {
      out.push("…");
      break;
    }
    out.push(quoteText(line, 400).replace(/^([-+]|\d+[.)])(\s)/, "\\$1$2"));
    total += line.length;
  }
  return `${out.map((l) => `> ${l}`).join("  \n")}\n>\n> — ${citationLine(p)}`;
}

/** A table pinned from a filing (a release's guidance or reconciliation): its lines as written,
 * in a code block inside the quote so columns and minus signs stay as they are, then the same
 * citation line as quoteBlock. Backticks become quotes (they could close the block), at most
 * MAX_TABLE_LINES lines of up to 200 characters, and "…" says when it was cut. */
export function quoteTable(p: Passage): string {
  const all = p.text.replace(/\r\n?/g, "\n").split("\n").map((l) => l.replace(/`/g, "'").replace(/\s+/g, " ").trim()).filter(Boolean);
  const lines = all.slice(0, MAX_TABLE_LINES).map((l) => (l.length > 200 ? `${l.slice(0, 199)}…` : l));
  if (all.length > MAX_TABLE_LINES) lines.push("…");
  return ["> ```text", ...lines.map((l) => `> ${l}`), "> ```", ">", `> — ${citationLine(p)}`].join("\n");
}
