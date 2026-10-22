/** Customizable views: Home's card arrangement and the screener's and watchlist's column sets,
 * saved per user (Query API /v1/views, query_api/views.py, which validates the same catalogs).
 * Saved preferences are untrusted here too: unknown cards and columns are dropped and a
 * missing or broken one is the default. Pure, tested without rendering. */

import { WATCHLIST_COLUMNS } from "../api";
import { metricLabel } from "../chart/metrics";
import { SCREEN_COLUMNS } from "../companies/screen";
import { change, money, pct } from "../format";
import type { ColumnInfo, HomeCardPref } from "../types";

/** Home's cards in their default order (query_api/views.py HOME_CARDS). */
export const HOME_CARDS = ["status", "recent", "watchlist", "filings", "expected", "notes"] as const;
export type HomeCardId = (typeof HOME_CARDS)[number];
export const HOME_CARD_LABELS: Record<HomeCardId, string> = {
  status: "Status strip",
  recent: "Continue where you left off",
  watchlist: "Watchlist",
  filings: "New filings",
  expected: "Expected filings",
  notes: "Recent data notes",
};

export interface HomeCard {
  id: HomeCardId;
  hidden: boolean;
}

export const MAX_SCREENER_COLUMNS = 12;
export const MAX_WATCHLIST_COLUMNS = 8;
export const DEFAULT_SCREENER_COLUMNS = SCREEN_COLUMNS.map((c) => c.key);
export const DEFAULT_WATCHLIST_COLUMNS = [...WATCHLIST_COLUMNS];

const isCard = (id: string): id is HomeCardId => (HOME_CARDS as readonly string[]).includes(id);

/** Every card once: the saved order (unknown ids dropped), then any card it doesn't list. */
export function homeLayout(saved: { cards: HomeCardPref[] } | null | undefined): HomeCard[] {
  const out: HomeCard[] = [];
  for (const c of saved?.cards ?? []) {
    if (isCard(c.id) && !out.some((x) => x.id === c.id)) out.push({ id: c.id, hidden: c.hidden === true });
  }
  for (const id of HOME_CARDS) if (!out.some((x) => x.id === id)) out.push({ id, hidden: false });
  return out;
}

export const defaultHome = (): HomeCard[] => homeLayout(null);

export function isDefaultHome(cards: HomeCard[]): boolean {
  return cards.every((c, i) => c.id === HOME_CARDS[i] && !c.hidden);
}

/** Moves a card (or column) one place up (-1) or down (1); unchanged at the ends. */
export function move<T>(list: T[], index: number, by: -1 | 1): T[] {
  const to = index + by;
  if (index < 0 || to < 0 || to >= list.length) return list;
  const out = [...list];
  [out[index], out[to]] = [out[to]!, out[index]!];
  return out;
}

export function toggleCard(cards: HomeCard[], id: HomeCardId): HomeCard[] {
  return cards.map((c) => (c.id === id ? { ...c, hidden: !c.hidden } : c));
}

/** A saved column set as it applies: known keys only (when the API's catalog is known), no
 * repeats, at most `max`; nothing left means the default. */
export function columnList(
  saved: { columns: string[] } | null | undefined,
  options: ColumnInfo[] | null,
  fallback: string[],
  max: number,
): string[] {
  const known = options ? new Set(options.map((o) => o.key)) : null;
  const out = [...new Set(saved?.columns ?? [])].filter((k) => typeof k === "string" && (!known || known.has(k))).slice(0, max);
  return out.length ? out : fallback;
}

export function sameColumns(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((k, i) => k === b[i]);
}

export type ColumnKind = "usd" | "pct" | "growth" | "x" | "shares" | "eps";

/** A value column as tables show it: header label and period, long name, how values read. */
export interface ColumnSpec {
  key: string;
  label: string;
  long: string;
  period: string;
  kind: ColumnKind;
}

const SHORT: Record<string, { label: string; long: string }> = {
  revenue_ttm: { label: "Revenue", long: "Revenue" },
  revenue_growth_ttm: { label: "Rev growth", long: "Revenue growth, year over year" },
  gross_margin_ttm: { label: "Gross mgn", long: "Gross margin" },
  operating_margin_ttm: { label: "Op mgn", long: "Operating margin" },
  free_cash_flow_ttm: { label: "FCF", long: "Free cash flow" },
  net_debt_q: { label: "Net debt", long: "Net debt" },
  debt_to_equity_q: { label: "Debt/eq", long: "Debt to equity" },
};
const MULTIPLES = new Set(["debt_to_equity", "current_ratio"]);

/** The spec for a column key, from the API's catalog entry when known. */
export function columnSpec(key: string, info?: ColumnInfo): ColumnSpec {
  const span = info?.span ?? (key.endsWith("_ttm") ? "ttm" : "quarter");
  const metric = info?.metric ?? key.replace(/_(ttm|q)$/, "");
  const unit = info?.unit ?? (/margin|growth|ratio|roe|equity/.test(metric) ? "ratio" : "USD");
  const kind: ColumnKind =
    unit === "USD" ? "usd"
      : unit === "shares" ? "shares"
        : unit === "USD/shares" ? "eps"
          : metric.endsWith("_growth") ? "growth"
            : MULTIPLES.has(metric) ? "x"
              : "pct";
  const known = SHORT[key];
  const long = known?.long ?? metricLabel(metric);
  return {
    key,
    label: known?.label ?? long,
    long,
    period: span === "ttm" ? (kind === "growth" ? "TTM YoY" : "TTM") : "qtr end",
    kind,
  };
}

export function columnSpecs(keys: string[], options: ColumnInfo[] | null): ColumnSpec[] {
  const byKey = new Map((options ?? []).map((o) => [o.key, o]));
  return keys.map((k) => columnSpec(k, byKey.get(k)));
}

export function formatColumn(kind: ColumnKind, v: number): string {
  switch (kind) {
    case "usd":
      return money(v, "USD");
    case "pct":
      return pct(v);
    case "growth":
      return change(v);
    case "x":
      return `${v.toFixed(2)}x`;
    case "eps":
      return money(v, "USD/shares");
    case "shares": {
      const abs = Math.abs(v);
      return abs >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : abs >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toFixed(0);
    }
  }
}
