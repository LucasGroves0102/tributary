/** Query API responses → what Home and the sidebar show. Pure functions, so the mapping (units,
 * periods, sources, what counts as new) is tested without rendering. */

import { change, day, money, pct } from "../format";
import type {
  CalendarEntry,
  ExpectedFiling,
  FinancialRow,
  IndexedFiling,
  LatestFiling,
  Num,
  ValueSource,
  WatchlistEntry,
} from "../types";

/** One number as shown: text with its unit, and a hover title with period and source. */
export interface Cell {
  value: number | null;
  text: string;
  title: string;
}

export interface WatchColumn {
  key: string;
  label: string;
  /** shown in the header: the period every value in the column covers */
  period: string;
  format: (v: number) => string;
}

export const WATCH_COLUMNS: WatchColumn[] = [
  { key: "revenue_ttm", label: "Revenue", period: "TTM", format: (v) => money(v, "USD") },
  { key: "revenue_growth_ttm", label: "YoY", period: "TTM", format: change },
  { key: "operating_margin_ttm", label: "Op margin", period: "TTM", format: (v) => pct(v) },
  { key: "free_cash_flow_ttm", label: "FCF", period: "TTM", format: (v) => money(v, "USD") },
  { key: "net_debt_q", label: "Net debt", period: "qtr end", format: (v) => money(v, "USD") },
];

const LONG_LABEL: Record<string, string> = {
  revenue_ttm: "Revenue",
  revenue_growth_ttm: "Revenue growth, year over year",
  operating_margin_ttm: "Operating margin",
  free_cash_flow_ttm: "Free cash flow",
  net_debt_q: "Net debt",
};

export interface WatchRow {
  companyId: string;
  name: string;
  ticker: string | null;
  industry: string | null;
  readable: boolean;
  last: {
    form: string;
    filed: string;
    periodEnd: string | null;
    /** fiscal label ("FY2027 Q2") when the financials say which quarter it was */
    fiscalPeriod: string | null;
    accessionNo: string;
  } | null;
  cells: Record<string, Cell>;
  next: ExpectedFiling | null;
  /** revenue by quarter, oldest first, for the sparkline (readable companies only) */
  revenue: number[];
  isNew: boolean;
}

export function num(v: Num | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function sourceTitle(key: string, src: ValueSource | null): string {
  const label = LONG_LABEL[key] ?? key;
  if (!src) return label;
  const span = key.endsWith("_ttm")
    ? `trailing twelve months to ${day(src.period_end, true)}`
    : `at the quarter end, ${day(src.period_end, true)}`;
  return `${label}, ${span}. Source: ${src.form} filed ${day(src.filed, true)}, ${src.accession_no}`;
}

function cell(e: WatchlistEntry, col: WatchColumn, readable: boolean): Cell {
  if (!readable) {
    return {
      value: null,
      text: "—",
      title: "Values need a financials grant for this company. Watching it grants nothing.",
    };
  }
  const value = num(e.values[col.key]);
  if (value === null) {
    return { value, text: "—", title: `${LONG_LABEL[col.key]}: not reported for the latest quarter` };
  }
  return { value, text: col.format(value), title: sourceTitle(col.key, e.sources[col.key] ?? null) };
}

/** Quarterly revenue rows → values oldest first, and period end → fiscal label. */
export function revenueSeries(rows: FinancialRow[]): { values: number[]; fiscal: Map<string, string> } {
  const revenue = rows
    .filter((r) => r.metric === "revenue")
    .sort((a, b) => a.period_end.localeCompare(b.period_end));
  const fiscal = new Map<string, string>();
  for (const r of rows) if (r.fiscal_period) fiscal.set(r.period_end, r.fiscal_period);
  return {
    values: revenue.map((r) => num(r.value)).filter((v): v is number => v !== null),
    fiscal,
  };
}

export function watchRows(
  entries: WatchlistEntry[],
  revenue: Record<string, FinancialRow[]>,
  newCompanies: Set<string>,
): WatchRow[] {
  return entries.map((e) => {
    const readable = e.access.includes("financials");
    const series = revenueSeries(revenue[e.company_id] ?? []);
    const lf = e.last_filing;
    return {
      companyId: e.company_id,
      name: e.name ?? e.company_id,
      ticker: e.tickers?.[0] ?? null,
      industry: e.industry,
      readable,
      last: lf && {
        form: lf.form,
        filed: lf.filing_date,
        periodEnd: lf.period_end,
        fiscalPeriod: (lf.period_end && series.fiscal.get(lf.period_end)) || null,
        accessionNo: lf.accession_no,
      },
      cells: Object.fromEntries(WATCH_COLUMNS.map((c) => [c.key, cell(e, c, readable)])),
      next: e.next_expected,
      revenue: series.values,
      isNew: newCompanies.has(e.company_id),
    };
  });
}

export interface FilingItem {
  companyId: string;
  name: string;
  accessionNo: string;
  form: string;
  filed: string;
  periodEnd: string | null;
  /** parsed text the caller can open in the reader (it came from the filings feed) */
  readable: boolean;
  isNew: boolean;
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Filings for the caller's companies in the last `days` days, newest first: the public SEC
 * index of watched and readable companies (every form, so free plans see 8-Ks too; readable
 * where the reader can open parsed sections), parsed filings from the feed, and each watched
 * company's latest 10-K/10-Q. `since` is the last visit; anything filed after it is new.
 */
export function recentFilings(
  latest: LatestFiling[],
  watched: WatchlistEntry[],
  since: string,
  today: string,
  days = 90,
  index: IndexedFiling[] = [],
): FilingItem[] {
  const from = addDays(today, -days);
  const sinceDay = since.slice(0, 10);
  const out = new Map<string, FilingItem>();
  for (const f of index) {
    out.set(f.accession_no, {
      companyId: f.company_id,
      name: f.name ?? f.company_id,
      accessionNo: f.accession_no,
      form: f.form,
      filed: f.filed,
      periodEnd: f.period_of_report,
      readable: f.sections_available,
      isNew: f.filed > sinceDay,
    });
  }
  for (const f of latest) {
    out.set(f.accession_no, {
      companyId: f.company_id,
      name: f.name,
      accessionNo: f.accession_no,
      form: f.form,
      filed: f.filed,
      periodEnd: f.period_of_report,
      readable: true,
      isNew: f.filed > sinceDay,
    });
  }
  for (const e of watched) {
    const lf = e.last_filing;
    if (!lf || out.has(lf.accession_no)) continue;
    out.set(lf.accession_no, {
      companyId: e.company_id,
      name: e.name ?? e.company_id,
      accessionNo: lf.accession_no,
      form: lf.form,
      filed: lf.filing_date,
      periodEnd: lf.period_end,
      readable: false,
      isNew: lf.filing_date > sinceDay,
    });
  }
  return [...out.values()]
    .filter((f) => f.filed >= from)
    .sort((a, b) => b.filed.localeCompare(a.filed) || b.accessionNo.localeCompare(a.accessionNo));
}

export interface ExpectedItem extends CalendarEntry {
  watched: boolean;
}

/** Watchlist and readable-company calendars merged, soonest first; watched ones marked. */
export function expectedItems(
  watched: CalendarEntry[],
  readable: CalendarEntry[],
  watchIds: Set<string>,
): ExpectedItem[] {
  const out = new Map<string, ExpectedItem>();
  for (const e of [...watched, ...readable]) {
    const k = `${e.company_id}|${e.form}|${e.period_end}`;
    if (!out.has(k)) out.set(k, { ...e, watched: watchIds.has(e.company_id) });
  }
  return [...out.values()].sort(
    (a, b) =>
      (a.expected_date ?? "").localeCompare(b.expected_date ?? "") ||
      (a.name ?? "").localeCompare(b.name ?? ""),
  );
}

export interface Status {
  companies: number;
  industries: number;
  newFilings: number;
  newPeriodic: number;
  new8k: number;
  next: { row: WatchRow; expected: ExpectedFiling } | null;
  medianGrowth: number | null;
  best: { row: WatchRow; growth: number } | null;
}

export function median(values: number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

export function status(rows: WatchRow[], filings: FilingItem[]): Status {
  const fresh = filings.filter((f) => f.isNew);
  const next = rows
    .filter((r) => r.next?.expected_date)
    .map((r) => ({ row: r, expected: r.next! }))
    .sort((a, b) => a.expected.expected_date!.localeCompare(b.expected.expected_date!))[0];
  const growth = rows
    .map((r) => ({ row: r, growth: r.cells.revenue_growth_ttm?.value ?? null }))
    .filter((g): g is { row: WatchRow; growth: number } => g.growth !== null);
  return {
    companies: rows.length,
    industries: new Set(rows.map((r) => r.industry).filter(Boolean)).size,
    newFilings: fresh.length,
    newPeriodic: fresh.filter((f) => f.form.startsWith("10-")).length,
    new8k: fresh.filter((f) => f.form.startsWith("8-K")).length,
    next: next ?? null,
    medianGrowth: median(growth.map((g) => g.growth)),
    best: growth.sort((a, b) => b.growth - a.growth)[0] ?? null,
  };
}

/** Newest ingestion time across the feed, for the freshness notes. */
export function lastIngested(latest: LatestFiling[]): string | null {
  return latest.map((f) => f.ingested_at).sort().at(-1) ?? null;
}
