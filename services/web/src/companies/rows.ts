/** Screener rows → table cells: units, the period behind each value, its source filing, and an
 * honest note where the API masked a company's values. Pure, tested without rendering. */

import { NOT_REPORTED, readFigure } from "../blanks";
import { day, UNNAMED } from "../format";
import type { Cell } from "../home/data";
import type { ScreenerRow, ValueSource } from "../types";
import { columnSpec, formatColumn, type ColumnSpec } from "../views/model";
import { fyeMonth, SCREEN_COLUMNS } from "./screen";

/** The default columns as specs (their labels and kinds as companies/screen.ts has them). */
export const DEFAULT_SPECS: ColumnSpec[] = SCREEN_COLUMNS.map((c) => ({ ...columnSpec(c.key), label: c.label, period: c.period, kind: c.kind }));

export const MASKED_TITLE =
  "Your plan doesn't cover this company's financials, so the API returns its public metadata " +
  "only. It can't match value filters or size bands.";

const LONG: Record<string, string> = {
  revenue_ttm: "Revenue",
  revenue_growth_ttm: "Revenue growth, year over year",
  gross_margin_ttm: "Gross margin",
  operating_margin_ttm: "Operating margin",
  free_cash_flow_ttm: "Free cash flow",
  net_debt_q: "Net debt",
  debt_to_equity_q: "Debt to equity",
};

export interface ScreenRow {
  companyId: string;
  name: string;
  ticker: string | null;
  exchange: string | null;
  industry: string | null;
  sic: string | null;
  /** 046's grouping (null from an older API or without a SIC) */
  sector: string | null;
  sectorIndustry: string | null;
  /** companies filing one combined 10-K with it (#135) */
  coRegistrants: { company_id: string; name: string; filed_by: string | null }[];
  fye: string | null;
  readable: boolean;
  cells: Record<string, Cell>;
}

function title(key: string, src: ValueSource | null, long?: string): string {
  const label = long ?? LONG[key] ?? key;
  if (!src) return label;
  const span = key.endsWith("_ttm")
    ? `trailing twelve months to ${day(src.period_end, true)}`
    : `at the quarter end, ${day(src.period_end, true)}`;
  return `${label}, ${span}. Source: ${src.form} filed ${day(src.filed, true)}, ${src.accession_no}`;
}

/** The table's foot names its currency: dollars, unless the page holds an amount in another one (a
 * 20-F filer's TWD or CNY: shown in its own currency and never ranked with dollars, migration 056).
 * Riley 2: TSMC's "TWD 2.89T" sat under a foot that said "US dollars". A ratio sorts across currencies. */
export function currencyFoot(rows: ScreenRow[]): string {
  const others = new Set<string>();
  for (const r of rows) {
    for (const c of Object.values(r.cells)) {
      if (c.status === "other_currency" && c.currency) others.add(c.currency);
    }
  }
  if (others.size === 0) return "US dollars.";
  return `US dollars, except the amounts shown in ${[...others].sort().join(", ")}: those companies report in ` +
    "their own currency, so their amounts aren't sorted, filtered or averaged with dollars (their ratios are).";
}

export function screenRow(r: ScreenerRow, columns: ColumnSpec[] = DEFAULT_SPECS): ScreenRow {
  const readable = r.access.includes("financials");
  const cells: Record<string, Cell> = {};
  for (const c of columns) {
    const long = LONG[c.key] ?? c.long;
    const source = r.sources[c.key] ?? null;
    cells[c.key] = !readable
      ? { value: null, text: NOT_REPORTED, title: MASKED_TITLE }
      : readFigure({
          label: long,
          columnKey: c.key,
          raw: r.values[c.key],
          reading: r.readings?.[c.key],
          source,
          latestPeriod: r.latest_period,
          format: (v) => formatColumn(c.kind, v),
          sourceTitle: title(c.key, source, long),
        });
  }
  return {
    companyId: r.company_id,
    name: r.name ?? UNNAMED,
    ticker: r.tickers?.[0] ?? null,
    exchange: r.exchanges?.[0] ?? null,
    industry: r.industry,
    sic: r.sic,
    sector: r.sector ?? null,
    sectorIndustry: r.sector_industry ?? null,
    coRegistrants: (r.co_registrants ?? []).map((c) => ({ company_id: c.company_id, name: c.name, filed_by: c.filed_by })),
    fye: fyeMonth(r.fiscal_year_end),
    readable,
    cells,
  };
}
