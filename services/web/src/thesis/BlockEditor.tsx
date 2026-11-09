import { useEffect, useId, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from "react";

import type { Readable } from "../access";
import type { ChartState } from "../chart/model";
import { renderMarkdown } from "../markdown";
import { Embed } from "../reports/Embeds";
import type { EmbedItem } from "./model";
import { lineAt, lineOffset, moveUnit, quoteInfo, removeUnit, replaceRun, runText, segmentsOf, unitsOf, type Unit } from "./blocks";

/** What the thesis page asks of the block editor: where the cursor is (an embed goes there) and
 * selecting a check's quote in the text. Offsets are into the whole Markdown. */
export interface BlockEditorHandle {
  /** the cursor in the whole Markdown; null until the user has put it in a text box */
  cursor(): number | null;
  /** selects [start, end) in the text box that holds it; false when a card holds it */
  select(start: number, end: number): boolean;
}

interface Props {
  markdown: string;
  onChange: (markdown: string) => void;
  /** a card was removed: the page offers Undo (the text before) */
  onRemoved: (what: string, before: string) => void;
  items: EmbedItem[];
  token: string;
  label: (companyId: string) => string;
  readable: Readable;
  hasTtm: (metric: string) => boolean;
  onOpenChart: (s: ChartState) => void;
  handle?: Ref<BlockEditorHandle>;
  placeholder?: string;
}

const ITEM_KIND_WORD: Record<string, string> = { chart: "chart", comps: "comps table", screen: "screen", model: "model" };

/** The thesis written with its evidence in place (F6, wishlist B.8): text in boxes, and each
 * embedded chart, comps table, model or screen and each pinned quote or earnings passage drawn as
 * a card that can move up and down past the paragraph or card beside it, or be removed. The
 * Markdown underneath (thesis/blocks.ts) is still what is saved, exported and imported.
 * Keyboard: Tab reaches each card; on a card, Alt+↑ and Alt+↓ move it and Delete removes it (a
 * notice offers Undo); the cards' buttons do the same. Moves and removals are announced. */
export function BlockEditor({ markdown, onChange, onRemoved, items, token, label, readable, hasTtm, onOpenChart, handle, placeholder }: Props) {
  const id = useId();
  const units = useMemo(() => unitsOf(markdown), [markdown]);
  const segments = useMemo(() => segmentsOf(units), [units]);
  const byId = useMemo(() => new Map(items.map((i) => [i.item_id, i])), [items]);
  const texts = useRef(new Map<number, { el: HTMLTextAreaElement; start: number; end: number }>());
  const cards = useRef(new Map<number, HTMLElement>());
  const cursor = useRef<{ line: number; offset: number } | null>(null);
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const [said, say] = useState("");
  const latest = useRef(markdown);
  latest.current = markdown;

  useImperativeHandle(handle, () => ({
    cursor: () => (cursor.current ? lineOffset(latest.current, cursor.current.line) + cursor.current.offset : null),
    select: (start, end) => {
      const line = lineAt(latest.current, start);
      for (const t of texts.current.values()) {
        if (line >= t.start && line < t.end) {
          const base = lineOffset(latest.current, t.start);
          t.el.focus();
          t.el.setSelectionRange(start - base, end - base);
          return true;
        }
      }
      return false;
    },
  }), []);

  // after a move or a removal, focus follows: the card at that line, else the text box holding it
  useEffect(() => {
    if (focusLine === null) return;
    const card = cards.current.get(focusLine);
    if (card) card.focus();
    else {
      for (const t of texts.current.values()) {
        if (focusLine >= t.start && focusLine < t.end) {
          t.el.focus();
          break;
        }
      }
    }
    setFocusLine(null);
  }, [focusLine, markdown]);

  const describe = (u: Unit): string => {
    if (u.kind === "quote") {
      const q = quoteInfo(markdown, u);
      return q.earnings ? "earnings release passage" : `quote from the ${q.form ?? "filing"}`;
    }
    const item = u.id ? byId.get(u.id) : undefined;
    return item ? `${ITEM_KIND_WORD[item.kind] ?? "item"} “${item.name}”` : "embed of a missing item";
  };
  const canMove = (index: number, dir: -1 | 1) => {
    let j = index + dir;
    while (units[j]?.kind === "blank") j += dir;
    return !!units[j];
  };
  const move = (index: number, dir: -1 | 1) => {
    const what = describe(units[index]!);
    const r = moveUnit(markdown, units, index, dir);
    if (!r) return;
    onChange(r.markdown);
    setFocusLine(r.line);
    say(`Moved the ${what} ${dir < 0 ? "up" : "down"}.`);
  };
  const remove = (index: number) => {
    const what = describe(units[index]!);
    const r = removeUnit(markdown, units, index);
    if (!r) return;
    onRemoved(what, markdown);
    onChange(r.markdown);
    setFocusLine(r.line);
    say(`Removed the ${what}. Undo is in the notice above.`);
  };
  const onCardKey = (e: KeyboardEvent<HTMLElement>, index: number) => {
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      move(index, e.key === "ArrowUp" ? -1 : 1);
    } else if ((e.key === "Delete" || e.key === "Backspace") && e.target === e.currentTarget) {
      e.preventDefault();
      remove(index);
    }
  };

  texts.current.clear();
  cards.current.clear();
  const firstRun = segments.find((s) => s.kind === "run");
  // card keys follow the card, not its place: an embed's id, a quote's text (with a count for repeats)
  const seen = new Map<string, number>();
  const keyOf = (u: Unit) => {
    const base = u.kind === "embed" ? `embed-${u.id}` : `quote-${hashOf(runText(markdown, u.start, u.end))}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return `${base}-${n}`;
  };
  return (
    <div className="block-editor" role="group" aria-label="Thesis" aria-describedby={`${id}-keys`}>
      <p id={`${id}-keys`} className="visually-hidden">
        Text boxes hold your writing; charts, comps, models and quotes are cards. On a card, Alt and the up or down arrow move it, Delete removes it.
      </p>
      {segments.map((s) => {
        if (s.kind === "run") {
          const text = runText(markdown, s.start, s.end);
          // only blank lines between two cards: a slim box, there to type between them
          const gap = text.trim() === "" && s !== firstRun;
          return (
            <textarea
              key={`run-${s.ordinal}`}
              ref={(el) => {
                if (el) texts.current.set(s.ordinal, { el, start: s.start, end: s.end });
              }}
              className={gap ? "field block-text gap" : "field block-text"}
              value={text}
              rows={gap ? 1 : Math.max(2, text.split("\n").length)}
              spellCheck
              aria-label={s === firstRun ? "Thesis text" : gap ? "Text between two cards" : "Thesis text, continued"}
              placeholder={s === firstRun ? placeholder : gap ? "Write here…" : undefined}
              onChange={(e) => {
                cursor.current = { line: s.start, offset: e.target.selectionStart };
                onChange(replaceRun(markdown, s.start, s.end, e.target.value));
              }}
              onSelect={(e) => {
                cursor.current = { line: s.start, offset: e.currentTarget.selectionStart };
              }}
              onMouseUp={(e) => {
                cursor.current = { line: s.start, offset: e.currentTarget.selectionStart };
              }}
              onKeyUp={(e) => {
                if (e.key !== "Tab") cursor.current = { line: s.start, offset: e.currentTarget.selectionStart };
              }}
            />
          );
        }
        const u = s.unit;
        const what = describe(u);
        const actions = (
          <span className="card-moves no-print" role="group" aria-label={`Arrange the ${what}`}>
            <button type="button" className="btn sm ghost" aria-label={`Move the ${what} up`} title="Move up (Alt+↑)"
              disabled={!canMove(s.index, -1)} onClick={() => move(s.index, -1)}>↑</button>
            <button type="button" className="btn sm ghost" aria-label={`Move the ${what} down`} title="Move down (Alt+↓)"
              disabled={!canMove(s.index, 1)} onClick={() => move(s.index, 1)}>↓</button>
            <button type="button" className="btn sm ghost" aria-label={`Remove the ${what}`} title="Remove (Delete)"
              onClick={() => remove(s.index)}>✕</button>
          </span>
        );
        const item = u.id ? byId.get(u.id) : undefined;
        return (
          <div
            key={keyOf(u)}
            ref={(el) => {
              if (el) cards.current.set(u.start, el);
            }}
            className={`block-card ${u.kind}`}
            tabIndex={0}
            role="group"
            aria-roledescription="card"
            aria-label={what[0]!.toUpperCase() + what.slice(1)}
            onKeyDown={(e) => onCardKey(e, s.index)}
          >
            {u.kind === "quote" ? (
              <QuoteCard markdown={markdown} unit={u} actions={actions} />
            ) : item && item.kind !== "report" && item.kind !== "thesis" ? (
              <Embed token={token} item={{ ...item, kind: item.kind, ref: item.item_id }} label={label} readable={readable}
                hasTtm={hasTtm} onOpenChart={onOpenChart} actions={actions} />
            ) : (
              <figure className="card report-embed missing">
                <figcaption className="embed-h">
                  <span className="tag">Embed</span> <b>Not in this workspace</b>
                  <span className="grow" />
                  {actions}
                </figcaption>
                <p className="faint small">This embed points at an item that's no longer in this workspace, so it isn't drawn. Remove it, or save the item here again.</p>
              </figure>
            )}
          </div>
        );
      })}
      <p className="visually-hidden" role="status" aria-live="polite">{said}</p>
    </div>
  );
}

/** A pinned filing passage: its blockquote and citation rendered as written (markdown.ts, so the
 * EDGAR link and escaping are the pin's), headed by what it is and the period it reports on. */
function QuoteCard({ markdown, unit, actions }: { markdown: string; unit: Unit; actions: ReactNode }) {
  const raw = runText(markdown, unit.start, unit.end);
  const html = useMemo(() => renderMarkdown(raw), [raw]);
  const q = quoteInfo(markdown, unit);
  return (
    <figure className="card report-embed quote-card">
      <figcaption className="embed-h">
        <span className={`tag ${q.earnings ? "earnings" : "quote"}`}>{q.earnings ? "Earnings release" : "Quote"}</span>{" "}
        <b>{q.form ?? "Filing"}{q.period ? ` · ${q.period}` : ""}</b>
        <span className="grow" />
        {actions}
      </figcaption>
      <div className="answer report-text" dangerouslySetInnerHTML={{ __html: html }} />
    </figure>
  );
}

/** A short stable key for a card's text (a quote keeps its card, and its place in the DOM, when
 * it moves). */
function hashOf(text: string): string {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
