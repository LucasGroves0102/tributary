import { lazy, Suspense, useEffect, useRef, type RefObject } from "react";

import { isAttack, type PageContext } from "./context";
import { href } from "../route";
import { useScope } from "../scope";
import type { Assistant } from "./useAssistant";

// answers render through marked + DOMPurify (markdown.ts): loaded with the first answer, or
// in the background once idle (pages/lazy.ts), not with the first screen
const loadAnswers = () => import("../components/AssistantMessage");
const AssistantMessage = lazy(() => loadAnswers().then((m) => ({ default: m.AssistantMessage })));

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
  // a demo visitor gets the permission tests labelled as such, after the ordinary prompts
  const labelAttacks = useScope() !== null;
  const prompts = labelAttacks ? context.suggestions.filter((p) => !isAttack(p)) : context.suggestions;
  const attacks = labelAttacks ? context.suggestions.filter(isAttack) : [];
  const chip = (p: string) => (
    <button
      key={p}
      className="btn sm suggestion"
      disabled={!signedIn || busy}
      onClick={() => void submit(p)}
    >
      {p}
    </button>
  );

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
            <p className="faint">
              I can't give stock prices, valuation multiples, consensus estimates or call
              transcripts: Tributary has SEC filings and XBRL only.{" "}
              <a href={href({ view: "about" })}>What Tributary covers</a>
            </p>
          </div>
        ) : (
          turns.map((t) =>
            t.role === "user" ? (
              <article key={t.id} className="message user">
                {t.text}
              </article>
            ) : (
              <Suspense key={t.id} fallback={<article className="message">…</article>}>
                <AssistantMessage turn={t} undone={undone} onUndo={onUndo} />
              </Suspense>
            ),
          )
        )}
        <div ref={bottom} />
      </div>

      {context.suggestions.length > 0 && (
        <div className="ai-s">
          {prompts.map(chip)}
          {attacks.length > 0 && (
            <span className="ai-s-label" title="These try to get data this demo can't read; the assistant should refuse">
              Try to break it:
            </span>
          )}
          {attacks.map(chip)}
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
