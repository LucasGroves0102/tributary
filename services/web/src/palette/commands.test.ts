import { afterEach, expect, it, vi } from "vitest";

import { parseCommand, resolveTicker } from "./commands";

it("reads compare and chart commands, and nothing else", () => {
  expect(parseCommand("compare KO PEP")).toEqual({ kind: "compare", tickers: ["KO", "PEP"] });
  expect(parseCommand("  Compare ko, pep and kdp ")).toEqual({ kind: "compare", tickers: ["KO", "PEP", "KDP"] });
  expect(parseCommand("compare KO vs KO")).toEqual({ kind: "compare", tickers: ["KO"] });
  expect(parseCommand("chart NVDA revenue")).toEqual({ kind: "chart", ticker: "NVDA", metric: "revenue" });
  expect(parseCommand("chart brk.b free cash flow")).toEqual({ kind: "chart", ticker: "BRK.B", metric: "free_cash_flow" });
  for (const text of ["compare", "compare " + Array.from({ length: 9 }, (_, i) => `T${i}`).join(" "), "compare ../me", "chart NVDA", "chart NVDA rev3nue",
    "chart NVDA revenue; drop", "nvidia", ""]) {
    expect(parseCommand(text)).toBeNull();
  }
});

afterEach(() => vi.unstubAllGlobals());

it("resolves a ticker only from an exact ticker match", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results: [
    { company_id: "0000021344", name: "COCA COLA CO", matched_on: "name", matched_alias: "KO Holdings", is_current: true, access: [] },
    { company_id: "0000021345", name: "COCA COLA CO", matched_on: "ticker", matched_alias: "KO", is_current: true, access: [] },
  ] })));
  expect(await resolveTicker("t", "ko")).toEqual({ companyId: "0000021345", name: "Coca-Cola" });
  expect(new URL(String(vi.mocked(fetch).mock.calls[0]![0]), "http://x").searchParams.get("q")).toBe("ko");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results: [] })));
  expect(await resolveTicker("t", "ZZZ")).toBeNull();
});
