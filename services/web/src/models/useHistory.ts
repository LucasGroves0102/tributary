import { useEffect, useState } from "react";

import { ApiError, failureReason, financialsView } from "../api";
import { annualHistory, defaultAssumptions, HISTORY_METRICS, HISTORY_QUARTERS, historyStats, MAX_HISTORY, runRate, type Assumption, type HistoryYear, type RunRate } from "./model";

export type History =
  | { state: "loading" }
  | { state: "ok"; years: HistoryYear[]; asOf: string; run: RunRate }
  | { state: "error"; message: string; denied: boolean };

/** A model's history: up to MAX_HISTORY fiscal years of revenue and operating income, read now
 * through the Query API as the viewer (TTM at each fiscal Q4), and the latest TTM run rate (what
 * the defaults start from). Never stored in the item; a company the viewer can't read answers
 * 403 here like on every other page. */
export function useHistory(token: string, companyId: string | null): History {
  const [h, setH] = useState<History>({ state: "loading" });
  useEffect(() => {
    if (!companyId) return;
    let live = true;
    setH({ state: "loading" });
    loadHistory(token, companyId)
      .then((r) => live && setH({ state: "ok", ...r }))
      .catch((e) => live && setH({
        state: "error",
        message: failureReason(e),
        denied: e instanceof ApiError && e.status === 403,
      }));
    return () => {
      live = false;
    };
  }, [token, companyId]);
  return h;
}

export function loadHistory(token: string, companyId: string): Promise<{ years: HistoryYear[]; asOf: string; run: RunRate }> {
  return financialsView(token, companyId, HISTORY_QUARTERS, HISTORY_METRICS, "ttm").then((r) => ({
    years: annualHistory(r.rows, MAX_HISTORY, companyId),
    asOf: r.as_of,
    run: runRate(r.rows),
  }));
}

/** A new model's state for a company: its history read now, the defaults from it (an empty
 * history gives zeros the user then types over). */
export async function newModelAssumptions(token: string, companyId: string, years: number): Promise<Assumption[]> {
  const h = await loadHistory(token, companyId).catch(() => null);
  return defaultAssumptions(historyStats(h?.years ?? []), years, h?.run ?? null);
}
