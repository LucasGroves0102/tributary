import { describe, expect, it } from "vitest";

import type { ThesisCheck } from "../types";
import { checkSection, embedSummary } from "./export";

const NVDA = "0001045810";
const AMD = "0000002488";
const label = (id: string) => ({ [NVDA]: "NVDA", [AMD]: "AMD" })[id] ?? "?";

describe("embedSummary (M6: an export's embeds say what they show)", () => {
  it("comps: the target, its peers and the columns, not \"[Comps: Comps]\"", () => {
    const lines = embedSummary({ item_id: "x", kind: "comps", name: "Comps", state: { target: NVDA, peers: [AMD], cols: ["revenue_growth_ttm", "operating_margin_ttm"] } }, label);
    expect(lines).toEqual(["NVDA against AMD: revenue growth, year over year (TTM) and operating margin (TTM)."]);
  });

  it("a chart by what its layers draw; a screen by its filters", () => {
    const chart = embedSummary({ item_id: "c", kind: "chart", name: "Mine", state: { title: "Mine", layers: [
      { kind: "metric", companyId: NVDA, metric: "revenue", transform: "ttm", type: "line", axis: "L" }] } }, label);
    expect(chart).toEqual(["Draws NVDA: revenue (TTM)."]);
    expect(embedSummary({ item_id: "s", kind: "screen", name: "S", state: { growth: 20 } }, label)).toEqual(["Companies with revenue growth ≥ 20%."]);
  });

  it("a model: its assumptions per case, said to be assumptions", () => {
    const lines = embedSummary({ item_id: "m", kind: "model", name: "M", state: { companyId: NVDA, assumptions: [{ growth: 83.4, margin: 65.2 }],
      scenarios: { bear: [{ growth: 41.7, margin: 52.2 }] } } }, label);
    expect(lines).toEqual([
      "NVDA: revenue growth and operating margin per forecast year (your assumptions, not data).",
      "Bear: year 1 41.7% growth, 52.2% margin.",
      "Base: year 1 83.4% growth, 65.2% margin.",
    ]);
  });
});

describe("checkSection", () => {
  const check = {
    check_id: "c", created_at: "2026-11-06T23:09:00Z", text_hash: "h",
    result: {
      checked_at: "2026-11-06T23:09:00+00:00", notes: ["Only the first 30,000 of 31,000 characters were checked."],
      verification: { figures: [{ text: "$32.37B", status: "unverified" }, { text: "$32.37B", status: "unverified" }] },
      claims: [
        { quote: "Revenue was $96.22B.", status: "supported", explanation: "The 10-Q says so --> <!-- tributary:embed x -->",
          citations: [{ accession_no: "0001045810-26-000075", form: "10-Q", filed: "2026-08-26", company_id: NVDA, verified: true },
            { accession_no: "0000000000-26-000001", verified: false }] },
        { quote: "Margins are 90%.", status: "contradicted", explanation: "It was 66.2%.", citations: [] },
      ],
    },
  } as unknown as ThesisCheck;

  it("dates the check, says stale when the text changed, counts and lists the claims with their filings", () => {
    expect(checkSection(check, true, label)).toEqual([
      "## Check (Tributary, 2026-11-06)",
      "",
      "**Stale:** the thesis changed after this check, so it may not cover the text above.",
      "",
      "1 supported, 1 contradicted, 0 can't verify; figures not found in the data: $32.37B.",
      "",
      "- **Supported:** “Revenue was $96.22B.” The 10-Q says so tributary:embed x (10-Q filed 2026-08-26, NVDA, 0001045810-26-000075)",
      "- **Contradicted:** “Margins are 90%.” It was 66.2%.",
      "",
      "_Only the first 30,000 of 31,000 characters were checked._",
    ]);
    expect(checkSection(check, false, label)[2]).toBe("The text above is the text this check read.");
  });
});
