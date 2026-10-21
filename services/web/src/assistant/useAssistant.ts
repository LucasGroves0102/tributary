import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ask } from "../api";
import type { AssistantTurn } from "../components/AssistantMessage";
import type { AgentEvent, HistoryTurn } from "../types";

export type Turn = { id: number; role: "user"; text: string } | AssistantTurn;

export interface Assistant {
  turns: Turn[];
  draft: string;
  busy: boolean;
  setDraft: (d: string) => void;
  submit: (question: string) => Promise<void>;
  stop: () => void;
  clear: () => void;
}

const HISTORY_TURNS = 10;
let nextId = 1;

/**
 * One conversation with the agent (agent-api /v1/ask, streamed), shared by the drawer and the
 * Ask page. It starts over when the signed-in user changes.
 */
export function useAssistant(token: string | null, user: string | null): Assistant {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    abort.current?.abort();
    setTurns([]);
  }, [user]);

  const update = useCallback((id: number, f: (t: AssistantTurn) => AssistantTurn) => {
    setTurns((ts) => ts.map((t) => (t.id === id && t.role === "assistant" ? f(t) : t)));
  }, []);

  const submit = useCallback(
    async (question: string) => {
      if (!token || busy || !question.trim()) return;
      const history: HistoryTurn[] = turns.flatMap((t): HistoryTurn[] =>
        t.role === "user"
          ? [{ role: "user", content: t.text }]
          : t.answer
            ? [{ role: "assistant", content: t.answer.text }]
            : [],
      );
      const id = nextId++;
      setTurns((ts) => [
        ...ts,
        { id: nextId++, role: "user", text: question },
        { id, role: "assistant", rounds: {}, tools: [] },
      ]);
      setDraft("");
      setBusy(true);
      abort.current = new AbortController();

      const onEvent = (e: AgentEvent) => {
        switch (e.type) {
          case "text":
            update(id, (t) => ({
              ...t,
              rounds: { ...t.rounds, [e.round]: (t.rounds[e.round] ?? "") + e.text },
            }));
            break;
          case "tool":
            update(id, (t) => ({ ...t, tools: [...t.tools, e] }));
            break;
          case "done":
            update(id, (t) => ({ ...t, answer: e.answer }));
            break;
          case "error":
            update(id, (t) => ({ ...t, error: e.message }));
        }
      };

      try {
        await ask(token, question, history.slice(-HISTORY_TURNS), onEvent, abort.current.signal);
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          const msg = err instanceof ApiError ? err.message : "The agent is unreachable.";
          update(id, (t) => ({ ...t, error: msg }));
        }
      } finally {
        setBusy(false);
      }
    },
    [token, busy, turns, update],
  );

  const stop = useCallback(() => abort.current?.abort(), []);
  const clear = useCallback(() => {
    abort.current?.abort();
    setTurns([]);
  }, []);

  return { turns, draft, busy, setDraft, submit, stop, clear };
}
