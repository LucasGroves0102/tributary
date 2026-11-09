/** Compare (comps) state: what the URL holds (`#/compare?target=…&peers=…,…&cols=…`), how it
 * maps to the Query API's /v1/screener (`company_id` = the target and its peers), and the
 * table built from the answer: cells with units, the period behind each value and its source,
 * the peer median (target excluded), and the CSV. Pure, tested without rendering. */

import { NOT_REPORTED, readFigure, type Figure } from "../blanks";
import { change, day, money, pct } from "../format";
import { num } from "../home/data";
import type { ScreenerRow, ValueSource } from "../types";
import { csvCell } from "../chart/series";
import { fyeMonth } from "../companies/screen";

export const MAX_PEERS = 20;

type Kind = "usd" | "pct" | "growth" | "x";

/** Columns a comps table can show. agent-api ui.py COMPARE_COLUMNS mirrors the first eleven keys
 * (AGENT_COLUMNS); `bank` ones (migration 048) are a bank's own lines, offered when the target is
 * a bank, and query-api workspaces.py CompsState accepts them since F4. */
export const COMPARE_COLUMNS: { key: string; label: string; long: string; period: string; kind: Kind; bank?: boolean }[] = [
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
  { key: "net_interest_income_ttm", label: "NII", long: "Net interest income", period: "TTM", kind: "usd", bank: true },
  { key: "efficiency_ratio_ttm", label: "Efficiency", long: "Efficiency ratio (noninterest expense / net revenue; lower is leaner)", period: "TTM", kind: "pct", bank: true },
  { key: "provision_for_credit_losses_ttm", label: "Provision", long: "Provision for credit losses", period: "TTM", kind: "usd", bank: true },
  { key: "loans_q", label: "Loans", long: "Loans, net of the allowance", period: "qtr end", kind: "usd", bank: true },
  { key: "deposits_q", label: "Deposits", long: "Deposits", period: "qtr end", kind: "usd", bank: true },
  { key: "cet1_ratio_q", label: "CET1", long: "CET1 capital ratio (where the bank tags it)", period: "latest", kind: "pct", bank: true },
];
/** The keys agent-api's UiContext knows (ui.py CompareColumn): uiContext sends only these. */
export const AGENT_COLUMNS = COMPARE_COLUMNS.filter((c) => !c.bank).map((c) => c.key);
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

/** A bank's comps when no columns were chosen (the Query API's BANK_PEER_COLUMNS, F4). */
export const BANK_COLUMNS = [
  "revenue_ttm",
  "net_interest_income_ttm",
  "efficiency_ratio_ttm",
  "provision_for_credit_losses_ttm",
  "loans_q",
  "deposits_q",
  "roe_ttm",
  "net_margin_ttm",
];

/** A bank by migration 035's rule: the API marks metrics that don't describe it. */
export const isBank = (row: { not_applicable?: string[] } | null | undefined): boolean =>
  !!row?.not_applicable?.length;

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

/** The columns shown: the chosen ones, else a bank's or the standard set. */
export function columnsOf(s: CompareState, bank = false): string[] {
  return s.cols?.length ? s.cols : bank ? BANK_COLUMNS : DEFAULT_COLUMNS;
}

export function companiesOf(s: CompareState): string[] {
  return [...(s.target ? [s.target] : []), ...(s.peers ?? [])];
}

/** /v1/screener parameters for the table's companies. With no columns chosen it asks for both
 * default sets, so a bank target's own columns are there without a second request. */
export function compareParams(s: CompareState): Record<string, string | number | string[]> {
  const ids = companiesOf(s);
  const cols = s.cols?.length ? s.cols : [...new Set([...DEFAULT_COLUMNS, ...BANK_COLUMNS])];
  return {
    company_id: ids,
    columns: [...cols, PERIOD_COLUMN],
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
  /** `value` is null for all of these, so a median or a sort can't take them: n/a, n/m and old */
  status?: Figure["status"];
  asOf?: string | null;
  old?: boolean;
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
 * ids) left out. `bank`: the target is a bank (its default columns). */
export function compareRows(s: CompareState, rows: ScreenerRow[], bank = false): CompareRow[] {
  const byId = new Map(rows.map((r) => [r.company_id, r]));
  const cols = columnsOf(s, bank);
  return companiesOf(s).flatMap((id) => {
    const r = byId.get(id);
    if (!r) return [];
    const readable = r.access.includes("financials");
    const cells: Record<string, CompareCell> = {};
    for (const key of cols) {
      const c = BY_KEY.get(key)!;
      const raw = readable ? r.values[key] : null;
      const source = readable ? (r.sources[key] ?? null) : null;
      if (!readable) {
        cells[key] = { value: null, raw: null, text: NOT_REPORTED, title: MASKED_TITLE, source: null };
        continue;
      }
      const figure = readFigure({
        label: c.long,
        columnKey: key,
        raw,
        reading: r.readings?.[key],
        source,
        latestPeriod: r.latest_period,
        format: (v) => formatValue(c.kind, v),
        sourceTitle: sourceTitle(key, source),
      });
      const leftOut = figure.status && figure.status !== "behind" ? " Left out of the peer median." : "";
      cells[key] = { ...figure, title: figure.title + leftOut, raw: num(raw) === null ? null : String(raw), source };
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
  /** peers whose figure counts in this column's median */
  n: number;
  /** every peer in the table (the target excluded): the column reads "n of N" */
  of: number;
  high: number | null;
  low: number | null;
  /** readable peers left out because the metric doesn't describe them (n/a: a bank) */
  banks: number;
  /** readable peers whose figure reads n/m or old, left out too */
  unusable: number;
  /** readable peers whose filings give no figure (—) */
  unreported: number;
  /** peers shown with metadata only (the plan doesn't cover their financials) */
  masked: number;
}

/** Per column: the peers' median, how many of the peers it uses (n of N) and why the others are
 * left out, and the high and low (the target excluded, and any figure that reads n/a, n/m or old:
 * its value is null). */
export function peerStats(rows: CompareRow[], cols: string[]) {
  const out: Record<string, PeerStat> = {};
  const peers = rows.filter((r) => !r.isTarget);
  for (const key of cols) {
    const v = peers.map((r) => r.cells[key]?.value).filter((x): x is number => x != null);
    const readable = peers.filter((r) => r.readable);
    const status = (r: CompareRow) => r.cells[key]?.status;
    const banks = readable.filter((r) => status(r) === "not_applicable").length;
    const unusable = readable.filter((r) => status(r) === "not_meaningful" || status(r) === "old").length;
    const unreported = readable.filter((r) => r.cells[key]?.value == null && !status(r)).length;
    out[key] = {
      median: median(v), n: v.length, of: peers.length,
      high: v.length ? Math.max(...v) : null, low: v.length ? Math.min(...v) : null,
      banks, unusable, unreported, masked: peers.length - readable.length,
    };
  }
  return out;
}

/** "3 of 4": how many peers a column's median uses. */
export const countText = (s: PeerStat): string => `${s.n} of ${s.of}`;

/** The peer-median cell's hover: what the median uses and why each other peer is left out, in the
 * blanks vocabulary (blanks.ts). */
export function medianTitle(s: PeerStat): string {
  const why = [
    s.unreported && `${s.unreported} not reported (—)`,
    s.banks && `${s.banks} not applicable (n/a: the metric doesn't describe ${s.banks === 1 ? "a bank" : "banks"})`,
    s.unusable && `${s.unusable} not meaningful or years old (n/m, dimmed)`,
    s.masked && `${s.masked} metadata only (your plan doesn't cover ${s.masked === 1 ? "its" : "their"} financials)`,
  ].filter(Boolean);
  return `Median of ${s.n} of ${s.of} peers' values; the target is excluded` +
    (why.length ? `. Left out: ${why.join("; ")}.` : ".");
}

export function statText(key: string, v: number | null): string {
  const c = BY_KEY.get(key);
  return v === null || !c ? "—" : formatValue(c.kind, v);
}

/** The table as CSV: exact values from the API, each with its period end and source
 * accession; ungranted companies with empty values and access "metadata only"; then the
 * peer median. */
export function compareCsv(s: CompareState, rows: CompareRow[], bank = false): string {
  const cols = columnsOf(s, bank);
  const header = [
    "role", "company_id", "ticker", "company", "access", "latest_quarter_calendar",
    "latest_quarter_fiscal", "latest_period_end",
    ...cols.flatMap((k) => [k, `${k}_period_end`, `${k}_source_accession`, `${k}_note`]),
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
      const note = c?.status && c.status !== "behind" ? c.status.replace("_", " ") : "";
      return [c?.raw ?? "", c?.source?.period_end ?? "", c?.source?.accession_no ?? "", note];
    }),
  ]);
  const stats = peerStats(rows, cols);
  const med = ["peer median (excludes target)", "", "", "", "", "", "", "",
    ...cols.flatMap((k) => {
      const m = stats[k]!.median;
      return [m === null ? "" : String(Number(m.toPrecision(12))), "", `${countText(stats[k]!)} peers`, ""];
    })];
  return [header, ...lines, med].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** "Chart these": the column's metric and transform in the chart builder. */
export function chartMetric(key: string): { metric: string; transform: "ttm" | "q" | "yoy" } | null {
  if (!BY_KEY.has(key)) return null;
  if (key === "revenue_growth_ttm") return { metric: "revenue", transform: "yoy" };
  return { metric: key.replace(/_(ttm|q)$/, ""), transform: key.endsWith("_q") ? "q" : "ttm" };
}

/** How a comps table is sorted: a column key, "name" or "latest"; high-first for values. */
export interface CompareSort {
  key: string;
  desc: boolean;
}

/** The next sort after clicking a header: first click high-first (names A-Z), second click the
 * other way, third click back to the order the comps were added in (null). */
export function nextSort(sort: CompareSort | null, key: string): CompareSort | null {
  const first = key !== "name";
  if (sort?.key !== key) return { key, desc: first };
  return sort.desc === first ? { key, desc: !first } : null;
}

/** Rows sorted by the column, the target among them; empty cells always last, whichever way. */
export function sortRows(rows: CompareRow[], sort: CompareSort | null): CompareRow[] {
  if (!sort) return rows;
  const by = sort.desc ? -1 : 1;
  const text = (r: CompareRow) => (sort.key === "latest" ? (r.latest?.periodEnd ?? null) : sort.key === "name" ? r.name : null);
  return [...rows].sort((a, b) => {
    if (sort.key === "name" || sort.key === "latest") {
      const x = text(a);
      const y = text(b);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
      return by * x.localeCompare(y);
    }
    const x = a.cells[sort.key]?.value ?? null;
    const y = b.cells[sort.key]?.value ?? null;
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    return by * (x - y);
  });
}
