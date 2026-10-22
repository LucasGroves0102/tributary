/** Compare (comps) state: what the URL holds (`#/compare?target=…&peers=…,…&cols=…`), how it
 * maps to the Query API's /v1/screener (`company_id` = the target and its peers), and the
 * table built from the answer: cells with units, the period behind each value and its source,
 * the peer median (target excluded), and the CSV. Pure, tested without rendering. */

import { BANK_NA, bankTitle, change, day, money, notForBanks, pct } from "../format";
import { num } from "../home/data";
import type { ScreenerRow, ValueSource } from "../types";
import { csvCell } from "../chart/series";
import { fyeMonth } from "../companies/screen";

export const MAX_PEERS = 20;

type Kind = "usd" | "pct" | "growth" | "x";

/** Columns a comps table can show (agent-api ui.py COMPARE_COLUMNS mirrors the keys). */
export const COMPARE_COLUMNS: { key: string; label: string; long: string; period: string; kind: Kind }[] = [
  { key: "revenue_ttm", label: "Revenue", long: "Revenue", period: "TTM", kind: "usd" },
  { key: "revenue_growth_ttm", label: "Rev YoY", long: "Revenue growth, year over year", period: "TTM", kind: "growth" },
  { key: "gross_margin_ttm", label: "Gross mgn", long: "Gross margin", period: "TTM", kind: "pct" },
  { key: "operating_margin_ttm", label: "Op mgn", long: "Operating margin", period: "TTM", kind: "pct" },
  { key: "net_margin_ttm", label: "Net mgn", long: "Net margin", period: "TTM", kind: "pct" },
  { key: "free_cash_flow_ttm", label: "FCF", long: "Free cash flow", period: "TTM", kind: "usd" },
  { key: "fcf_margin_ttm", label: "FCF mgn", long: "Free cash flow margin", period: "TTM", kind: "pct" },
  { key: "net_debt_q", label: "Net debt", long: "Net debt", period: "qtr end", kind: "usd" },
  { key: "debt_to_equity_q", label: "Debt/eq", long: "Debt to equity", period: "qtr end", kind: "x" },
  { key: "roe_ttm", label: "ROE", long: "Return on equity", period: "TTM", kind: "pct" },
  { key: "current_ratio_q", label: "Current ratio", long: "Current ratio", period: "qtr end", kind: "x" },
];
const BY_KEY = new Map(COMPARE_COLUMNS.map((c) => [c.key, c]));

export const DEFAULT_COLUMNS = [
  "revenue_ttm",
  "revenue_growth_ttm",
  "gross_margin_ttm",
  "operating_margin_ttm",
  "free_cash_flow_ttm",
  "fcf_margin_ttm",
  "net_debt_q",
  "debt_to_equity_q",
];

/** Read with the table so every row has a latest quarter even if a chosen column is empty. */
const PERIOD_COLUMN = "revenue_q";

export interface CompareState {
  target?: string;
  peers?: string[];
  /** column keys; unset = DEFAULT_COLUMNS */
  cols?: string[];
}

const CIK = /^\d{10}$/;

/** Unknown ids and columns are dropped, the target never repeats as a peer, at most
 * MAX_PEERS peers. */
export function cleanCompare(s: CompareState): CompareState {
  const target = s.target && CIK.test(s.target) ? s.target : undefined;
  const peers = [...new Set((s.peers ?? []).filter((p) => CIK.test(p) && p !== target))].slice(0, MAX_PEERS);
  const cols = [...new Set((s.cols ?? []).filter((c) => BY_KEY.has(c)))];
  const out: CompareState = {};
  if (target) out.target = target;
  if (peers.length) out.peers = peers;
  if (cols.length && cols.join() !== DEFAULT_COLUMNS.join()) out.cols = cols;
  return out;
}

export function parseCompare(query: string): CompareState {
  const p = new URLSearchParams(query);
  const list = (k: string) => (p.get(k) ?? "").split(",").filter(Boolean);
  return cleanCompare({ target: p.get("target") ?? undefined, peers: list("peers"), cols: list("cols") });
}

/** The URL query ("" for an empty table), in a fixed key order. */
export function compareQuery(s: CompareState): string {
  const c = cleanCompare(s);
  const parts = [
    c.target && `target=${c.target}`,
    c.peers && `peers=${c.peers.join(",")}`,
    c.cols && `cols=${c.cols.join(",")}`,
  ].filter(Boolean);
  return parts.join("&");
}

export function columnsOf(s: CompareState): string[] {
  return s.cols?.length ? s.cols : DEFAULT_COLUMNS;
}

export function companiesOf(s: CompareState): string[] {
  return [...(s.target ? [s.target] : []), ...(s.peers ?? [])];
}

/** /v1/screener parameters for the table's companies. */
export function compareParams(s: CompareState): Record<string, string | number | string[]> {
  const ids = companiesOf(s);
  return {
    company_id: ids,
    columns: [...columnsOf(s), PERIOD_COLUMN],
    sort: "name",
    limit: Math.max(ids.length, 1),
  };
}

/** Calendar quarter a period ending on `periodEnd` belongs to (gold.calendar_quarter: the
 * quarter of periodEnd - 35 days): "CY2026 Q2". */
export function calendarLabel(periodEnd: string): string {
  const d = new Date(`${periodEnd.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 35);
  return `CY${d.getUTCFullYear()} Q${Math.floor(d.getUTCMonth() / 3) + 1}`;
}

const DAY = 86400000;

/** Fiscal quarter label from the period end and the SEC fiscal year end (MMDD), as gold
 * labels it (migration 014: the first nominal fiscal year end on or after periodEnd - 10 days;
 * a year ending in the first week of January is named for the year before). */
export function fiscalLabel(periodEnd: string, fye: string | null): string | null {
  if (!fye || !/^\d{4}$/.test(fye)) return null;
  const end = Date.parse(`${periodEnd.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(end)) return null;
  const nominal = (y: number) => Date.UTC(y, Number(fye.slice(0, 2)) - 1, 1) + (Number(fye.slice(2)) - 1) * DAY;
  const year = new Date(end - 10 * DAY).getUTCFullYear();
  const fyEnd = nominal(year) >= end - 10 * DAY ? nominal(year) : nominal(year + 1);
  const fy = new Date(fyEnd - 7 * DAY).getUTCFullYear();
  const q = Math.max(1, Math.min(4, 4 - Math.round((fyEnd - end) / DAY / 91.31)));
  return `FY${fy} Q${q}`;
}

/** "CY2026 Q2" → "CY26 Q2" for narrow cells. */
export const short = (label: string) => label.replace(/^(CY|FY)\d\d(\d\d)/, "$1$2");

export interface CompareCell {
  value: number | null;
  raw: string | null;
  text: string;
  title: string;
  source: ValueSource | null;
  /** a bank's operating margin / FCF / net debt: shown as n/a, never in the peer median */
  bank?: true;
}

export interface CompareRow {
  companyId: string;
  name: string;
  ticker: string | null;
  industry: string | null;
  sic: string | null;
  fye: string | null;
  isTarget: boolean;
  readable: boolean;
  /** the latest quarter the values run to, or null without readable values */
  latest: { periodEnd: string; calendar: string; fiscal: string | null; form: string; filed: string } | null;
  cells: Record<string, CompareCell>;
}

export const MASKED_TITLE =
  "Your plan doesn't cover this company's financials, so only its public metadata is shown. " +
  "It is left out of the median.";

export function formatValue(kind: Kind, v: number): string {
  switch (kind) {
    case "usd":
      return money(v, "USD");
    case "pct":
      return pct(v);
    case "growth":
      return change(v);
    case "x":
      return `${v.toFixed(2)}x`;
  }
}

function sourceTitle(key: string, src: ValueSource | null): string {
  const c = BY_KEY.get(key);
  const label = c?.long ?? key;
  if (!src) return label;
  const span = key.endsWith("_ttm")
    ? `trailing twelve months to ${day(src.period_end, true)}`
    : `at the quarter end, ${day(src.period_end, true)}`;
  return `${label}, ${span} (${calendarLabel(src.period_end)}). Source: ${src.form} filed ` +
    `${day(src.filed, true)}, ${src.accession_no}`;
}

function latestOf(r: ScreenerRow): CompareRow["latest"] {
  const srcs = Object.values(r.sources).filter((s): s is ValueSource => !!s);
  if (!srcs.length) return null;
  const s = srcs.reduce((a, b) => (b.period_end > a.period_end ? b : a));
  return {
    periodEnd: s.period_end,
    calendar: calendarLabel(s.period_end),
    fiscal: fiscalLabel(s.period_end, r.fiscal_year_end),
    form: s.form,
    filed: s.filed,
  };
}

/** Rows in the state's order (target first), with companies the API didn't return (unknown
 * ids) left out. */
export function compareRows(s: CompareState, rows: ScreenerRow[]): CompareRow[] {
  const byId = new Map(rows.map((r) => [r.company_id, r]));
  const cols = columnsOf(s);
  return companiesOf(s).flatMap((id) => {
    const r = byId.get(id);
    if (!r) return [];
    const readable = r.access.includes("financials");
    const cells: Record<string, CompareCell> = {};
    for (const key of cols) {
      const c = BY_KEY.get(key)!;
      const raw = readable ? r.values[key] : null;
      const value = num(raw);
      const source = readable ? (r.sources[key] ?? null) : null;
      const bank = notForBanks(r.industry, key);
      cells[key] = !readable
        ? { value: null, raw: null, text: "—", title: MASKED_TITLE, source: null }
        : bank && value === null
          ? { value, raw: null, text: BANK_NA, title: `${bankTitle(c.long)} Left out of the peer median.`, source, bank }
        : bank
          ? { value, raw: String(raw), text: formatValue(c.kind, value!), title: `${sourceTitle(key, source)}. A bank: left out of the peer median.`, source, bank }
        : value === null
          ? { value, raw: null, text: "—", title: `${c.long}: not reported for the latest period`, source }
          : { value, raw: String(raw), text: formatValue(c.kind, value), title: sourceTitle(key, source), source };
    }
    return [{
      companyId: id,
      name: r.name ?? id,
      ticker: r.tickers?.[0] ?? null,
      industry: r.industry,
      sic: r.sic,
      fye: fyeMonth(r.fiscal_year_end),
      isTarget: id === s.target,
      readable,
      latest: readable ? latestOf(r) : null,
      cells,
    }];
  });
}

export function median(values: number[]): number | null {
  if (!values.length) return null;
  const v = [...values].sort((a, b) => a - b);
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m]! : (v[m - 1]! + v[m]!) / 2;
}

export interface PeerStat {
  median: number | null;
  n: number;
  high: number | null;
  low: number | null;
  /** readable bank peers left out of this column (the metric doesn't describe a bank) */
  banks: number;
}

/** Per column: the peers' median, its count, and the high and low (the target and, for
 * operating margin / FCF / net debt, banks excluded). */
export function peerStats(rows: CompareRow[], cols: string[]) {
  const out: Record<string, PeerStat> = {};
  for (const key of cols) {
    const peers = rows.filter((r) => !r.isTarget);
    const v = peers.filter((r) => !r.cells[key]?.bank).map((r) => r.cells[key]?.value).filter((x): x is number => x != null);
    const banks = peers.filter((r) => r.cells[key]?.bank).length;
    out[key] = { median: median(v), n: v.length, high: v.length ? Math.max(...v) : null, low: v.length ? Math.min(...v) : null, banks };
  }
  return out;
}

/** The peer-median cell's hover. */
export function medianTitle(s: PeerStat): string {
  const banks = s.banks ? `; ${s.banks} ${s.banks === 1 ? "bank" : "banks"} left out (the metric doesn't describe a bank)` : "";
  return `Median of ${s.n} peers' values; the target is excluded${banks}`;
}

export function statText(key: string, v: number | null): string {
  const c = BY_KEY.get(key);
  return v === null || !c ? "—" : formatValue(c.kind, v);
}

/** The table as CSV: exact values from the API, each with its period end and source
 * accession; ungranted companies with empty values and access "metadata only"; then the
 * peer median. */
export function compareCsv(s: CompareState, rows: CompareRow[]): string {
  const cols = columnsOf(s);
  const header = [
    "role", "company_id", "ticker", "company", "access", "latest_quarter_calendar",
    "latest_quarter_fiscal", "latest_period_end",
    ...cols.flatMap((k) => [k, `${k}_period_end`, `${k}_source_accession`]),
  ];
  const lines = rows.map((r) => [
    r.isTarget ? "target" : "peer",
    r.companyId,
    r.ticker ?? "",
    r.name,
    r.readable ? "financials" : "metadata only",
    r.latest?.calendar ?? "",
    r.latest?.fiscal ?? "",
    r.latest?.periodEnd ?? "",
    ...cols.flatMap((k) => {
      const c = r.cells[k];
      return [c?.raw ?? "", c?.source?.period_end ?? "", c?.source?.accession_no ?? ""];
    }),
  ]);
  const stats = peerStats(rows, cols);
  const med = ["peer median (excludes target)", "", "", "", "", "", "", "",
    ...cols.flatMap((k) => {
      const m = stats[k]!.median;
      return [m === null ? "" : String(Number(m.toPrecision(12))), "", `${stats[k]!.n} peers`];
    })];
  return [header, ...lines, med].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** "Chart these": the column's metric and transform in the chart builder. */
export function chartMetric(key: string): { metric: string; transform: "ttm" | "q" | "yoy"; title: string } | null {
  const c = BY_KEY.get(key);
  if (!c) return null;
  if (key === "revenue_growth_ttm") return { metric: "revenue", transform: "yoy", title: "Revenue growth, YoY (TTM)" };
  const quarterEnd = key.endsWith("_q");
  return {
    metric: key.replace(/_(ttm|q)$/, ""),
    transform: quarterEnd ? "q" : "ttm",
    title: `${c.long}, ${quarterEnd ? "quarter end" : "TTM"}`,
  };
}
