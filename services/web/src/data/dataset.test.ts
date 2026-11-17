import { expect, it } from "vitest";

import { olderState } from "../api";
import { parseScreen, screenParams, screenQuery, sortable } from "../companies/screen";
import { restoreScreen } from "../workspaces/model";
import { fieldValue } from "../company/YourData";
import { delimiterOf, guessColumns, headerOf, leftOutWords, metricKey, problemWords, simplify, unitsField, valueColumns } from "./dataset";

const M1 = "6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f";
const M2 = "00000000-0000-4000-8000-000000000002";

it("simplifies column names as the Query API does (datasets_csv.simplify's cases)", () => {
  expect(simplify("Store count (EoP)")).toBe("store_count_eop");
  expect(simplify("Umsatz €")).toBe("umsatz");
  expect(simplify("Café")).toBe("cafe");
  expect(simplify("2024 sales")).toBe("f_2024_sales");
  expect(simplify("%%%")).toBe("");
  expect(simplify("x'); DROP TABLE userdata.rows; --")).toBe("x_drop_table_userdata_rows");
  expect(simplify("a".repeat(60))).toHaveLength(40);
});

it("reads a header row with its delimiter, quotes and byte-order mark", () => {
  expect(headerOf("﻿Ticker,Date,Stores\nAAPL,2026-06-30,1\n")).toEqual(["Ticker", "Date", "Stores"]);
  expect(headerOf("\n\nticker;date;\"a;b\"\r\nAAPL;2026-06-30;1")).toEqual(["ticker", "date", "a;b"]);
  expect(headerOf('"say ""hi""",date\nx,y')).toEqual(['say "hi"', "date"]);
  expect(headerOf("ticker\tdate\tx")).toEqual(["ticker", "date", "x"]);
  expect(headerOf("")).toEqual([]);
  expect(delimiterOf("a;b;c,d\n")).toBe(";");
  expect(delimiterOf("a,b;c\n")).toBe(",");
});

it("guesses the company and date columns and only suggests a publication date", () => {
  const header = ["Symbol", "Period end", "Published", "EPS", "eps"];
  expect(guessColumns(header)).toEqual({ company: "Symbol", date: "Period end", published: "Published" });
  expect(guessColumns(["Name", "When", "x"])).toEqual({ company: "", date: "", published: "" });
  // the value columns: every named column but the roles, once each by its formula name
  expect(valueColumns(header, ["Symbol", "Period end", ""])).toEqual([
    { label: "Published", name: "published" }, { label: "EPS", name: "eps" }]);
  expect(valueColumns(header, ["Symbol", "Period end", "Published"])).toEqual([{ label: "EPS", name: "eps" }]);
  expect(unitsField({ eps: "per_share", stores: "number", sales: "money" })).toBe("eps:per_share,sales:money");
});

it("words problems by row and column only, and rows left out by number", () => {
  expect(problemWords({ code: "number", message: "Not a number", row: 1204, column: 3 })).toBe("Row 1,204, column 3: Not a number.");
  expect(problemWords({ code: "no_rows", message: "The file has no row with a company.", row: null, column: null }))
    .toBe("The file has no row with a company.");
  expect(leftOutWords({ rows: [], count: 0, companies: 0 })).toBeNull();
  expect(leftOutWords({ rows: [5], count: 1, companies: 1 })).toBe(
    "1 row is left out: its company matched no ticker or CIK, and Tributary doesn't guess (row 5).");
  expect(leftOutWords({ rows: [5, 9], count: 102, companies: 3 })).toContain("(row 5, 9 and 100 more)");
});

it("keeps own metrics in the screen's address, its saved state and its query, and an older API drops them", () => {
  const screen = parseScreen(`band=1b-10b&metric=${M1.toUpperCase()}&metric=${M1}&metric=bogus&metric=${M2}&sort=-${metricKey(M1)}`);
  expect(screen.metric).toEqual([M1, M2]);
  expect(screen.sort).toBe(`-m_${M1.replace(/-/g, "")}`);
  expect(sortable(metricKey(M2))).toBe(true);
  expect(sortable("m_nothex")).toBe(false);
  expect(parseScreen(screenQuery(screen))).toEqual(screen);
  expect(restoreScreen({ band: "1b-10b", metric: [M1, M2], sort: screen.sort })).toEqual(screen);
  expect(screenParams(screen).metric).toEqual([M1, M2]);
  expect(parseScreen(Array.from({ length: 8 }, (_, i) => `metric=00000000-0000-4000-8000-00000000000${i}`).join("&")).metric).toHaveLength(5);
  expect(olderState({ band: "1b-10b", metric: [M1], sort: screen.sort })).toEqual({ band: "1b-10b" });
});

it("shows a field's value in its unit, never converting", () => {
  expect(fieldValue("1500", "money", "USD")).toBe("$1.50K");
  expect(fieldValue("0.25", "ratio", "USD")).toBe("25.0%");
  expect(fieldValue("12", "number", "USD")).toBe("12");
  expect(fieldValue("1234567", "number", "USD")).toBe("1,234,567");
  expect(fieldValue(null, "number", "USD")).toBe("—");
  expect(fieldValue("2.5", "per_share", "EUR")).not.toContain("$");
});
