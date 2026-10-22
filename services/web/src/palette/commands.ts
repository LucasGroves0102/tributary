/** Palette commands typed as text: "compare KO PEP" opens Compare with KO as the target and PEP
 * as a peer, "chart NVDA revenue" charts a metric. Tickers are resolved through the Query
 * API's company search (public identity); the pages then read data as the user, as always. */

import { searchCompanies } from "../api";

export type Command =
  | { kind: "compare"; tickers: string[] }
  | { kind: "chart"; ticker: string; metric: string };

const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;
const MAX_COMPARE = 8;

/** The command in the palette's text, or null. Metric words become a metric name
 * ("operating margin" → operating_margin); the chart builder checks it against the catalog. */
export function parseCommand(text: string): Command | null {
  const [verb, ...rest] = text.trim().split(/[\s,]+/).filter(Boolean);
  if (!verb) return null;
  if (verb.toLowerCase() === "compare") {
    const tickers = [...new Set(rest.filter((t) => t.toLowerCase() !== "and" && t.toLowerCase() !== "vs").map((t) => t.toUpperCase()))];
    if (tickers.length < 1 || tickers.length > MAX_COMPARE || !tickers.every((t) => TICKER.test(t))) return null;
    return { kind: "compare", tickers };
  }
  if (verb.toLowerCase() === "chart" && rest.length >= 2 && TICKER.test(rest[0]!)) {
    const metric = rest.slice(1).join("_").toLowerCase().replace(/^'s_/, "");
    if (!/^[a-z][a-z_]{1,63}$/.test(metric)) return null;
    return { kind: "chart", ticker: rest[0]!.toUpperCase(), metric };
  }
  return null;
}

/** Ticker → company_id from an exact ticker match in company search; null when none. */
export async function resolveTicker(token: string, ticker: string): Promise<{ companyId: string; name: string } | null> {
  const results = await searchCompanies(token, ticker);
  const hit = results.find((r) => r.matched_on === "ticker" && r.matched_alias.toUpperCase() === ticker.toUpperCase());
  return hit ? { companyId: hit.company_id, name: hit.name } : null;
}
