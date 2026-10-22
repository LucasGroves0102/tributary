/** Deck output: the subtitle and source footnote a slide needs, from what is plotted. Pure, so
 * the wording is tested; ChartBuilder passes them to LayerChart, which draws them into the
 * SVG, so SVG and PNG exports carry them. */

import { day } from "../format";
import type { Align, Frequency } from "./model";
import { unitLabel } from "./metrics";
import { xAxis, type Series } from "./series";

/** "Fiscal quarters, FY2024 Q1–FY2026 Q2 · USD (left), % (right)". */
export function autoSubtitle(series: Series[], frequency: Frequency, basis: Align): string {
  const ticks = xAxis(series);
  const what = frequency === "annual"
    ? basis === "fiscal" ? "Fiscal years" : "Calendar years"
    : basis === "fiscal" ? "Fiscal quarters" : "Calendar quarters";
  const span = ticks.length ? `, ${ticks[0]!.period}${ticks.length > 1 ? `–${ticks.at(-1)!.period}` : ""}` : "";
  const units = (axis: "L" | "R") => [...new Set(series.filter((s) => s.axis === axis).map((s) => unitLabel(s.unit)))];
  const left = units("L");
  const right = units("R");
  const unitText = right.length
    ? `${left.join(", ") || "—"} (left), ${right.join(", ")} (right)`
    : left.join(", ");
  return `${what}${span}${unitText ? ` · ${unitText}` : ""}`;
}

/** "Source: SEC filings via Tributary; 10-K, 10-Q for KO, PEP; latest PEP 10-Q filed Jul 22,
 * 2026; as of Oct 21, 2026." Forms and companies come from every plotted point's filings. */
export function sourceFootnote(series: Series[], short: (companyId: string) => string, asOf: string | null): string {
  const forms = new Set<string>();
  const companies = new Set<string>();
  let latest: Series["points"][number]["sources"][number] | null = null;
  for (const s of series) {
    for (const p of s.points) {
      for (const src of p.sources) {
        forms.add(src.form);
        if (src.companyId) companies.add(short(src.companyId));
        if (!latest || src.filed > latest.filed) latest = src;
      }
    }
  }
  const parts = ["Source: SEC filings via Tributary"];
  if (forms.size) {
    parts.push(`${[...forms].sort().join(", ")}${companies.size ? ` for ${[...companies].sort().join(", ")}` : ""}`);
  }
  if (latest) {
    parts.push(`latest ${latest.companyId ? `${short(latest.companyId)} ` : ""}${latest.form} filed ${day(latest.filed, true)}`);
  }
  if (asOf) parts.push(`as of ${day(asOf, true)}`);
  return `${parts.join("; ")}.`;
}

/** How the values were derived, for the footnote's second line. */
export function methodNote(basis: Align, frequency: Frequency, transforms: Set<string>): string {
  const annual = frequency === "annual";
  return (
    `${basis === "fiscal" ? "Fiscal" : "Calendar"} ${annual ? "years" : "quarters"}` +
    (annual
      ? `: flows are ${basis === "fiscal" ? "fiscal" : "calendar"}-year totals (TTM at Q4), balances year-end values`
      : "; TTM = trailing four quarters") +
    (transforms.has("yoy") || (annual && transforms.has("qoq")) ? (annual ? "; YoY over years" : "; YoY over TTM") : "") +
    (!annual && transforms.has("qoq") ? "; QoQ over quarters" : "") +
    (transforms.has("ps") ? "; per share = TTM USD / diluted weighted shares of the quarter" : "") +
    "."
  );
}
