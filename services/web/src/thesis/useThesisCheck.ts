import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, checkThesis, thesisCheck, thesisChecks } from "../api";
import type { AgentEvent, ThesisCheck, ThesisCheckSummary } from "../types";

/** One thesis's checks: the history (newest first), the one shown, the one before it (what
 * "Changes since" compares with), and a run in progress. */
export interface ThesisChecks {
  history: ThesisCheckSummary[];
  shown: ThesisCheck | null;
  /** the check just older than `shown` in the history, once read; null if none */
  previous: ThesisCheck | null;
  run: { status: "idle" } | { status: "running"; tools: number } | { status: "error"; message: string };
  loading: boolean;
  start: () => void;
  stop: () => void;
  show: (checkId: string) => void;
}

/** `itemId` null: nothing is read or run (a page that was handed its checks by an analysis
 * project, which shares one instance between its Thesis and Monitor steps). */
export function useThesisCheck(token: string, itemId: string | null): ThesisChecks {
  const [history, setHistory] = useState<ThesisCheckSummary[]>([]);
  const [shown, setShown] = useState<ThesisCheck | null>(null);
  const [run, setRun] = useState<ThesisChecks["run"]>({ status: "idle" });
  const [loading, setLoading] = useState(true);
  const [previous, setPrevious] = useState<ThesisCheck | null>(null);
  const abort = useRef<AbortController | null>(null);
  const live = useRef(true);
  const read = useRef(new Map<string, ThesisCheck>());

  useEffect(() => {
    live.current = true;
    if (!itemId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    thesisChecks(token, itemId)
      .then(async (r) => {
        if (!live.current) return;
        setHistory(r.checks);
        const newest = r.checks[0];
        if (newest) {
          const c = await thesisCheck(token, itemId, newest.check_id);
          if (live.current) setShown(c);
        }
      })
      .catch(() => undefined) // no history shown; checking still works
      .finally(() => live.current && setLoading(false));
    return () => {
      live.current = false;
      abort.current?.abort();
    };
  }, [token, itemId]);

  const show = useCallback(
    (checkId: string) => {
      if (!itemId || shown?.check_id === checkId) return;
      thesisCheck(token, itemId, checkId)
        .then((c) => live.current && setShown(c))
        .catch((e) => live.current && setRun({ status: "error", message: e instanceof ApiError ? e.message : "the Query API didn't answer" }));
    },
    [token, itemId, shown],
  );

  const start = useCallback(() => {
    if (!itemId || run.status === "running") return;
    const ctl = new AbortController();
    abort.current = ctl;
    setRun({ status: "running", tools: 0 });
    let finished = false;
    const onEvent = (e: AgentEvent) => {
      if (!live.current) return;
      if (e.type === "tool") {
        setRun((r) => (r.status === "running" ? { ...r, tools: r.tools + 1 } : r));
      } else if (e.type === "check") {
        finished = true;
        setHistory((h) => [e.check, ...h.filter((c) => c.check_id !== e.check.check_id)]);
        setShown({ ...e.check, result: e.result });
        setRun({ status: "idle" });
      } else if (e.type === "error") {
        finished = true;
        setRun({ status: "error", message: `The check wasn't finished: ${e.message}.` });
      }
    };
    checkThesis(token, itemId, onEvent, ctl.signal)
      .then(() => {
        if (!finished && live.current) setRun({ status: "error", message: "The check wasn't finished. Try again." });
      })
      .catch((err) => {
        if (!live.current) return;
        if (err instanceof DOMException && err.name === "AbortError") setRun({ status: "idle" });
        else setRun({ status: "error", message: err instanceof ApiError ? err.message : "The assistant is unreachable." });
      });
  }, [token, itemId, run.status]);

  // the check before the shown one, for "Changes since" (results never change: kept once read)
  const shownId = shown?.check_id;
  const at = shownId ? history.findIndex((c) => c.check_id === shownId) : -1;
  const beforeId = at >= 0 ? history[at + 1]?.check_id : undefined;
  useEffect(() => {
    if (shown) read.current.set(shown.check_id, shown);
    if (!beforeId || !itemId) {
      setPrevious(null);
      return;
    }
    const hit = read.current.get(beforeId);
    if (hit) {
      setPrevious(hit);
      return;
    }
    let ok = true;
    setPrevious(null);
    thesisCheck(token, itemId, beforeId)
      .then((c) => {
        read.current.set(c.check_id, c);
        if (ok && live.current) setPrevious(c);
      })
      .catch(() => undefined); // no "Changes since"; the check itself still shows
    return () => {
      ok = false;
    };
  }, [token, itemId, shownId, beforeId]);

  const stop = useCallback(() => abort.current?.abort(), []);
  return { history, shown, previous, run, loading, start, stop, show };
}
