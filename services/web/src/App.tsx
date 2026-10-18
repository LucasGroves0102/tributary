import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ask, demoUsers, login, me as fetchMe } from "./api";
import { AssistantMessage, type AssistantTurn } from "./components/AssistantMessage";
import { SUGGESTIONS, Sidebar } from "./components/Sidebar";
import type { AgentEvent, HistoryTurn, Me } from "./types";

type Turn = { id: number; role: "user"; text: string } | AssistantTurn;

let nextId = 1;

export function App() {
  const [users, setUsers] = useState<string[]>([]);
  const [user, setUser] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    demoUsers()
      .then((u) => {
        setUsers(u);
        setUser(u[0] ?? null);
      })
      .catch(() => setError("Can't reach the Query API."));
  }, []);

  useEffect(() => {
    if (!user) return;
    setMe(null);
    setTurns([]);
    let cancelled = false;
    login(user)
      .then(async (t) => {
        const m = await fetchMe(t);
        if (!cancelled) {
          setToken(t);
          setMe(m);
        }
      })
      .catch(() => !cancelled && setError(`Can't sign in as ${user}.`));
    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns]);

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
        await ask(token, question, history.slice(-10), onEvent, abort.current.signal);
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

  return (
    <div className="layout">
      <Sidebar
        users={users}
        user={user}
        me={me}
        busy={busy}
        onUser={setUser}
        onPrompt={(p) => void submit(p)}
      />
      <main className="chat">
        {error && <div className="banner">{error}</div>}
        <div className="thread">
          {turns.length === 0 ? (
            <Empty user={user} onPrompt={(p) => void submit(p)} />
          ) : (
            turns.map((t) =>
              t.role === "user" ? (
                <article key={t.id} className="message user">
                  {t.text}
                </article>
              ) : (
                <AssistantMessage key={t.id} turn={t} />
              ),
            )
          )}
          <div ref={bottom} />
        </div>
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void submit(draft);
          }}
        >
          <textarea
            value={draft}
            placeholder={user ? `Ask about SEC filings as ${user}…` : "Ask about SEC filings…"}
            rows={1}
            maxLength={2000}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit(draft);
              }
            }}
          />
          {busy ? (
            <button type="button" onClick={() => abort.current?.abort()}>
              Stop
            </button>
          ) : (
            <button type="submit" disabled={!token || !draft.trim()}>
              Ask
            </button>
          )}
        </form>
      </main>
    </div>
  );
}

function Empty({ user, onPrompt }: { user: string | null; onPrompt: (p: string) => void }) {
  return (
    <div className="empty">
      <h1>Ask about public companies' SEC filings</h1>
      <p className="muted">
        Answers come only from data {user ?? "you"} can read, with the filing behind every
        number.
      </p>
      <div className="prompts center">
        {[SUGGESTIONS.ask[0], SUGGESTIONS.attack[0]].map((p) => (
          <button key={p} className="prompt" onClick={() => onPrompt(p)}>
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
