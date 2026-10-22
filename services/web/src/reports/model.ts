/** Reports: the research note the assistant writes from a workspace (agent-api /v1/reports,
 * stored as a workspace item of kind "report", validated by query_api/reports.py). A report's
 * state is still untrusted here: `restoreReport` keeps only well-formed parts, its Markdown is
 * rendered only through markdown.ts (DOMPurify), and its embeds name only its own items, each
 * drawn client-side from the saved state through the Query API as the viewer. Pure functions,
 * tested without rendering. */

import type { Citation, SavedKind } from "../types";

/** What a report counts against the assistant limits (services/agent report.py). */
export const REPORT_QUESTIONS = 3;

export interface ReportItem {
  ref: string;
  item_id: string;
  kind: SavedKind;
  name: string;
  state: Record<string, unknown>;
}

export type FigureStatus = "verified" | "derived" | "quoted" | "unverified";

export interface Report {
  workspaceId: string;
  markdown: string;
  items: ReportItem[];
  citations: Citation[];
  verification: {
    ok: boolean;
    figures: { text: string; status: FigureStatus }[];
    unverifiedAccessions: string[];
    usedData: boolean;
    freshness: boolean;
  };
  asOf: string | null;
  writtenAt: string;
  model: string;
  questions: number;
  stop: string;
}

export type Segment = { type: "text"; markdown: string } | { type: "embed"; item: ReportItem };

const EMBED = /^\[\[embed:(item-[1-9])\]\]$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const KINDS = new Set(["chart", "comps", "screen"]);
const STATUSES = new Set(["verified", "derived", "quoted", "unverified"]);
const SEC = "https://www.sec.gov/";

const str = (v: unknown, max = 200): string | null => (typeof v === "string" ? v.slice(0, max) : null);
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** A report's state from the API as a Report, or null if it isn't one. */
export function restoreReport(raw: unknown): Report | null {
  const o = obj(raw);
  const markdown = str(o?.markdown, 50_000);
  const v = obj(o?.verification);
  if (!o || !markdown || !v) return null;
  const items: ReportItem[] = arr(o.items).flatMap((x) => {
    const i = obj(x);
    const ref = str(i?.ref, 10);
    const state = obj(i?.state);
    if (!i || !ref || !/^item-[1-9]$/.test(ref) || !state || !KINDS.has(String(i.kind))) return [];
    return [{ ref, item_id: str(i.item_id, 64) ?? "", kind: i.kind as SavedKind, name: str(i.name, 120) ?? "Item", state }];
  });
  const citations: Citation[] = arr(o.citations).flatMap((x) => {
    const c = obj(x);
    const acc = str(c?.accession_no, 20);
    if (!c || !acc || !ACCESSION.test(acc)) return [];
    const url = str(c.url, 300);
    return [{
      accession_no: acc,
      form: str(c.form, 20),
      filed: str(c.filed, 10),
      company_id: str(c.company_id, 10),
      url: url && url.startsWith(SEC) ? url : null,
      sections: arr(c.sections).filter((s): s is string => typeof s === "string").slice(0, 30),
    }];
  });
  const figures = arr(v.figures).flatMap((x) => {
    const f = obj(x);
    const text = str(f?.text, 60);
    return f && text && STATUSES.has(String(f.status)) ? [{ text, status: f.status as FigureStatus }] : [];
  });
  return {
    workspaceId: str(o.workspace_id, 64) ?? "",
    markdown,
    items,
    citations,
    verification: {
      ok: v.ok === true,
      figures,
      unverifiedAccessions: arr(v.unverified_accessions).filter((a): a is string => typeof a === "string" && ACCESSION.test(a)),
      usedData: v.used_data === true,
      freshness: v.freshness === true,
    },
    asOf: str(o.as_of, 40),
    writtenAt: str(o.written_at, 40) ?? "",
    model: str(o.model, 60) ?? "",
    questions: typeof o.questions === "number" ? o.questions : REPORT_QUESTIONS,
    stop: str(o.stop, 20) ?? "",
  };
}

/** The Markdown split at its embed lines; an embed of an item the report doesn't have is
 * dropped, and each item is embedded once. */
export function segments(report: Report): Segment[] {
  const byRef = new Map(report.items.map((i) => [i.ref, i]));
  const out: Segment[] = [];
  const seen = new Set<string>();
  let text: string[] = [];
  const flush = () => {
    const md = text.join("\n").trim();
    if (md) out.push({ type: "text", markdown: md });
    text = [];
  };
  for (const line of report.markdown.split("\n")) {
    const m = EMBED.exec(line.trim());
    if (!m) {
      text.push(line);
      continue;
    }
    const item = byRef.get(m[1]!);
    if (!item || seen.has(item.ref)) continue;
    seen.add(item.ref);
    flush();
    out.push({ type: "embed", item });
  }
  flush();
  return out;
}

/** Figures the verification couldn't trace to the data: highlighted, never hidden. */
export function unverifiedFigures(report: Report): string[] {
  return [...new Set(report.verification.figures.filter((f) => f.status === "unverified").map((f) => f.text))];
}

/** "All 6 figures verified" / "2 of 6 figures unverified" (and unknown filings, freshness). */
export function verificationSummary(report: Report): { ok: boolean; text: string } {
  const v = report.verification;
  const n = v.figures.length;
  const bad = v.figures.filter((f) => f.status === "unverified").length;
  const problems = [
    bad && `${bad} of ${n} figure${n === 1 ? "" : "s"} unverified (highlighted)`,
    v.unverifiedAccessions.length &&
      `${v.unverifiedAccessions.length} filing number${v.unverifiedAccessions.length === 1 ? "" : "s"} not from the data`,
    v.usedData && !v.freshness && "no freshness note",
  ].filter(Boolean) as string[];
  if (problems.length) return { ok: false, text: problems.join(" · ") };
  return { ok: true, text: n ? `All ${n} figure${n === 1 ? "" : "s"} verified against the data` : "No figures to verify" };
}

/** One line on what an embedded item is, for the Markdown export (it can't draw charts). */
export function describeItem(item: ReportItem, label: (companyId: string) => string): string {
  const s = item.state;
  if (item.kind === "chart") {
    const layers = arr(s.layers).flatMap((x) => {
      const l = obj(x);
      if (!l) return [];
      if (l.kind === "formula") return [String(l.formula ?? "formula")];
      return [`${label(String(l.companyId ?? ""))} ${String(l.metric ?? "").replace(/_/g, " ")}`];
    });
    return `Chart${str(s.title) ? ` “${str(s.title)}”` : ""}: ${layers.join(", ") || "no layers"}`;
  }
  if (item.kind === "comps") {
    const peers = arr(s.peers).filter((p): p is string => typeof p === "string").map(label);
    return `Comps: ${label(String(s.target ?? ""))} vs ${peers.join(", ") || "no peers"}`;
  }
  const filters = Object.entries(s).filter(([k]) => k !== "sort").map(([k, v]) => `${k} ${String(v)}`);
  return `Screen: ${filters.join(", ") || "all companies"}`;
}

function flag(markdown: string, figures: string[]): string {
  let out = markdown;
  for (const f of [...figures].sort((a, b) => b.length - a.length)) {
    out = out.split(f).join(`${f} [unverified]`);
  }
  return out;
}

/** The report as a Markdown file: embeds become a line naming the saved item, unverified
 * figures are marked, then when and how it was written, and its sources. */
export function reportMarkdown(report: Report, name: string, label: (companyId: string) => string): string {
  const figures = unverifiedFigures(report);
  const body = segments(report)
    .map((s) =>
      s.type === "text"
        ? flag(s.markdown, figures)
        : `> **${s.item.name}**: ${describeItem(s.item, label)}. Drawn from the saved ${s.item.kind} in Tributary.`,
    )
    .join("\n\n");
  const sum = verificationSummary(report);
  const lines = [
    body,
    "---",
    `*${name}*: written ${report.writtenAt} by the Tributary assistant (${report.model}) from the workspace's saved items` +
      `${report.asOf ? `; data as of ${report.asOf}` : ""}. ${sum.text}${figures.length ? ", marked [unverified]" : ""}.`,
  ];
  if (report.citations.length) {
    lines.push(
      "## Sources",
      report.citations
        .map((c) => `- ${[c.form ?? "Filing", c.filed && `filed ${c.filed}`].filter(Boolean).join(" ")}, ${c.accession_no}${c.url ? `: ${c.url}` : ""}`)
        .join("\n"),
    );
  }
  return `${lines.join("\n\n")}\n`;
}
