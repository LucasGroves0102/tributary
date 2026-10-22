/** Filing markers on the chart's time axis: each chart company's 10-Ks and 10-Qs from the
 * public SEC filing index, and its 8-K results (item 2.02) where the parsed filings say so
 * (that needs the filings tier; without it there are no 8-K markers). Pure. */

import { day } from "../format";
import type { Filing, IndexedFiling } from "../types";

export type MarkerKind = "10-K" | "10-Q" | "8-K";

export interface Marker {
  companyId: string;
  accessionNo: string;
  kind: MarkerKind;
  filed: string;
  /** one letter drawn on the axis: K, Q or 8 */
  glyph: string;
  /** hover text: form, company, date, period */
  label: string;
  color: number;
}

const PERIODIC: Record<string, MarkerKind> = { "10-K": "10-K", "10-KT": "10-K", "10-Q": "10-Q", "10-QT": "10-Q" };
const GLYPH: Record<MarkerKind, string> = { "10-K": "K", "10-Q": "Q", "8-K": "8" };
export const MARKER_FORMS = Object.keys(PERIODIC);

/** Markers for one company, oldest first, filed on or after `from` (null: all). */
export function filingMarkers(
  companyId: string,
  short: string,
  color: number,
  index: IndexedFiling[],
  parsed: Filing[],
  from: string | null,
): Marker[] {
  const out = new Map<string, Marker>();
  const add = (accessionNo: string, kind: MarkerKind, filed: string, period: string | null, what: string) => {
    if (from && filed < from) return;
    out.set(accessionNo, {
      companyId, accessionNo, kind, filed, glyph: GLYPH[kind], color,
      label: `${short} ${what} filed ${day(filed, true)}${period ? `, period ${day(period, true)}` : ""} (${accessionNo})`,
    });
  };
  for (const f of index) {
    const kind = PERIODIC[f.form];
    if (kind && f.company_id === companyId) add(f.accession_no, kind, f.filed, f.period_of_report, f.form);
  }
  for (const f of parsed) {
    if (f.form.startsWith("8-K") && f.sections.some((s) => s.section_key === "2.02")) {
      add(f.accession_no, "8-K", f.filed, null, `${f.form} results (item 2.02)`);
    }
  }
  return [...out.values()].sort((a, b) => a.filed.localeCompare(b.filed));
}
