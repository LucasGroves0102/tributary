import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, writeReport } from "../api";
import type { AgentEvent } from "../types";

/** One report run (agent-api /v1/reports): writing, then saved or failed. It lives with the
 * app, so moving to another screen doesn't stop it. */
export type Run =
  | { status: "idle" }
  | { status: "writing"; workspaceId: string; reportId: string | null; text: string; tools: number }
  | { status: "done"; workspaceId: string; itemId: string; ok: boolean; unverified: number }
  | { status: "error"; workspaceId: string; reportId: string | null; message: string };

export interface ReportWriter {
  run: Run;
  /** writes a report from the workspace (or again, over reportId) */
  start: (workspaceId: string, reportId?: string | null) => void;
  stop: () => void;
}

const PREVIEW_CHARS = 1500;

/** onSaved(itemId, workspaceId): the report is stored (refresh the lists, show it). */
export function useReportWriter(
  token: string | null,
  user: string | null,
  onSaved: (itemId: string, workspaceId: string) => void,
): ReportWriter {
  const [run, setRun] = useState<Run>({ status: "idle" });
  const abort = useRef<AbortController | null>(null);
  const saved = useRef(onSaved);
  saved.current = onSaved;

  useEffect(() => {
    abort.current?.abort();
    setRun({ status: "idle" });
  }, [user]);

  const start = useCallback(
    (workspaceId: string, reportId: string | null = null) => {
      if (!token || run.status === "writing") return;
      const ctl = new AbortController();
      abort.current = ctl;
      setRun({ status: "writing", workspaceId, reportId, text: "", tools: 0 });
      let finished = false;
      const fail = (message: string) => {
        finished = true;
        setRun({ status: "error", workspaceId, reportId, message });
      };
      const onEvent = (e: AgentEvent) => {
        if (e.type === "text") {
          setRun((r) => (r.status === "writing" ? { ...r, text: (r.text + e.text).slice(-PREVIEW_CHARS) } : r));
        } else if (e.type === "tool") {
          setRun((r) => (r.status === "writing" ? { ...r, tools: r.tools + 1 } : r));
        } else if (e.type === "report") {
          finished = true;
          setRun({ status: "done", workspaceId, itemId: e.item.item_id, ok: e.verification.ok, unverified: e.verification.unverified });
          saved.current(e.item.item_id, workspaceId);
        } else if (e.type === "error") {
          fail(`The report wasn't finished: ${e.message}.`);
        }
      };
      writeReport(token, workspaceId, reportId, onEvent, ctl.signal)
        .then(() => {
          if (!finished) fail("The report wasn't finished. Try again.");
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            setRun({ status: "idle" });
            return;
          }
          fail(err instanceof ApiError ? err.message : "The assistant is unreachable.");
        });
    },
    [token, run.status],
  );

  const stop = useCallback(() => abort.current?.abort(), []);
  return { run, start, stop };
}
