import { describe, expect, it } from "vitest";

import deckEarnings from "../company/fixtures/deck-earnings.json";
import nvdaEarnings from "../company/fixtures/nvda-earnings.json";
import vrtEarnings from "../company/fixtures/vrt-earnings.json";
import type { EarningsResponse } from "../types";
import {
  adjustmentProblem,
  fromPreview,
  parseAdjustments,
  parseDraft,
  parsePriceDraft,
  resizeDrafts,
  toAdjustmentDraft,
  toDraft,
} from "./drafts";
import deckFixture from "./fixtures/deck-run.json";
import nvdaFixture from "./fixtures/nvda-run.json";
import vrtOldFixture from "./fixtures/vrt-old-run.json";
import vrtFixture from "./fixtures/vrt-run.json";
import {
  amountUnit,
  evBlank,
  formatCell,
  guidanceYear,
  modelColumns,
  modelLines,
  modelSeries,
  modelState,
  parseMillions,
  parsePercent,
  quartersLabel,
  remainderLabel,
  resizeAssumptions,
  restoreModel,
  scenarioAssumptions,
  statLabel,
  type ModelRun,
} from "./model";
import { defaultRules } from "./rules";

// real answers: the branch's POST /v1/companies/{id}/model over production inputs of 2026-11-09
// (services/query-api/tests/fixtures/model); regenerate, don't hand-edit
const vrt = vrtFixture.run as unknown as ModelRun;
const vrtOld = vrtOldFixture.run as unknown as ModelRun;
const deck = deckFixture.run as unknown as ModelRun;
const nvda = nvdaFixture.run as unknown as ModelRun;
const NVDA = "0001045810";

describe("saved states", () => {
  it("a model saved before F2 restores and saves byte-identical", () => {
    const old = { companyId: NVDA, historyYears: 8, assumptions: [{ growth: 25, margin: 55.5, note: "Blackwell ramp" }, { growth: 12.5, margin: 50 }] };
    const m = restoreModel(old)!;
    expect(JSON.stringify(modelState(m))).toBe(JSON.stringify(old));
    expect(m.ytd).toBeUndefined();
    const withCases = { ...old, scenarios: { bear: [{ growth: 5, margin: 40 }, { growth: 0, margin: 35 }], bull: [{ growth: 40, margin: 60 }, { growth: 25, margin: 58 }] } };
    expect(JSON.stringify(modelState(restoreModel(withCases)!))).toBe(JSON.stringify(withCases));
  });

  it("keeps F2's fields: rates per year, ytd, adjustments and the user's price", () => {
    const state = vrtFixture.state;
    const m = restoreModel(state)!;
    expect(m.ytd).toBe(true);
    expect(m.assumptions[1]!.taxRate).toBe(21);
    expect(m.assumptions[0]!.taxRate).toBeUndefined();
    expect(m.adjustments).toEqual([{ name: "UIG", fy: 2026, quarter: 4, kind: "acquired", amount: 150e6 }]);
    expect(m.price).toEqual({ value: 150, date: "2026-11-06" });
    expect(modelState(m)).toEqual(state);
  });

  it("drops what doesn't read, from untrusted JSON", () => {
    const m = restoreModel({
      companyId: NVDA, ytd: "yes", price: { value: -1, date: "2026-11-06" },
      adjustments: [{ name: "", fy: 2026, quarter: 1, amount: 1 }, { name: "ok", fy: 2026, quarter: 5, amount: 1 }, { name: "Charge", fy: 2026, quarter: 2, amount: -5e6, kind: "x" }],
      assumptions: [{ growth: 2000, margin: 5, shareChange: -80, taxRate: "21" }],
    })!;
    expect(m.ytd).toBeUndefined();
    expect(m.price).toBeUndefined();
    expect(m.adjustments).toEqual([{ name: "Charge", fy: 2026, quarter: 2, kind: "one_off", amount: -5e6 }]);
    expect(m.assumptions).toEqual([{ growth: 1000, margin: 5, shareChange: -50 }]);
  });

  it("reads bear and bull only with as many years as the base case", () => {
    const base = { companyId: NVDA, assumptions: [{ growth: 25, margin: 30, note: " ramp " }, { growth: 10, margin: 28 }] };
    expect(scenarioAssumptions(base, "base")).toEqual([{ growth: 25, margin: 30, note: "ramp" }, { growth: 10, margin: 28 }]);
    expect(scenarioAssumptions(base, "bear")).toBeNull();
    expect(scenarioAssumptions({ ...base, scenarios: { bear: [{ growth: 1, margin: 1 }] } }, "bear")).toBeNull();
    expect(scenarioAssumptions({ ...base, scenarios: { bull: [{ growth: 40, margin: 35 }, { growth: 20, margin: 30 }] } }, "bull")).toHaveLength(2);
  });

  it("new years repeat the last one without its note", () => {
    expect(resizeAssumptions([{ growth: 5, margin: 10, note: "x", taxRate: 20 }], 2)).toEqual([
      { growth: 5, margin: 10, note: "x", taxRate: 20 }, { growth: 5, margin: 10, taxRate: 20 }]);
  });
});

describe("drafts: what the fields hold as typed", () => {
  it("a blank rate is the default; a rate that doesn't read flags the year", () => {
    const d = toDraft({ growth: 5, margin: 20 });
    expect(parseDraft(d)).toEqual({ growth: 5, margin: 20 });
    expect(parseDraft({ ...d, taxRate: "21" })).toEqual({ growth: 5, margin: 20, taxRate: 21 });
    expect(parseDraft({ ...d, shareChange: "-60" })).toBeNull();
    expect(parseDraft({ ...d, fcfMargin: "abc" })).toBeNull();
    expect(resizeDrafts([{ ...d, taxRate: "21", note: "n" }], 2)[1]).toMatchObject({ taxRate: "21", note: "" });
  });

  it("a new bear or bull case starts from the run's preview, keeping only the rates the user set", () => {
    const bear = vrtOld.cases.bear!;
    expect(bear.preview).toBe(true);
    const drafts = fromPreview(bear.years);
    expect(drafts[0]).toMatchObject({ growth: String(bear.years[0]!.inputs.growth), margin: String(bear.years[0]!.inputs.margin), taxRate: "", note: "" });
  });

  it("adjustments: named, a fiscal quarter, $ millions", () => {
    const refund = { name: "IEEPA refunds", kind: "one_off" as const, fy: "2027", quarter: "2", amount: "46" };
    expect(parseAdjustments([refund])).toEqual([{ name: "IEEPA refunds", kind: "one_off", fy: 2027, quarter: 2, amount: 46e6 }]);
    expect(adjustmentProblem({ ...refund, name: " " })).toBe("name it");
    expect(adjustmentProblem({ ...refund, amount: "lots" })).toMatch(/\$ millions/);
    expect(parseAdjustments([{ ...refund, fy: "27" }])).toBeNull();
    expect(toAdjustmentDraft({ name: "x", kind: "one_off", fy: 2027, quarter: 2, amount: 46e6 }).amount).toBe("46");
    expect(parseMillions("$1,200.5")).toBe(1_200_500_000);
  });

  it("a price is blank (none), or a positive price with a real day", () => {
    expect(parsePriceDraft({ value: "", date: "" })).toBeUndefined();
    expect(parsePriceDraft({ value: "182.50", date: "2026-11-06" })).toEqual({ value: 182.5, date: "2026-11-06" });
    expect(parsePriceDraft({ value: "0", date: "2026-11-06" })).toBeNull();
    expect(parsePriceDraft({ value: "10", date: "2026-02-30" })).toBeNull();
    expect(parsePriceDraft({ value: "10", date: "" })).toBeNull();
    // 057: the price's currency when the user says one; "" is dollars, as before
    expect(parsePriceDraft({ value: "182.50", date: "2026-11-06", currency: "EUR" })).toEqual({ value: 182.5, date: "2026-11-06", currency: "EUR" });
    expect(parsePriceDraft({ value: "182.50", date: "2026-11-06", currency: "" })).toEqual({ value: 182.5, date: "2026-11-06" });
  });

  it("keeps a price's currency through restore and save (057), and none for a dollar one", () => {
    const m = restoreModel({ companyId: "0001674101", assumptions: [{ growth: 1, margin: 2 }], price: { value: 150, date: "2026-11-06", currency: "EUR" } })!;
    expect(m.price).toEqual({ value: 150, date: "2026-11-06", currency: "EUR" });
    expect(modelState(m).price).toEqual({ value: 150, date: "2026-11-06", currency: "EUR" });
    const usd = restoreModel({ companyId: "0001674101", assumptions: [{ growth: 1, margin: 2 }], price: { value: 150, date: "2026-11-06", currency: "euros" } })!;
    expect(modelState(usd).price).toEqual({ value: 150, date: "2026-11-06" });
  });

  it("words a euro filer's amounts in euros, and a price in another currency blocks EV (057)", () => {
    expect(amountUnit({})).toBe("USD");
    expect(amountUnit({ currency: "EUR" })).toBe("EUR");
    expect(formatCell(2097429000, "USD", "EUR")).toBe("€2.10B");
    expect(formatCell(1.22, "USD/shares", "EUR")).toBe("€1.22");
    expect(formatCell(1.22, "USD/shares")).toBe("$1.22");
    expect(formatCell(-0.5, "USD/shares")).toBe("-$0.50");
    expect(formatCell(996e9, "USD", "CNY")).toBe("CN¥996.00B");
    const euro = { ...vrt, currency: "EUR" } as ModelRun;
    expect(modelSeries(euro, 5, ["base"])[0]!.unit).toBe("EUR");
    const v = { ...vrt.valuation!, enterprise_value: null, ev_note: "currency", currency_mismatch: { price: "USD", figures: "EUR" } };
    expect(evBlank(v)).toBe("no EV: the price is in USD and the figures in EUR");
  });
});

describe("the three-case table", () => {
  it("Vertiv FY2026E is Q1–Q3 reported plus Q4, with the TTM and the reported quarters beside the history", () => {
    const cols = modelColumns(vrt, 6, 3);
    expect(cols.map((c) => c.label)).toEqual(["FY2020", "FY2021", "FY2022", "FY2023", "FY2024", "FY2025", "TTM", "FY2026 Q1–Q3", "FY2026E", "FY2027E", "FY2028E"]);
    expect(cols.find((c) => c.key === "ttm")!.sub).toBe("FY2026 Q3");
    expect(cols.find((c) => c.key === "ytd")!.sub).toBe("reported");
    expect(cols.find((c) => c.key === "f0")!.sub).toBe("Q1–Q3 reported + Q4");
    const year1 = vrt.cases.base!.years[0]!;
    expect(year1.actual!.quarters).toBe(3);
    expect(year1.revenue).toBeCloseTo(year1.actual!.revenue! + year1.remainder!.revenue, -3);
  });

  it("Deckers FY2027E is H1 reported plus H2; a model saved before F2 says its first year is a full-year guess", () => {
    expect(modelColumns(deck, 6, 2).map((c) => c.label).slice(-4)).toEqual(["TTM", "FY2027 H1", "FY2027E", "FY2028E"]);
    expect(modelColumns(vrtOld, 8, 2).find((c) => c.key === "f0")!.sub).toBe("full-year guess");
    expect(vrtOld.cases.base!.implied_remainder).not.toBeNull();
  });

  it("every line is there, with reported beside adjusted only for one-offs and organic growth only with acquired revenue", () => {
    const keys = (r: ModelRun) => modelLines(r).map((l) => l.key);
    expect(keys(vrt)).toEqual(["revenue", "growth", "organic", "oi", "margin", "nonop", "tax", "ni", "eps", "eps_growth", "shares", "share_change",
      "fcf", "fcf_margin", "buybacks", "pe", "ev_revenue", "ev_ebit", "fcf_yield"]);
    expect(keys(deck)).toEqual(expect.arrayContaining(["oi_reported", "margin_reported", "eps_reported"]));
    expect(keys(deck)).not.toContain("organic");
    expect(keys(nvda)).not.toContain("pe"); // no price, no valuation
    const margin = modelLines(deck).find((l) => l.key === "margin")!;
    expect(margin.label).toBe("Operating margin, adjusted");
    const ytd = margin.ytd!(deck.current!) as number;
    const reported = modelLines(deck).find((l) => l.key === "margin_reported")!.ytd!(deck.current!) as number;
    expect(reported - ytd).toBeCloseTo(46e6 / deck.current!.actual!.revenue!, 6);
  });

  it("a split says so instead of reading as buybacks; the year to date's net income is adjusted too", () => {
    const lines = modelLines(deck);
    const fy2023 = deck.history.find((y) => y.fy === 2023)!;
    const fy2022 = deck.history.find((y) => y.fy === 2022)!;
    const change = lines.find((l) => l.key === "share_change")!;
    expect(change.history(fy2023, 0)).toBeNull();
    expect(change.noteOf!(fy2023)).toMatch(/a split or a merger, not buybacks/);
    expect(lines.find((l) => l.key === "pe")!.history(fy2022, 0)).toBeNull();
    expect(lines.find((l) => l.key === "eps")!.noteOf!(fy2022)).toMatch(/before the split or merger in FY2023/);
    const ni = lines.find((l) => l.key === "ni")!;
    expect(ni.ytd!(deck.current!)).toBeCloseTo(deck.current!.actual!.net_income! - 46e6 * (1 - deck.current!.actual!.income_tax! / deck.current!.actual!.pretax_income!), 0);
    expect(defaultRules(deck, "base")[0]!.text).toMatch(/^FY2027E: H1 is reported/);
  });

  it("multiples at the user's price; no EV without a current net debt, and why", () => {
    const pe = modelLines(vrt).find((l) => l.key === "pe")!;
    expect(pe.forecast(vrt.cases.bull!.years[1]!, "bull", 1)).toBeCloseTo(150 / vrt.cases.bull!.years[1]!.eps_adjusted!, 6);
    const evEbit = modelLines(deck).find((l) => l.key === "ev_ebit")!;
    expect(evEbit.forecast(deck.cases.base!.years[0]!, "base", 0)).toBeNull();
    expect(evBlank(deck.valuation)).toMatch(/from 2014-09-30, over 300 days/);
    expect(evBlank(vrt.valuation)).toBeUndefined();
  });

  it("formats each unit", () => {
    expect(formatCell(13_117_100_000, "USD")).toBe("$13.12B");
    expect(formatCell(-783_200_000, "USD")).toBe("-$783.20M");
    expect(formatCell(7.02, "USD/shares")).toBe("$7.02");
    expect(formatCell(145_805_000, "shares")).toBe("145.8M");
    expect(formatCell(24.3, "multiple")).toBe("24.3x");
    expect(formatCell(0.2011, "ratio")).toBe("20.1%");
    expect(formatCell("n/m", "multiple")).toBe("n/m");
    expect(formatCell(null, "USD")).toBe("—");
  });

  it("the chart draws each case from the base year, bear and bull lighter", () => {
    const s = modelSeries(vrt, 6, ["bear", "base", "bull"]);
    expect(s.map((x) => x.name)).toEqual(["Revenue", "Revenue (bear)", "Revenue (base)", "Revenue (bull)", "Operating income",
      "Operating income (bear)", "Operating income (base)", "Operating income (bull)"]);
    expect(s.filter((x) => x.faint).map((x) => x.name)).toEqual(["Revenue (bear)", "Revenue (bull)", "Operating income (bear)", "Operating income (bull)"]);
    expect(s[1]!.points[0]!.key).toBe(2025);
    expect(modelSeries(nvda, 8, ["base"]).map((x) => x.name)).toEqual(["Revenue", "Revenue (forecast)", "Operating income", "Operating income (forecast)"]);
  });
});

describe("guidance beside the year it covers", () => {
  it("the latest release's outlook sits beside the fiscal year of the next quarter", () => {
    expect(guidanceYear(vrtEarnings as EarningsResponse, vrt)?.fy).toBe(2026);
    expect(guidanceYear(deckEarnings as EarningsResponse, deck)?.fy).toBe(2027);
    expect(guidanceYear(nvdaEarnings as EarningsResponse, nvda)?.fy).toBe(2027);
    expect(guidanceYear(null, vrt)).toBeNull();
  });

  it("a Q4 release out before its 10-K covers the year after; one older than the base year covers nothing", () => {
    const q = (vrtEarnings as EarningsResponse).quarters[0]!;
    const at = (period_end: string): EarningsResponse => ({ ...(vrtEarnings as EarningsResponse), quarters: [{ ...q, period_end }] });
    expect(guidanceYear(at("2025-12-31"), vrt)?.fy).toBe(2026);
    expect(guidanceYear(at("2026-12-31"), vrt)?.fy).toBe(2027);
    expect(guidanceYear(at("2025-06-30"), vrt)).toBeNull();
  });
});

describe("each default explained in one line", () => {
  it("Vertiv: the year to date, then the run rate fading to the long run, and each rate", () => {
    const lines = defaultRules(vrt, "base");
    expect(lines.map((l) => l.key)).toEqual(["ytd", "growth", "nonOperating", "taxRate", "shareChange", "fcfMargin"]);
    expect(lines[0]!.text).toBe("FY2026E: Q1–Q3 are reported ($10.24B, +39.3% on the same quarters a year earlier, at a 20.1% margin). "
      + "Q4 starts from Q4 FY2025 ($2.88B), grown at the trailing twelve months to FY2026 Q3's +35.3% and at its 20.1% operating margin.");
    expect(lines[1]!.text).toMatch(/^From FY2027E, growth and operating margin close half the gap each year from the trailing twelve months to FY2026 Q3/);
    expect(lines[3]!.text).toMatch(/effective rate, 19\.8%/);
    expect(lines.every((l) => !l.text.includes("\n"))).toBe(true);
  });

  it("NVIDIA: H2 starts from the trailing twelve months, not last year's H2 moved by this year's change", () => {
    const lines = defaultRules(nvda, "base");
    expect(lines[0]!.text).toMatch(/H2 starts from H2 FY2026 \(\$125\.13B\), grown at the trailing twelve months to FY2027 Q2's \+83\.4% and at its 65\.2% operating margin\.$/);
    expect(nvda.cases.base!.defaults[0]).toMatchObject({ growth: 83.4, margin: 65.2, fcfMargin: 45.6 });
    expect(lines.find((l) => l.key === "fcfMargin")!.text).toMatch(/; FY2027E's H2 at H2 FY2026's 45\.6%\.$/);
  });

  it("Deckers: shares down 8.4% a year, and a bear case's spread", () => {
    const lines = defaultRules(deck, "bear");
    expect(lines.find((l) => l.key === "shareChange")!.text).toBe(
      "Share change (buybacks less issuance): -8.4% a year, diluted shares in FY2027 Q2 against the same quarter a year earlier.");
    expect(lines.at(-1)!.key).toBe("spread");
    expect(defaultRules(vrtOld, "base")[0]!.text).toMatch(/^Growth and operating margin start at the trailing twelve months/);
  });

  it("words quarters and ranges", () => {
    expect([quartersLabel(1), quartersLabel(2), quartersLabel(3)]).toEqual(["Q1", "H1", "Q1–Q3"]);
    expect([remainderLabel(1), remainderLabel(2), remainderLabel(3)]).toEqual(["Q2–Q4", "H2", "Q4"]);
    expect(statLabel(0.081, 5, "CAGR")).toBe("5-yr CAGR 8.1%");
    expect(statLabel(null, 0, "avg")).toBe("no margin history");
    expect(parsePercent(" 12.5% ", [-100, 1000])).toBe(12.5);
  });
});
