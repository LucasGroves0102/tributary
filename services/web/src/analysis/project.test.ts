import { describe, expect, it } from "vitest";

import { request, restoreChart } from "../chart/model";
import { renderMarkdown } from "../markdown";
import type { ExpectedFiling, ProjectState } from "../types";
import { projectRows } from "./AnalysisHome";
import { draftKey, restoreDraft } from "./bridge";
import { numbersCharts } from "./charts";
import { pinMenu } from "./Pin";
import {
  appendToSection,
  nextPillarName,
  pillarsOf,
  placePin,
  catalystLines,
  coreWritten,
  edgarFilingUrl,
  firstOpenStep,
  hasOwnText,
  hasWork,
  nextStep,
  quoteBlock,
  quoteLines,
  quoteTable,
  quoteText,
  restoreProject,
  sectionText,
  stepDone,
  STEPS,
  thesisTemplate,
  toggleDone,
  workHint,
  type Facts,
} from "./project";
import { paragraphs } from "./Steps";

const NVDA = "0001045810";
const AAPL = "0000320193";
const P: ProjectState = { companyId: NVDA, peers: [AAPL], notes: "", done: [] };

describe("projects", () => {
  it("restores a project from untrusted JSON and refuses what isn't one", () => {
    expect(restoreProject({ companyId: NVDA, peers: [AAPL, AAPL, NVDA, "x", 3], notes: "a\r\nb\u0000", done: ["numbers", "admin", "numbers"] }))
      .toEqual({ companyId: NVDA, peers: [AAPL], notes: "a\nb", done: ["numbers"] });
    expect(restoreProject({ companyId: NVDA })).toEqual({ companyId: NVDA, peers: [], notes: "", done: [] });
    expect(restoreProject(null)).toBeNull();
    expect(restoreProject({ companyId: "NVDA" })).toBeNull();
    expect(restoreProject([NVDA])).toBeNull();
  });

  it("marks steps done by hand, in step order, and unmarks them", () => {
    const a = toggleDone(toggleDone(P, "peers"), "business");
    expect(a.done).toEqual(["business", "peers"]);
    expect(toggleDone(a, "peers").done).toEqual(["business"]);
  });

  it("a step is done only when the user marked it; its work is a hint (#71, M3)", () => {
    const f: Facts = { charts: 1, models: 1, thesisWritten: true, checks: 2 };
    const notes = { ...P, notes: "Sells GPUs" };
    // peers suggested at creation, a chart, a model, a stub thesis, a check: work, not done
    expect(STEPS.map((s) => hasWork(s.key, notes, f))).toEqual([true, true, false, false, true, true, true, true]);
    expect(STEPS.map((s) => stepDone(s.key, notes))).toEqual([false, false, false, false, false, false, false, false]);
    expect(STEPS.map((s) => workHint(s.key, notes, f))).toEqual([
      "Notes written", "1 chart saved here", null, null, "1 peer picked", "1 model here", "Core thesis written", "Checked 2 times"]);
    const marked = { ...notes, done: ["numbers" as const] };
    expect(stepDone("numbers", marked)).toBe(true);
    expect(workHint("numbers", marked, f)).toBeNull(); // done: no hint
  });

  it("opens at the first step the user hasn't marked done", () => {
    expect(firstOpenStep({ ...P, notes: "x" })).toBe("business"); // notes alone don't finish it
    expect(firstOpenStep({ ...P, done: ["business", "numbers"] })).toBe("earnings");
    // a project ticked before Earnings existed opens at it: the new step is undone, nothing else moves
    expect(firstOpenStep({ ...P, done: ["business", "numbers", "changes", "peers"] })).toBe("earnings");
    expect(firstOpenStep({ ...P, done: STEPS.map((s) => s.key) })).toBe("monitor");
  });

  it("steps come in order, and Monitor promises only what it does (#154)", () => {
    expect(STEPS.map((s) => s.label)).toEqual(["Business", "Numbers", "Earnings", "What changed", "Peers", "Model", "Thesis", "Monitor"]);
    expect(nextStep("business")).toBe("numbers");
    expect(nextStep("numbers")).toBe("earnings");
    expect(nextStep("earnings")).toBe("changes");
    // F1: the release and the 8-Ks since the last report have their places in the flow
    expect(STEPS.find((s) => s.key === "earnings")!.why).toMatch(/earnings release \(8-K Item 2\.02\)/);
    expect(STEPS.find((s) => s.key === "changes")!.why).toMatch(/since its last 10-Q or 10-K/);
    expect(nextStep("monitor")).toBeNull();
    const monitor = STEPS.find((s) => s.key === "monitor")!.why;
    expect(monitor).toContain("when you choose");
    expect(monitor).not.toMatch(/each new filing checked/i);
  });

  it("lists projects newest edit first, folders left out", () => {
    const ws = (id: string, project: unknown, updated: string, items: { kind: string; updated_at: string }[] = []) => ({
      workspace_id: id, name: id, created_at: "", updated_at: updated, project: project as ProjectState | null,
      items: items.map((i, n) => ({ item_id: `${id}-${n}`, workspace_id: id, name: "x", created_at: "", opened_at: null, ...i, kind: i.kind as "thesis" })),
    });
    const rows = projectRows([
      ws("folder", null, "2026-11-03T00:00:00Z"),
      ws("old", P, "2026-10-01T00:00:00Z", [{ kind: "thesis", updated_at: "2026-11-02T00:00:00Z" }]),
      ws("new", P, "2026-11-01T00:00:00Z"),
    ]);
    expect(rows.map((r) => [r.ws.workspace_id, r.edited, r.thesisId])).toEqual([
      ["old", "2026-11-02T00:00:00Z", "old-0"],
      ["new", "2026-11-01T00:00:00Z", null],
    ]);
  });
});

const EXPECTED: ExpectedFiling[] = [
  { form: "10-K", period_end: "2027-01-31", expected_date: "2027-02-26", deadline: null, deadline_days: null, filer_category: null, basis: "history", typical_lag_days: 26, lag_samples: 4, status: "upcoming", estimate: true, last_period_end: null, last_filed: null },
  { form: "10-Q", period_end: "2026-10-26", expected_date: "2026-11-19", deadline: null, deadline_days: null, filer_category: null, basis: "history", typical_lag_days: 24, lag_samples: 8, status: "upcoming", estimate: true, last_period_end: null, last_filed: null },
  { form: "10-Q", period_end: "2027-04-30", expected_date: null, deadline: null, deadline_days: null, filer_category: null, basis: "none", typical_lag_days: null, lag_samples: null, status: "upcoming", estimate: true, last_period_end: null, last_filed: null },
];

describe("the thesis template", () => {
  const md = thesisTemplate({ company: "NVIDIA (NVDA)", catalysts: catalystLines(EXPECTED) });

  it("is plain Markdown headings with guidance as comments, catalysts prefilled soonest first", () => {
    expect(md.split("\n").filter((l) => l.startsWith("#"))).toEqual([
      "# NVIDIA (NVDA): thesis", "## Core thesis", "## Variant perception", "## Pillars", "### Pillar 1", "## Catalysts",
      "## Risks and mitigants", "## What would prove me wrong", "## KPIs to watch",
    ]);
    expect(md).toContain("<!-- 3-5 sentences:");
    expect(sectionText(md, "catalysts").split("\n")).toEqual([
      "- ~2026-11-19: 10-Q for the period ending 2026-10-26 (estimated date)",
      "- ~2027-02-26: 10-K for the period ending 2027-01-31 (estimated date)",
      "- 10-Q for the period ending 2027-04-30: no date estimate yet (estimated date)",
    ]);
  });

  it("renders without the guidance (comments don't show)", () => {
    const html = renderMarkdown(md);
    expect(html).toContain("<h2>Core thesis</h2>");
    expect(html).not.toContain("3-5 sentences");
  });

  it("tells an untouched template from a written thesis", () => {
    expect(hasOwnText(md)).toBe(false);
    expect(coreWritten(md)).toBe(false);
    const written = appendToSection(md, "core", "Data centers keep buying.");
    expect(hasOwnText(written)).toBe(true);
    expect(coreWritten(written)).toBe(true);
    expect(hasOwnText(appendToSection(md, "kpis", "- gross margin above 70%"))).toBe(true);
    expect(coreWritten(appendToSection(md, "kpis", "- gross margin above 70%"))).toBe(false);
    expect(hasOwnText("")).toBe(false);
  });
});

describe("appendToSection", () => {
  const doc = "# T\n\n## Core thesis\n\nMine.\n\n## Pillars\n\n### Pillar 1\n\nOne.\n\n\n## Catalysts\n\n- soon\n";

  it("adds a block at the end of the section, sub-headings included", () => {
    expect(appendToSection(doc, "pillars", "[[embed:x]]")).toBe(
      "# T\n\n## Core thesis\n\nMine.\n\n## Pillars\n\n### Pillar 1\n\nOne.\n\n[[embed:x]]\n\n## Catalysts\n\n- soon\n");
    expect(appendToSection(doc, "catalysts", "> quote")).toBe(`${doc.trimEnd()}\n\n> quote\n`);
  });

  it("finds a renamed heading by its start, at any level, and an empty section", () => {
    expect(appendToSection("# Pillars (with evidence)\nA\n# Other", "pillars", "B")).toBe("# Pillars (with evidence)\nA\n\nB\n\n# Other");
    expect(appendToSection("## Risks and mitigants\n## KPIs to watch", "risks", "R")).toBe("## Risks and mitigants\n\nR\n\n## KPIs to watch");
  });

  it("adds a missing section at the end, and ignores headings inside code", () => {
    expect(appendToSection("# T\n\nText.\n\n", "pillars", "P")).toBe("# T\n\nText.\n\n## Pillars\n\nP\n");
    expect(appendToSection("", "core", "C")).toBe("## Core thesis\n\nC\n");
    expect(appendToSection("```\n## Pillars\n```\n", "pillars", "P")).toBe("```\n## Pillars\n```\n\n## Pillars\n\nP\n");
  });
});

describe("pinning under a chosen pillar (L5)", () => {
  const doc = "# T\n\n## Pillars\n\n### Data center demand\n\nOne.\n\n### Margins hold\n\nTwo.\n\n## Catalysts\n\n- soon\n";

  it("lists the pillars by their sub-headings, renamed ones included", () => {
    expect(pillarsOf(doc)).toEqual(["Data center demand", "Margins hold"]);
    expect(pillarsOf(thesisTemplate({ company: "X", catalysts: [] }))).toEqual(["Pillar 1"]);
    expect(pillarsOf("# T\n\n## Pillars\n\nNo sub-headings.\n")).toEqual([]);
    expect(pillarsOf("## Pillars\n```\n### not a pillar\n```\n")).toEqual([]);
  });

  it("puts a pin at the end of the chosen pillar, not the last one", () => {
    expect(placePin(doc, { section: "pillars", pillar: "Data center demand" }, "[[embed:x]]")).toBe(
      "# T\n\n## Pillars\n\n### Data center demand\n\nOne.\n\n[[embed:x]]\n\n### Margins hold\n\nTwo.\n\n## Catalysts\n\n- soon\n");
    expect(placePin(doc, { section: "pillars", pillar: "Margins hold" }, "Y")).toBe(appendToSection(doc, "pillars", "Y"));
  });

  it("starts a new pillar after the others, named after the next free number", () => {
    expect(placePin(doc, { section: "pillars", newPillar: true }, "Z")).toBe(
      "# T\n\n## Pillars\n\n### Data center demand\n\nOne.\n\n### Margins hold\n\nTwo.\n\n### Pillar 3\n\nZ\n\n## Catalysts\n\n- soon\n");
    expect(nextPillarName("## Pillars\n\n### Pillar 1\n\n### Pillar 2\n")).toBe("Pillar 3");
    expect(nextPillarName("## Pillars\n\n### Pillar 2\n")).toBe("Pillar 3"); // never a second "Pillar 2"
  });

  it("the menu offers each pillar and a new one, the first the default; no pillars, Pillars itself", () => {
    expect(pinMenu(["Data center demand", "Margins hold"]).slice(0, 4).map((m) => [m.text, m.hint ?? ""])).toEqual([
      ["Pillars › Data center demand", "default"], ["Pillars › Margins hold", ""], ["Pillars › New pillar", ""], ["Core thesis", ""],
    ]);
    expect(pinMenu([])[0]).toMatchObject({ text: "Pillars", hint: "default", target: { section: "pillars" } });
    expect(pinMenu([]).map((m) => m.target.section)).toEqual(["pillars", "core", "variant", "catalysts", "risks", "wrong", "kpis"]);
  });

  it("a pillar the text no longer has, or another section, goes where it did before", () => {
    expect(placePin(doc, { section: "pillars", pillar: "Gone" }, "Q")).toBe(appendToSection(doc, "pillars", "Q"));
    expect(placePin(doc, { section: "catalysts" }, "C")).toBe(appendToSection(doc, "catalysts", "C"));
  });
});

describe("quoting a filing", () => {
  it("links the accession to EDGAR only when both ids have the API's shape", () => {
    expect(edgarFilingUrl(NVDA, "0001045810-26-000075")).toBe(
      "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/0001045810-26-000075-index.htm");
    expect(edgarFilingUrl(NVDA, "../../x")).toBeNull();
    expect(edgarFilingUrl("javascript:", "0001045810-26-000075")).toBeNull();
  });

  it("escapes Markdown and HTML so the passage renders as written, and cuts long ones", () => {
    expect(quoteText("  *Not* <b>bold</b>\n [link](x) # 1 ")).toBe("\\*Not\\* \\<b\\>bold\\</b\\> \\[link\\](x) \\# 1");
    const long = quoteText("word ".repeat(600));
    expect(long.length).toBeLessThan(1510);
    expect(long.endsWith(" …")).toBe(true);
  });

  it("is a blockquote with its citation, and renders safely", () => {
    const block = quoteBlock({
      text: "Demand for <script>alert(1)</script> Blackwell exceeded supply.", companyId: NVDA, accessionNo: "0001045810-26-000075",
      form: "10-Q", filed: "2026-08-27", section: "Item 1A. Risk Factors", change: "new",
    });
    expect(block).toBe(
      "> Demand for \\<script\\>alert(1)\\</script\\> Blackwell exceeded supply.\n>\n" +
      "> — 10-Q filed 2026-08-27, Item 1A. Risk Factors (new in this filing) · " +
      "[0001045810-26-000075](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/0001045810-26-000075-index.htm)");
    const html = renderMarkdown(block);
    expect(html).toContain("<blockquote>");
    expect(html).not.toContain("<script");
    expect(html).toContain('href="https://www.sec.gov/Archives/edgar/data/1045810/');
  });

  it("says which period the quoted filing reports on (H13: six-month wording under Q2 figures)", () => {
    const at = (form: string, period: string | null) => quoteBlock({
      text: "HOKA brand net sales increased.", companyId: NVDA, accessionNo: "0001045810-26-000075",
      form, filed: "2026-10-24", period, section: "Item 2. MD&A",
    }).split("\n")[2];
    expect(at("10-Q", "2026-09-30")).toMatch(/^> — 10-Q for the quarter ended 2026-09-30, filed 2026-10-24, Item 2\. MD&A · /);
    expect(at("10-K", "2026-03-31")).toMatch(/^> — 10-K for the fiscal year ended 2026-03-31, filed 2026-10-24,/);
    expect(at("10-Q", null)).toMatch(/^> — 10-Q filed 2026-10-24, Item 2/); // unknown: as before
    expect(at("10-Q", "<b>x</b>")).toMatch(/^> — 10-Q filed 2026-10-24,/); // only a date
  });

  it("F1: a release's outlook keeps its lines and says the quarter it reports; a table keeps its columns", () => {
    const release = { companyId: "0001674101", accessionNo: "0001628280-26-067707", form: "8-K", filed: "2026-10-28",
      period: "2026-09-30", periodWord: "quarter", section: "Earnings release (Exhibit 99.1), outlook: Fourth Quarter 2026 Guidance" };
    const cite = "> — 8-K for the quarter ended 2026-09-30, filed 2026-10-28, Earnings release (Exhibit 99.1), outlook: Fourth Quarter 2026 Guidance · " +
      "[0001628280-26-067707](https://www.sec.gov/Archives/edgar/data/1674101/000162828026067707/0001628280-26-067707-index.htm)";
    const lines = quoteLines({ ...release, text: "Fourth Quarter 2026 Guidance\nNet sales $4,313M - $4,513M\n- 1. not a list\n<b>x</b> *y*" });
    expect(lines).toBe(`> Fourth Quarter 2026 Guidance  \n> Net sales $4,313M - $4,513M  \n> \\- 1. not a list  \n> \\<b\\>x\\</b\\> \\*y\\*\n>\n${cite}`);
    const html = renderMarkdown(lines);
    expect(html).toContain("<blockquote>");
    expect(html).toContain("Net sales $4,313M - $4,513M<br>");
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<li>");
    const table = quoteTable({ ...release, text: "Reconciliation\nNet income $ 9,450 $ 8,788\nTax `effect` (2,581)" });
    expect(table.split("\n").slice(0, 5)).toEqual(["> ```text", "> Reconciliation", "> Net income $ 9,450 $ 8,788", "> Tax 'effect' (2,581)", "> ```"]);
    expect(table.endsWith(cite)).toBe(true);
    expect(renderMarkdown(table)).toContain("<pre><code");
    // long passages are cut, and say so
    const many = Array.from({ length: 60 }, (_, i) => `Line ${i}`).join("\n");
    expect(quoteTable({ ...release, text: many }).split("\n").filter((l) => l === "> …")).toHaveLength(1);
    expect(quoteLines({ ...release, text: many }).split("  \n")).toHaveLength(41);
  });

  it("splits section text into paragraphs, joining short headings to what follows", () => {
    expect(paragraphs("Item 1. Business\nOur Company\nNVIDIA pioneered accelerated computing to help solve hard problems.\n\n\nWe sell GPUs, systems and software to data centers.\n"))
      .toEqual([
        "Item 1. Business Our Company — NVIDIA pioneered accelerated computing to help solve hard problems.",
        "We sell GPUs, systems and software to data centers.",
      ]);
  });
});

describe("the Numbers step's charts", () => {
  const sets = numbersCharts(NVDA, "NVDA");

  it("covers growth, margins, free cash flow, receivables vs revenue and debt", () => {
    expect(sets.map((s) => s.key)).toEqual(["growth", "margins", "fcf", "receivables", "debt"]);
    expect(sets.find((s) => s.key === "fcf")!.chart.layers.map((l) => l.metric)).toEqual(["operating_cash_flow", "capex", "free_cash_flow"]);
  });

  it("are chart builder states: they restore unchanged and ask the formula API for annual fiscal values", () => {
    for (const s of sets) {
      expect(restoreChart(s.chart)).toEqual(s.chart);
      for (const l of s.chart.layers) {
        const r = request(l, s.chart, () => true, new Date("2026-11-02T00:00:00Z"))!;
        expect(r.basis).toBe("fiscal");
        expect(r.default_company).toBe(NVDA);
      }
    }
    expect(request(sets[0]!.chart.layers[1]!, sets[0]!.chart, () => true)!.formula).toBe("(revenue) / (revenue)[-4q] - 1");
  });
});

describe("thesis drafts in the browser", () => {
  it("keeps only well-formed drafts, per user and thesis", () => {
    expect(draftKey("alice", "t1")).toBe("tributary.thesis-draft.alice.t1");
    const d = { name: "N", markdown: "M", companyId: NVDA, base: "b", at: "2026-11-02T10:00:00Z" };
    expect(restoreDraft(JSON.stringify(d))).toEqual(d);
    expect(restoreDraft(JSON.stringify({ ...d, companyId: "x" }))?.companyId).toBeNull();
    expect(restoreDraft(JSON.stringify({ ...d, markdown: 3 }))).toBeNull();
    expect(restoreDraft("{")).toBeNull();
    expect(restoreDraft(null)).toBeNull();
  });
});
