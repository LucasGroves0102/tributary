import { useEffect, useRef, useState } from "react";

import { ApiError, failureReason, runModel } from "../api";
import { DEFAULT_HISTORY, DEFAULT_YEARS, modelState, type ModelRun } from "./model";

export type RunState =
  | { state: "loading" }
  | { state: "ok"; run: ModelRun }
  /** a Query API one release older: no model route yet (the minute of a deploy) */
  | { state: "older" }
  | { state: "error"; message: string; denied: boolean };

/** A model run (POST /v1/companies/{id}/model, the Query API's one copy of the maths) for a
 * state as saved or as typed: the first at once, later ones `delay` ms after the last change,
 * the previous answer kept on screen meanwhile (`pending`). The history is read by the API as the
 * viewer on every run; a company the viewer can't read answers 403 here like on every page. */
export function useModelRun(token: string, state: Record<string, unknown> | null, delay = 300): {
  run: RunState;
  pending: boolean;
  retry: () => void;
} {
  const [run, setRun] = useState<RunState>({ state: "loading" });
  const [pending, setPending] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const key = state ? JSON.stringify(state) : null;
  const first = useRef(true);
  useEffect(() => {
    if (!key) return;
    let live = true;
    setPending(true);
    const body = JSON.parse(key) as Record<string, unknown>;
    const go = () => {
      runModel(token, String(body.companyId), body)
        .then((r) => live && setRun(r ? { state: "ok", run: r } : { state: "older" }))
        .catch((e) => live && setRun({ state: "error", message: failureReason(e), denied: e instanceof ApiError && e.status === 403 }))
        .finally(() => live && setPending(false));
    };
    const wait = first.current ? 0 : delay;
    first.current = false;
    const timer = setTimeout(go, wait);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [token, key, delay, attempt]);
  return { run, pending, retry: () => setAttempt((a) => a + 1) };
}

/** A new model's state for a company (the model page and Analysis's model step): built from the
 * current fiscal year's reported quarters (`ytd`), its growth and margin the run's defaults, the
 * other rates left to their defaults. Without a run (no access, or an older Query API during a
 * deploy) the state an older API accepts: zeros the user types over, as when there is no history. */
export async function newModelState(token: string, companyId: string, years = DEFAULT_YEARS): Promise<Record<string, unknown>> {
  const zeros = Array.from({ length: years }, () => ({ growth: 0, margin: 0 }));
  const run = await runModel(token, companyId, modelState({ companyId, historyYears: DEFAULT_HISTORY, ytd: true, assumptions: zeros })).catch(() => null);
  const defaults = run?.cases.base?.defaults;
  if (!run || !defaults?.length) return modelState({ companyId, historyYears: DEFAULT_HISTORY, assumptions: zeros });
  return modelState({
    companyId,
    historyYears: DEFAULT_HISTORY,
    ytd: true,
    assumptions: defaults.slice(0, years).map((d) => ({ growth: d.growth, margin: d.margin })),
  });
}
