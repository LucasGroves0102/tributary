import { describe, expect, it } from "vitest";

import { request, restoreChart } from "../chart/model";
import { renderMarkdown } from "../markdown";
import type { ExpectedFiling, ProjectState } from "../types";
import { projectRows } from "./AnalysisHome";
import { draftKey, restoreDraft } from "./bridge";
import { numbersCharts } from "./charts";
import {
  appendToSection,
  autoDone,
  catalystLines,
  coreWritten,
  edgarFilingUrl,
  firstOpenStep,
  groupOf,
  GROUPS,
  hasOwnText,
  nextStep,
  quoteBlock,
  quoteText,
  restoreProject,
  sectionText,
  stepDone,
  STEPS,
  thesisTemplate,
  toggleDone,
  type Facts,
} from "./project";
import { paragraphs } from "./Steps";

const NVDA = "0001045810";
const AAPL = "0000320193";
const P: ProjectState = { companyId: NVDA, peers: [AAPL], notes: "", done: [] };
const NONE: Facts = { charts: 0, models: 0, thesisWritten: false, checks: 0 };

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

  it("counts a step done when its work exists", () => {
    expect(STEPS.map((s) => autoDone(s.key, P, NONE))).toEqual([false, false, false, true, false, false, false]);
    const f: Facts = { charts: 1, models: 1, thesisWritten: true, checks: 2 };
    const notes = { ...P, notes: "Sells GPUs" };
    expect(STEPS.map((s) => autoDone(s.key, notes, f))).toEqual([true, true, false, true, true, true, true]);
    // What changed is marked by hand only
    expect(stepDone("changes", { ...P, done: ["changes"] }, NONE)).toBe(true);
  });

  it("opens at the first step that isn't done", () => {
    expect(firstOpenStep({ ...P, peers: [] }, NONE)).toBe("business");
    expect(firstOpenStep({ ...P, notes: "x" }, NONE)).toBe("numbers");
    const all: ProjectState = { ...P, notes: "x", done: ["changes"] };
    expect(firstOpenStep(all, { charts: 1, models: 1, thesisWritten: true, checks: 1 })).toBe("monitor");
  });

  it("groups the steps under the header's tabs", () => {
    expect(GROUPS.map((g) => g.label)).toEqual(["Company", "Charts", "Model", "Thesis", "Monitor"]);
    expect(STEPS.map((s) => groupOf(s.key))).toEqual(["company", "charts", "company", "company", "model", "thesis", "monitor"]);
    expect(GROUPS.flatMap((g) => g.steps).sort()).toEqual(STEPS.map((s) => s.key).sort());
    expect(nextStep("business")).toBe("numbers");
    expect(nextStep("monitor")).toBeNull();
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
