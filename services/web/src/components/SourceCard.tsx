import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { popIn, popOut } from "../motion";
import { Mark, MARK_WORDS, type MarkKind } from "./Mark";

/** What a figure's source card says (.interface-design/system.md §4, §5): only what the API holds about
 * the figure. A card never retypes a quote and never shows an exact figure Tributary doesn't hold; the
 * filing's own printed line needs a cell-to-line lookup (spec §8, product work), so a card names where
 * the figure sits and opens the filing there instead. */
export interface SourceInfo {
  /** what and which period: "Revenue · three months to Jul 26, 2026" */
  title: string;
  /** the figure as Tributary holds it ("$96,221 million" when exact, else as shown) */
  value: string;
  mark: MarkKind | null;
  /** the mark's words when they need more than MARK_WORDS ("derived: …") */
  words?: string;
  /** where it sits: the form and its filing date, the statement */
  where?: string[];
  /** how it is made: a derived figure's rule, Σ's terms, a ratio's formula */
  how?: string;
  /** opens the filing in the reader */
  reader?: { href: string; label: string } | null;
  /** the filing on EDGAR */
  edgar?: string | null;
  note?: string;
}

/** One card at a time, app-wide; the tooltip rule (§5.2): another card opens at once for 400ms after one closes. */
let closeOpen: ((instant: boolean) => void) | null = null;
let lastClose = 0;
const QUICK_MS = 400;
const HOVER_MS = 200;
const LEAVE_MS = 150;

const finePointer = () => typeof window !== "undefined" && !!window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;

interface Open {
  keyboard: boolean;
  /** opened by a hover: it closes when the pointer leaves, unless clicked */
  hover: boolean;
}

/** A trigger's card. `hover`: a standalone figure opens on hover after 200ms on a fine pointer (§5.2);
 * table cells never do. Returns the trigger's handlers and the card (portalled to the body). */
export function useSourceCard(info: () => SourceInfo | null, { hover = true, onOpenChange }: { hover?: boolean; onOpenChange?: (open: boolean) => void } = {}) {
  const [open, setOpen] = useState<Open | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const id = useId();

  const cardEl = useRef<HTMLDivElement | null>(null);
  const close = useCallback((instant = true, refocus = false) => {
    window.clearTimeout(timer.current);
    const el = cardEl.current;
    // a pointer's close fades 90ms; a key's is instant (§7.2)
    if (!instant && el) popOut(el, () => setOpen(null));
    else setOpen(null);
    if (refocus) trigger.current?.focus({ preventScroll: true });
  }, []);

  const show = useCallback((how: Open) => {
    window.clearTimeout(timer.current);
    if (!info()) return;
    if (closeOpen && closeOpen !== closeRef.current) closeOpen(true);
    setOpen((o) => (o && !how.hover ? { ...o, hover: false } : o ?? how));
  }, [info]);
  const closeRef = useRef<((instant: boolean) => void) | null>(null);
  closeRef.current = (instant: boolean) => close(instant);

  useEffect(() => {
    onOpenChange?.(!!open);
    if (!open) return;
    const mine = closeRef.current;
    closeOpen = mine;
    return () => {
      if (closeOpen === mine) closeOpen = null;
      lastClose = Date.now();
    };
  }, [!!open]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const props = {
    "aria-haspopup": "dialog" as const,
    "aria-expanded": !!open,
    ...(open ? { "aria-controls": id } : {}),
    onClick: (e: MouseEvent<HTMLElement>) => {
      trigger.current = e.currentTarget;
      if (open && !open.hover) return close(e.detail === 0);
      show({ keyboard: e.detail === 0, hover: false });
    },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.key === "Escape" && open) {
        e.stopPropagation();
        close(true);
      }
    },
    ...(hover ? {
      onMouseEnter: (e: MouseEvent<HTMLElement>) => {
        if (!finePointer()) return;
        trigger.current = e.currentTarget;
        window.clearTimeout(timer.current);
        if (open) return;
        timer.current = window.setTimeout(() => show({ keyboard: false, hover: true }), Date.now() - lastClose < QUICK_MS ? 0 : HOVER_MS);
      },
      onMouseLeave: () => {
        window.clearTimeout(timer.current);
        if (open?.hover) timer.current = window.setTimeout(() => close(false), LEAVE_MS);
      },
    } : {}),
  };

  /** Opens by keyboard from outside (a ledger's Enter or Space on a cell). */
  const openFrom = useCallback((el: HTMLElement, keyboard: boolean) => {
    trigger.current = el;
    show({ keyboard, hover: false });
  }, [show]);

  const card = open && trigger.current ? (
    <Card id={id} info={info()} anchor={trigger.current} keyboard={open.keyboard} quick={Date.now() - lastClose < QUICK_MS} cardRef={cardEl}
      onClose={(refocus, instant) => close(instant, refocus)}
      onHover={(inside) => {
        window.clearTimeout(timer.current);
        if (!inside && open.hover) timer.current = window.setTimeout(() => close(false), LEAVE_MS);
      }} />
  ) : null;

  return { props, card, open: !!open, openFrom, close: () => close(true) };
}

function Card({ id, info, anchor, keyboard, quick, cardRef, onClose, onHover }: {
  id: string;
  cardRef: { current: HTMLDivElement | null };
  info: SourceInfo | null;
  anchor: HTMLElement;
  keyboard: boolean;
  quick: boolean;
  onClose: (refocus: boolean, instant: boolean) => void;
  onHover: (inside: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    cardRef.current = el;
    if (!el) return;
    place(el, anchor);
    popIn(el, { keyboard, quick });
    if (keyboard) (el.querySelector<HTMLElement>("a[href]") ?? el).focus({ preventScroll: true });
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!el.contains(t) && !anchor.contains(t)) onClose(false, false);
    };
    const onScroll = () => place(el, anchor);
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      window.removeEventListener("resize", onScroll);
      if (cardRef.current === el) cardRef.current = null;
    };
  }, [anchor]);
  if (!info) return null;
  const words = info.words ?? (info.mark ? MARK_WORDS[info.mark] : null);
  return createPortal(
    <div ref={ref} id={id} className="pop source-card" role="dialog" aria-label={`Source of ${info.title}`} tabIndex={-1}
      onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          e.preventDefault();
          onClose(true, true);
        }
      }}
      onBlur={(e) => {
        const to = e.relatedTarget as Node | null;
        if (to && !ref.current?.contains(to) && !anchor.contains(to)) onClose(false, true);
      }}>
      <p className="sc-title">{info.title}</p>
      <p className="sc-value">
        <b className={info.mark === "d" ? "derived" : undefined}>{info.value}</b>
        {info.mark && words && (
          <span className={`sc-agree sc-${info.mark}`}>
            <Mark kind={info.mark} />
            {words}
          </span>
        )}
      </p>
      {(info.where?.length || info.how) && (
        <div className="sc-where">
          {info.how && <p>{info.how}</p>}
          {info.where?.map((w) => <p key={w}>{w}</p>)}
        </div>
      )}
      {info.note && <p className="sc-note">{info.note}</p>}
      {(info.reader || info.edgar) && (
        <p className="sc-foot">
          {info.reader && <a href={info.reader.href}>{info.reader.label} →</a>}
          {info.edgar && (
            <a href={info.edgar} target="_blank" rel="noopener noreferrer" className="sc-edgar">EDGAR ↗</a>
          )}
        </p>
      )}
    </div>,
    document.body,
  );
}

/** Below the trigger, above it when there's no room; inside the window with 16px to spare; the scale's
 * origin at the trigger's corner (§7.4: origin-aware). Positioned through the CSSOM (the CSP allows it). */
function place(el: HTMLElement, anchor: HTMLElement) {
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let y = r.bottom + 8;
  let above = false;
  if (r.bottom + 8 + h > vh && r.top - 8 - h > 0) {
    y = r.top - 8 - h;
    above = true;
  }
  const x = Math.max(16, Math.min(r.left, vw - w - 16));
  el.style.left = `${x + window.scrollX}px`;
  el.style.top = `${y + window.scrollY}px`;
  const ox = Math.max(12, Math.min(w - 12, r.left - x + Math.min(r.width, 40) / 2));
  el.style.transformOrigin = `${ox}px ${above ? "100%" : "0"}`;
}

/** A standalone figure with its mark: one button, so the whole figure is the target (§4: at least 24px
 * tall on a fine pointer, 44 on a coarse one). Its text stays the figure (the mark is drawn). */
export function FigureButton({ text, mark, info, className, hero = false, children }: {
  text: string;
  mark: MarkKind | null;
  info: () => SourceInfo | null;
  className?: string;
  hero?: boolean;
  children?: ReactNode;
}) {
  const { props, card } = useSourceCard(info);
  return (
    <>
      <button type="button" className={["fig", hero && "fig-hero", mark === "d" && "derived", className].filter(Boolean).join(" ")} {...props}>
        <span className="v">{text}</span>
        {mark && <Mark kind={mark} size={hero ? "hero" : "ledger"} />}
        {children}
      </button>
      {card}
    </>
  );
}
