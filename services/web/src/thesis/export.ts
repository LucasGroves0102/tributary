/** What a thesis's Markdown export says beyond its text (thesis/model.ts `thesisMarkdown`):
 * each embedded item's evidence from its saved state, and the last check. Pure, tested without
 * rendering. The export carries no data the viewer's screen doesn't: a chart is named by what it
 * draws, comps by their companies and columns, a model by its assumptions; the numbers behind
 * them stay live in the app. */

import { metricLabel } from "../chart/metrics";
import { autoTitle } from "../chart/model";
import { describe, isEmpty } from "../companies/screen";
import { cleanCompare, COMPARE_COLUMNS, DEFAULT_COLUMNS } from "../compare/state";
import { RATE_LABELS, RATES, restoreModel, SCENARIO_LABELS, SCENARIOS, type Assumption } from "../models/model";
import type { ClaimStatus, ThesisCheck } from "../types";
import { restoreChartState, restoreCompare, restoreScreen } from "../workspaces/model";
import { STATUS_LABELS } from "./check";
import { blockText, type EmbedItem } from "./model";

const andList = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
const pct = (v: number) => `${v.toFixed(1)}%`;

/** Lines saying what an embedded item shows, for the export (M6: "[Comps: Comps]" said nothing). */
export function embedSummary(item: EmbedItem, label: (companyId: string) => string): string[] {
  switch (item.kind) {
    case "chart": {
      const what = autoTitle(restoreChartState(item.state), label, metricLabel);
      return what ? [`Draws ${what}.`] : [];
    }
    case "comps": {
      const c = cleanCompare(restoreCompare(item.state));
      if (!c.target) return [];
      const cols = (c.cols?.length ? c.cols : DEFAULT_COLUMNS)
        .map((k) => COMPARE_COLUMNS.find((x) => x.key === k))
        .flatMap((x) => (x ? [`${x.long.toLowerCase()} (${x.period})`] : []));
      const peers = (c.peers ?? []).map(label);
      return [`${label(c.target)}${peers.length ? ` against ${andList(peers)}` : ""}: ${andList(cols)}.`];
    }
    case "screen": {
      const f = restoreScreen(item.state);
      return [isEmpty(f) ? "Every company." : `Companies with ${describe(f)}.`];
    }
    case "model": {
      const m = restoreModel(item.state);
      if (!m) return [];
      const rates = (a: Assumption) => RATES.flatMap((r) => (a[r] === undefined ? [] : [`${RATE_LABELS[r].toLowerCase()} ${pct(a[r]!)}`]));
      const years = (list: Assumption[]) => list.map((a, i) => [`year ${i + 1}${i === 0 && m.ytd ? " (the rest of the current year)" : ""} ${pct(a.growth)} growth`,
        `${pct(a.margin)} margin`, ...rates(a)].join(", ")).join("; ");
      return [
        `${label(m.companyId)}: revenue growth and operating margin per forecast year, and any other rate set (your assumptions, not data; blank rates follow history).`,
        ...(m.ytd ? ["Year 1 is the current fiscal year: its reported quarters, then the rest at these numbers."] : []),
        ...SCENARIOS.flatMap((s) => {
          const list = s === "base" ? m.assumptions : m.scenarios?.[s];
          return list ? [`${SCENARIO_LABELS[s]}: ${years(list)}.`] : [];
        }),
        ...(m.adjustments ?? []).map((a) => `Your adjustment: ${a.name}, ${a.kind === "acquired" ? "acquired revenue" : "a one-off in operating income"} of $${(a.amount / 1e6).toFixed(1)}M in FY${a.fy} Q${a.quarter}.`),
        ...(m.price ? [`Valued at your price of $${m.price.value.toFixed(2)} on ${m.price.date}.`] : []),
      ];
    }
    default:
      return [];
  }
}

/** The last check, for the export (M6): when, whether the text changed since (stale), the
 * counts, and each claim's status, quote, explanation and the filings it cites. Model text is
 * one line each and can't open or close the export's blocks (`blockText`). */
export function checkSection(check: ThesisCheck, stale: boolean, label: (companyId: string) => string): string[] {
  const r = check.result;
  const n = (s: ClaimStatus) => r.claims.filter((c) => c.status === s).length;
  const when = r.checked_at.slice(0, 10);
  const missing = new Set(r.verification.figures.filter((f) => f.status === "unverified").map((f) => f.text));
  const lines = [
    `## Check (Tributary, ${when})`,
    "",
    stale
      ? "**Stale:** the thesis changed after this check, so it may not cover the text above."
      : "The text above is the text this check read.",
    "",
    `${n("supported")} supported, ${n("contradicted")} contradicted, ${n("unverifiable")} can't verify` +
      (missing.size ? `; figures not found in the data: ${[...missing].map(blockText).join(", ")}` : "") + ".",
    "",
  ];
  for (const c of r.claims) {
    const cites = c.citations.filter((x) => x.verified)
      .map((x) => `${x.form ?? "filing"}${x.filed ? ` filed ${x.filed}` : ""}${x.company_id ? `, ${label(x.company_id)}` : ""}, ${x.accession_no}`);
    lines.push(`- **${STATUS_LABELS[c.status]}:** “${blockText(c.quote)}” ${blockText(c.explanation)}${cites.length ? ` (${cites.join("; ")})` : ""}`);
  }
  for (const note of r.notes) lines.push("", `_${blockText(note)}_`);
  return lines;
}
