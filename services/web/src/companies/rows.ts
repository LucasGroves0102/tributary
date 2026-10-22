/** Screener rows → table cells: units, the period behind each value, its source filing, and an
 * honest note where the API masked a company's values. Pure, tested without rendering. */

import { BANK_NA, bankTitle, day, notForBanks } from "../format";
import { num, type Cell } from "../home/data";
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

export function screenRow(r: ScreenerRow, columns: ColumnSpec[] = DEFAULT_SPECS): ScreenRow {
  const readable = r.access.includes("financials");
  const cells: Record<string, Cell> = {};
  for (const c of columns) {
    const long = LONG[c.key] ?? c.long;
    const value = readable ? num(r.values[c.key]) : null;
    cells[c.key] = !readable
      ? { value, text: "—", title: MASKED_TITLE }
      : value === null && notForBanks(r.industry, c.key)
        ? { value, text: BANK_NA, title: bankTitle(long) }
        : value === null
        ? { value, text: "—", title: `${long}: not reported for the latest period` }
        : { value, text: formatColumn(c.kind, value), title: title(c.key, r.sources[c.key] ?? null, long) };
  }
  return {
    companyId: r.company_id,
    name: r.name ?? r.company_id,
    ticker: r.tickers?.[0] ?? null,
    exchange: r.exchanges?.[0] ?? null,
    industry: r.industry,
    sic: r.sic,
    fye: fyeMonth(r.fiscal_year_end),
    readable,
    cells,
  };
}
