import { describe, expect, it } from "vitest";

import { describeItem, reportMarkdown, restoreReport, segments, unverifiedFigures, verificationSummary } from "./model";

const NVDA = "0001045810";
const MSFT = "0000789019";
const ACC = "0001045810-26-000021";

const REPORT_STATE = {
  workspace_id: "w1",
  markdown:
    "# Semis\n\n## Summary\n\nRevenue was $46.7B (10-Q filed 2026-08-27, " + ACC + ").\n\n" +
    "## Revenue\n\n[[embed:item-1]]\n\nNext quarter $61.2B.\n\n## Comps\n\n[[embed:item-2]]\n\n" +
    "[[embed:item-7]]\n\n[[embed:item-1]]\n\nAs of 2026-10-21.",
  items: [
    { ref: "item-1", item_id: "i1", kind: "chart", name: "NVDA revenue", state: { title: "NVDA revenue", layers: [{ kind: "metric", companyId: NVDA, metric: "revenue" }] } },
    { ref: "item-2", item_id: "i2", kind: "comps", name: "Comps", state: { target: NVDA, peers: [MSFT] } },
    { ref: "item-3", item_id: "i3", kind: "notes", name: "x", state: {} },
  ],
  citations: [
    { accession_no: ACC, form: "10-Q", filed: "2026-08-27", company_id: NVDA, url: "https://www.sec.gov/Archives/x.htm", sections: [] },
    { accession_no: "0000789019-26-000001", form: "10-K", filed: "2026-07-30", company_id: MSFT, url: "javascript:alert(1)" },
    { accession_no: "../../etc", form: "10-K" },
  ],
  verification: {
    ok: false,
    figures: [{ text: "$46.7B", status: "verified" }, { text: "$61.2B", status: "unverified" }, { text: "$1B", status: "trusted" }],
    unverified_accessions: [],
    used_data: true,
    freshness: true,
  },
  as_of: "2026-10-21T08:00:00+00:00",
  written_at: "2026-10-21T09:00:00+00:00",
  model: "claude-haiku-5-5",
  questions: 3,
  stop: "end_turn",
};

describe("restoreReport", () => {
  it("keeps only well-formed parts", () => {
    const r = restoreReport(REPORT_STATE)!;
    expect(r.items.map((i) => i.ref)).toEqual(["item-1", "item-2"]); // a "notes" item is no kind
    expect(r.citations.map((c) => c.accession_no)).toEqual([ACC, "0000789019-26-000001"]);
    expect(r.citations[1]!.url).toBeNull(); // only EDGAR links
    expect(r.verification.figures).toHaveLength(2);
    expect(r.asOf).toBe("2026-10-21T08:00:00+00:00");
  });

  it("refuses what isn't a report", () => {
    expect(restoreReport(null)).toBeNull();
    expect(restoreReport({ markdown: "x" })).toBeNull();
    expect(restoreReport({ verification: {} })).toBeNull();
    expect(restoreReport([1, 2])).toBeNull();
  });
});

describe("segments", () => {
  it("splits at embeds, once per item, dropping unknown ones", () => {
    const s = segments(restoreReport(REPORT_STATE)!);
    expect(s.map((x) => (x.type === "embed" ? x.item.ref : "text"))).toEqual([
      "text", "item-1", "text", "item-2", "text",
    ]);
    expect(s.at(-1)).toEqual({ type: "text", markdown: "As of 2026-10-21." });
  });

  it("an embed marker must be its own line", () => {
    const r = restoreReport({ ...REPORT_STATE, markdown: "Look: [[embed:item-1]] here" })!;
    expect(segments(r)).toEqual([{ type: "text", markdown: "Look: [[embed:item-1]] here" }]);
  });
});

describe("verification", () => {
  it("lists unverified figures and summarizes", () => {
    const r = restoreReport(REPORT_STATE)!;
    expect(unverifiedFigures(r)).toEqual(["$61.2B"]);
    expect(verificationSummary(r)).toEqual({ ok: false, text: "1 of 2 figures unverified (highlighted)" });
    const ok = restoreReport({ ...REPORT_STATE, verification: { ...REPORT_STATE.verification, figures: [{ text: "$46.7B", status: "verified" }] } })!;
    expect(verificationSummary(ok)).toEqual({ ok: true, text: "All 1 figure verified against the data" });
    const stale = restoreReport({ ...REPORT_STATE, verification: { figures: [], used_data: true, freshness: false } })!;
    expect(verificationSummary(stale).ok).toBe(false);
  });
});

describe("Markdown export", () => {
  const label = (id: string) => ({ [NVDA]: "NVDA", [MSFT]: "MSFT" })[id] ?? id;

  it("describes embeds, marks unverified figures and lists sources", () => {
    const md = reportMarkdown(restoreReport(REPORT_STATE)!, "Report: Semis", label);
    expect(md).toContain("> **NVDA revenue**: Chart “NVDA revenue”: NVDA revenue. Drawn from the saved chart in Tributary.");
    expect(md).toContain("> **Comps**: Comps: NVDA vs MSFT.");
    expect(md).toContain("Next quarter $61.2B [unverified].");
    expect(md).toContain("Revenue was $46.7B (");
    expect(md).not.toContain("$46.7B [unverified]");
    expect(md).toContain("data as of 2026-10-21T08:00:00+00:00");
    expect(md).toContain(`- 10-Q filed 2026-08-27, ${ACC}: https://www.sec.gov/Archives/x.htm`);
    expect(md).not.toContain("[[embed:");
    expect(md).not.toContain("javascript:");
  });

  it("describes screens by their filters", () => {
    expect(describeItem({ ref: "item-1", item_id: "x", kind: "screen", name: "s", state: { industry: "software", margin: 20, sort: "-revenue_ttm" } }, label))
      .toBe("Screen: industry software, margin 20");
  });
});
