import { expect, it } from "vitest";

import { WATCHLIST_COLUMNS } from "../api";
import { SCREEN_COLUMNS } from "../companies/screen";
import type { ColumnInfo } from "../types";
import {
  columnList,
  columnSpec,
  DEFAULT_SCREENER_COLUMNS,
  formatColumn,
  HOME_CARDS,
  homeLayout,
  isDefaultHome,
  move,
  offeredColumn,
  sameColumns,
  toggleCard,
} from "./model";

const OPTIONS = [
  { key: "revenue_ttm", metric: "revenue", span: "ttm" as const, unit: "USD" },
  { key: "net_income_ttm", metric: "net_income", span: "ttm" as const, unit: "USD" },
  { key: "eps_diluted_q", metric: "eps_diluted", span: "quarter" as const, unit: "USD/shares" },
  { key: "weighted_shares_diluted_q", metric: "weighted_shares_diluted", span: "quarter" as const, unit: "shares" },
  { key: "net_income_growth_ttm", metric: "net_income_growth", span: "ttm" as const, unit: "ratio" },
  { key: "current_ratio_q", metric: "current_ratio", span: "quarter" as const, unit: "ratio" },
  { key: "roe_ttm", metric: "roe", span: "ttm" as const, unit: "ratio" },
];

it("Home layout: saved order, unknown cards dropped, missing ones appended", () => {
  expect(homeLayout(null).map((c) => c.id)).toEqual([...HOME_CARDS]);
  expect(isDefaultHome(homeLayout(null))).toBe(true);
  const saved = homeLayout({ cards: [{ id: "notes", hidden: false }, { id: "ads", hidden: false }, { id: "status", hidden: true }, { id: "notes", hidden: true }] });
  expect(saved.map((c) => `${c.id}${c.hidden ? "-" : ""}`)).toEqual(["notes", "status-", "recent", "watchlist", "filings", "expected"]);
  expect(isDefaultHome(saved)).toBe(false);
  expect(toggleCard(saved, "status")[1]).toEqual({ id: "status", hidden: false });
});

it("moves within bounds", () => {
  expect(move(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
  expect(move(["a", "b", "c"], 2, -1)).toEqual(["a", "c", "b"]);
  expect(move(["a", "b"], 0, -1)).toEqual(["a", "b"]);
  expect(move(["a", "b"], 1, 1)).toEqual(["a", "b"]);
});

it("column sets: known keys, no repeats, capped; empty means the default", () => {
  expect(columnList(null, OPTIONS, DEFAULT_SCREENER_COLUMNS, 12)).toEqual(DEFAULT_SCREENER_COLUMNS);
  expect(columnList({ columns: ["net_income_ttm", "bogus_ttm", "net_income_ttm", "revenue_ttm"] }, OPTIONS, ["x"], 12))
    .toEqual(["net_income_ttm", "revenue_ttm"]);
  expect(columnList({ columns: ["bogus"] }, OPTIONS, ["revenue_ttm"], 12)).toEqual(["revenue_ttm"]);
  expect(columnList({ columns: ["a", "b", "c"] }, null, ["x"], 2)).toEqual(["a", "b"]); // catalog not known yet
  expect(sameColumns(["a", "b"], ["a", "b"])).toBe(true);
  expect(sameColumns(["a", "b"], ["b", "a"])).toBe(false);
});

it("the defaults are the tables' own columns", () => {
  expect(DEFAULT_SCREENER_COLUMNS).toEqual(SCREEN_COLUMNS.map((c) => c.key));
  expect(WATCHLIST_COLUMNS).toEqual(["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q"]);
});

it("a balance is at a date: no TTM on offer, and a saved one reads as what it is", () => {
  const cash = (span: "ttm" | "quarter", key: string): ColumnInfo => ({ key, metric: "cash", span, unit: "USD", balance: true });
  expect(offeredColumn(cash("ttm", "cash_ttm"))).toBe(false);
  expect(offeredColumn(cash("quarter", "cash_q"))).toBe(true);
  expect(offeredColumn({ key: "revenue_ttm", metric: "revenue", span: "ttm", unit: "USD", balance: false })).toBe(true);
  expect(columnSpec("cash_ttm", cash("ttm", "cash_ttm")).period).toBe("qtr end");
  expect(columnSpec("net_debt_ttm").period).toBe("qtr end");
  // a flow in one quarter isn't "at the quarter end"
  expect(columnSpec("revenue_q").period).toBe("quarter");
});

it("columns from the API's catalog get labels, periods and formats by unit", () => {
  const spec = (k: string) => columnSpec(k, OPTIONS.find((o) => o.key === k));
  expect(spec("revenue_ttm")).toMatchObject({ label: "Revenue", period: "TTM", kind: "usd" });
  expect(spec("net_income_ttm")).toMatchObject({ label: "Net income", period: "TTM", kind: "usd" });
  expect(spec("eps_diluted_q")).toMatchObject({ period: "quarter", kind: "eps" });
  expect(spec("weighted_shares_diluted_q").kind).toBe("shares");
  expect(spec("net_income_growth_ttm")).toMatchObject({ kind: "growth", period: "TTM YoY" });
  expect(spec("current_ratio_q").kind).toBe("x");
  expect(spec("roe_ttm").kind).toBe("pct");
  expect(formatColumn("usd", 1.2e9)).toBe("$1.20B");
  expect(formatColumn("eps", 1.234)).toBe("$1.23");
  expect(formatColumn("shares", 2.45e9)).toBe("2.45B");
  expect(formatColumn("growth", 0.1)).toBe("+10.0%");
  expect(formatColumn("x", 1.5)).toBe("1.50x");
});
