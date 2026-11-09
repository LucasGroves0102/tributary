/** Backlog and orders from the filings' text (F8b, H11). The Query API decides what a passage
 * states, for which date, and which figure wins when filings disagree (query_api/text_kpis.py's
 * docstring); this only words its answer: chart series, table rows, where each figure is written.
 * Nothing here reads a number out of text or re-derives one. Pure. */

import type { Series, Source } from "../chart/series";
import { day, money } from "../format";
import { sectionName } from "../reader/sections";
import type { TextKpiMetric, TextKpiPoint, TextKpis, TextKpiSeries, TextKpiSource } from "../types";

export const TEXT_KPI_ORDER: TextKpiMetric[] = ["backlog", "orders", "orders_growth"];

/** What the card and the Numbers step call these figures, everywhere they show. */
export const FROM_TEXT = "from the filing's text";

export function hasFigures(data: TextKpis | null | undefined): boolean {
  return Boolean(data && TEXT_KPI_ORDER.some((m) => data.metrics[m]?.points.length));
}

/** A date's calendar quarter, keyed like chart/series.ts periodKey ("CY2025 Q3"), so the chart
 * builder's axis fills the quarters between figures as gaps. */
export function quarterOf(isoDate: string): { key: number; period: string } {
  const [y, m] = isoDate.slice(0, 7).split("-").map(Number);
  const q = Math.floor((m! - 1) / 3) + 1;
  return { key: y! * 4 + q - 1, period: `CY${y} Q${q}` };
}

/** Order growth is drawn for one basis: organic when the filings say organic more often. */
export function growthBasis(s: TextKpiSeries | undefined): "organic" | "reported" | null {
  if (!s?.points.length) return null;
  const organic = s.points.filter((p) => p.basis === "organic").length;
  return organic >= s.points.length - organic ? "organic" : "reported";
}

const NAMES: Record<TextKpiMetric, string> = {
  backlog: `Backlog (${FROM_TEXT})`,
  orders: `Orders in the quarter (${FROM_TEXT})`,
  orders_growth: `Order growth, year on year (${FROM_TEXT})`,
};

function sources(p: TextKpiPoint, companyId: string): Source[] {
  return p.sources.map((s) => ({
    form: s.citation.form, filed: s.citation.filed, accessionNo: s.citation.accession_no,
    url: s.citation.document_url, periodEnd: p.period_end, companyId,
  }));
}

/** The figures as the chart builder's series: backlog and orders as bars (dollars, left), order
 * growth as a line (right). One point per calendar quarter: the latest date in it. */
export function textKpiSeries(data: TextKpis, companyId: string): Series[] {
  const out: Series[] = [];
  const basis = growthBasis(data.metrics.orders_growth);
  TEXT_KPI_ORDER.forEach((metric, i) => {
    const s = data.metrics[metric];
    if (!s?.points.length) return;
    const growth = metric === "orders_growth";
    const byKey = new Map<number, TextKpiPoint>();
    for (const p of s.points) {
      if (growth && p.basis !== basis) continue;
      const { key } = quarterOf(p.period_end);
      const had = byKey.get(key);
      if (!had || had.period_end < p.period_end) byKey.set(key, p);
    }
    const points = [...byKey.entries()].sort((a, b) => a[0] - b[0]).map(([key, p]) => ({
      key, period: quarterOf(p.period_end).period, periodEnd: p.period_end,
      y: growth ? p.value / 100 : p.value, raw: String(p.value), sources: sources(p, companyId),
    }));
    out.push({
      id: i + 1,
      name: growth && basis === "organic" ? `Organic order growth, year on year (${FROM_TEXT})` : NAMES[metric],
      unit: growth ? "pct" : "USD", color: [0, 4, 2][i]!, type: growth ? "line" : "bar",
      axis: growth ? "R" : "L", points,
    });
  });
  return out;
}

/** A figure in words: "$15.00B", "60%" (a decline "-4%"), at the precision it was written. */
export function figure(p: TextKpiPoint): string {
  if (p.unit === "percent") {
    const decimals = p.precision < 1 ? Math.round(-Math.log10(p.precision)) : 0;
    return `${p.value.toFixed(decimals)}%`;
  }
  return money(p.value, "USD");
}

/** When a figure is: "Sep 30, 2025" for a backlog, "quarter to Sep 30, 2025" for orders. */
export function when(p: TextKpiPoint): string {
  return p.span === "instant" ? day(p.period_end, true) : `quarter to ${day(p.period_end, true)}`;
}

/** Where it's written: "10-Q filed Oct 22, 2025 · MD&A", "8-K filed ... · Press release". */
export function writtenIn(s: TextKpiSource): string {
  return `${s.citation.form} filed ${day(s.citation.filed, true)} · ${sectionName(s.citation.section_key)}`;
}

/** The passage around the figure, at most `max` characters, cut at word boundaries. */
export function excerpt(s: TextKpiSource, max = 240): string {
  const text = s.passage.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const at = Math.max(0, text.indexOf(s.written));
  let start = Math.max(0, at - Math.floor(max / 2));
  let end = Math.min(text.length, start + max);
  start = Math.max(0, end - max);
  if (start > 0) start = text.indexOf(" ", start) + 1 || start;
  if (end < text.length) end = text.lastIndexOf(" ", end) > start ? text.lastIndexOf(" ", end) : end;
  return `${start > 0 ? "… " : ""}${text.slice(start, end)}${end < text.length ? " …" : ""}`;
}

/** Dates the filings disagree on, in words, or null: "2 dates left out where the filings state
 * different figures; 1 settled by a table's total row or the most filings." */
export function conflictNote(data: TextKpis): string | null {
  const all = TEXT_KPI_ORDER.flatMap((m) => data.metrics[m]?.conflicts ?? []);
  if (!all.length) return null;
  const dropped = all.filter((c) => c.kept === null).length;
  const settled = all.length - dropped;
  const parts = [];
  if (dropped) parts.push(`${dropped} date${dropped === 1 ? "" : "s"} left out where the filings state different figures`);
  if (settled) {
    parts.push(`${settled} date${settled === 1 ? "" : "s"} where a segment's or another figure disagreed: the table's total, the company-wide wording or the figure more filings state was kept`);
  }
  return `${parts.join("; ")}.`;
}
