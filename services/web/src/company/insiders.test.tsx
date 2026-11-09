import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

import type { InsiderFilings } from "../types";
import NVDA from "./fixtures/nvda-insiders.json";
import { InsiderTable } from "./Insiders";
import { filingLine, signedShares, transactions } from "./insiders";

// the branch's /v1/companies/{NVIDIA}/insiders over five real NVIDIA filings (F7b scratchpad
// make_insider_fixture.py): regenerate, don't hand-edit
const DATA = NVDA as InsiderFilings;
const byAcc = (acc: string) => DATA.filings.find((f) => f.accession_no === acc)!;

it("#117: a filing's line in the index names the insider, their role and the trade", () => {
  expect(filingLine(byAcc("0001588670-26-000014"))).toBe(
    "Kress Colette, EVP & Chief Financial Officer · sold 34,918 shares ($7.65M) · shares withheld for tax or exercise price: 40,747 shares");
  expect(filingLine(byAcc("0001199039-26-000014"))).toBe("STEVENS MARK A, Director · sold 1,022,239 shares ($235.64M)");
  expect(filingLine(byAcc("0001197647-26-000013"))).toBe("COXE TENCH, Director · gift: 500,000 shares");
  expect(filingLine(byAcc("0002152188-26-000003"))).toBe("Parker Nicholas P., EVP, Worldwide Field Ops · no securities listed");
});

it("#117: every trade, newest first, signed by direction; holdings stay in the filing", () => {
  const rows = transactions(DATA.filings);
  expect(rows).toHaveLength(13);
  expect(rows[0]!.line.transaction_date).toBe("2026-11-04");
  expect(rows.every((r) => r.line.kind === "transaction")).toBe(true);
  const award = rows.find((r) => r.line.code === "A")!.line;
  expect(signedShares(award)).toBe("+160,418");
  expect(signedShares(rows.find((r) => r.line.code === "S")!.line)).toMatch(/^-/);
});

it("#117: the insider table, and an older API's 404 says so instead of failing", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  await act(async () => root.render(<InsiderTable load={{ state: "ok", data: DATA }} />));
  const first = [...container.querySelectorAll("tbody tr")].find((r) => r.textContent?.includes("Kress Colette"))!;
  const cells = [...first.querySelectorAll("td")].map((td) => td.textContent);
  // the day's first sale in filing order: 4,681 shares at $218.0144
  expect(cells.slice(1, 7)).toEqual(["Sep 17, 2026", "Open-market sale · 10b5-1 plan", "-4,681", "$218.01", "$1.02M", "795,340"]);
  expect(first.querySelector("a")?.getAttribute("href")).toBe("https://www.sec.gov/Archives/edgar/data/1045810/000158867026000014/");
  await act(async () => root.render(<InsiderTable load={{ state: "ok", data: null }} />));
  expect(container.textContent).toContain("aren't available on this server yet");
  act(() => root.unmount());
  container.remove();
});

it("pre-ship: older filings page on the API's cursor (never a day), and an older API offers no button", async () => {
  // the fixture split in two: the first page's three filings, the next page's two
  const firstPage: InsiderFilings = { ...DATA, filings: DATA.filings.slice(0, 3), has_more: true, next_cursor: "2026-09-18_0001588670-26-000014" };
  const nextPage: InsiderFilings = { ...DATA, filings: DATA.filings.slice(3), has_more: false, next_cursor: null };
  const fetched: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    fetched.push(url);
    return Response.json(nextPage);
  }));
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  await act(async () => root.render(<InsiderTable load={{ state: "ok", data: firstPage }} token="t" companyId="0001045810" />));
  expect(container.querySelector(".sub")!.textContent).toContain("3 most recent filings");
  expect(container.textContent).not.toContain("STEVENS MARK A");
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Show older filings")!;
  await act(async () => button.click());
  await act(async () => new Promise((r) => setTimeout(r, 0)));
  expect(fetched).toHaveLength(1);
  const asked = new URL(fetched[0]!, "http://x");
  expect(asked.pathname).toBe("/api/query/v1/companies/0001045810/insiders");
  expect(asked.searchParams.get("cursor")).toBe("2026-09-18_0001588670-26-000014");
  expect(asked.searchParams.get("before")).toBeNull();
  expect(container.textContent).toContain("STEVENS MARK A");
  expect(container.querySelector(".sub")!.textContent).toContain("5 filings");
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Show older filings")).toBe(false);
  // an API older than the cursor: has_more without next_cursor, so no button (it can't page)
  const olderApi: InsiderFilings = { ...firstPage, next_cursor: undefined };
  await act(async () => root.render(<InsiderTable load={{ state: "ok", data: olderApi }} token="t" companyId="0001045810" />));
  expect(container.querySelector(".sub")!.textContent).toContain("3 most recent filings");
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Show older filings")).toBe(false);
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
