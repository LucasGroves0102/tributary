import { useEffect, useRef, type RefObject } from "react";

import { AssistantMessage } from "../components/AssistantMessage";
import type { PageContext } from "./context";
import type { Assistant } from "./useAssistant";

interface Props {
  assistant: Assistant;
  /** undo for the assistant's UI actions, and which are undone */
  onUndo?: (actionId: number) => void;
  undone?: ReadonlySet<number>;
  context: PageContext;
  signedIn: boolean;
  /** drawer: the right-hand panel on every page; page: the Ask screen, full width */
  variant: "drawer" | "page";
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onClose?: () => void;
}

export function AssistantPanel({
  assistant,
  onUndo,
  undone,
  context,
  signedIn,
  variant,
  inputRef,
  onClose,
}: Props) {
  const { turns, draft, busy, setDraft, submit, stop, clear } = assistant;
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <aside
      className={variant === "drawer" ? "ai ai-drawer" : "ai ai-page card"}
      aria-label="Assistant"
    >
      <div className="ai-h">
        <b>Assistant</b>
        <span className="chip" title="Suggestions follow the page you're on">
          {context.label}
        </span>
        <span className="grow" />
        {turns.length > 0 && (
          <button className="btn sm ghost" onClick={clear}>
            Clear
          </button>
        )}
        {onClose && (
          <button className="btn sm ghost" onClick={onClose} title="Close (Ctrl J)" aria-label="Close assistant">
            ✕
          </button>
        )}
      </div>

      <div className="ai-m">
        {turns.length === 0 ? (
          <div className="empty">
            Ask about any company you can read: revenue, margins, cash flow, what a filing says
            or what changed since the last one. Every answer cites its filing and section, and I
            only see the companies your plan covers.
          </div>
        ) : (
          turns.map((t) =>
            t.role === "user" ? (
              <article key={t.id} className="message user">
                {t.text}
              </article>
            ) : (
              <AssistantMessage key={t.id} turn={t} undone={undone} onUndo={onUndo} />
            ),
          )
        )}
        <div ref={bottom} />
      </div>

      {context.suggestions.length > 0 && (
        <div className="ai-s">
          {context.suggestions.map((p) => (
            <button
              key={p}
              className="btn sm suggestion"
              disabled={!signedIn || busy}
              onClick={() => void submit(p)}
            >
              {p}
            </button>
          ))}
        </div>
      )}

      <form
        className="ai-i"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(draft);
        }}
      >
        <textarea
          ref={inputRef}
          value={draft}
          rows={2}
          maxLength={2000}
          placeholder={signedIn ? "Ask about SEC filings and financials…" : "Signing in…"}
          aria-label="Ask the assistant"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit(draft);
            } else if (e.key === "Escape") {
              e.currentTarget.blur();
            }
          }}
        />
        {busy ? (
          <button type="button" className="btn" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="btn primary" disabled={!signedIn || !draft.trim()}>
            Send
          </button>
        )}
      </form>
      <div className="ai-f">
        Enter to send · Shift+Enter new line · {variant === "drawer" && (
          <>
            <span className="kbd">Ctrl J</span> toggle ·{" "}
          </>
        )}
        <span className="kbd">/</span> focus
      </div>
    </aside>
  );
}
