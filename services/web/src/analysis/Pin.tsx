import { createContext, useContext, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { DEFAULT_PIN_SECTION, THESIS_SECTIONS, type PinTarget } from "./project";

/** What a project offers its steps: pin a block (built when the user picks where, e.g. after
 * saving a chart) into the project's thesis, and the thesis's pillars to choose from. */
export interface Pinning {
  pin: (target: PinTarget, make: () => Promise<string>, what: string) => Promise<void>;
  /** the thesis's pillars now (its Pillars sub-headings), read when the menu opens */
  pillars: () => string[];
}

interface MenuItem {
  key: string;
  target: PinTarget;
  text: string;
  hint?: string;
}

/** The menu: under Pillars, each pillar the thesis has and a new one (L5: pinning went under
 * "Pillar 1" with no choice), the default first; then the other sections. */
export function pinMenu(pillars: string[]): MenuItem[] {
  const pillarItems: MenuItem[] = pillars.length
    ? [
        ...pillars.map((p, i): MenuItem => ({ key: `pillar:${i}`, target: { section: "pillars", pillar: p }, text: `Pillars › ${p}`, ...(i === 0 ? { hint: "default" } : {}) })),
        { key: "pillar:new", target: { section: "pillars", newPillar: true }, text: "Pillars › New pillar" },
      ]
    : [{ key: "pillars", target: { section: "pillars" }, text: "Pillars", hint: "default" }];
  const rest = THESIS_SECTIONS.filter((s) => s.key !== DEFAULT_PIN_SECTION)
    .map((s): MenuItem => ({ key: s.key, target: { section: s.key }, text: s.heading }));
  return [...pillarItems, ...rest];
}

export const PinContext = createContext<Pinning | null>(null);

/** A pushpin drawn inline (an emoji depends on the system's fonts; the CSP loads none). */
function PinIcon() {
  return (
    <svg className="pin-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M9.8 1.2a.75.75 0 0 1 1.06 0l3.94 3.94a.75.75 0 0 1 0 1.06l-.9.9a.75.75 0 0 1-.68.2l-1.38-.28-2.17 2.17.53 2.66a.75.75 0 0 1-.2.68l-.53.53a.75.75 0 0 1-1.06 0L5.9 10.53l-3.62 3.62a.75.75 0 1 1-1.06-1.06L4.84 9.47 2.31 6.94a.75.75 0 0 1 0-1.06l.53-.53a.75.75 0 0 1 .68-.2l2.66.53 2.17-2.17-.28-1.38a.75.75 0 0 1 .2-.68l.9-.9z" />
    </svg>
  );
}

/** "📌 Add to thesis": a small menu of where in the thesis (each pillar and a new one first: the default),
 * then the project pins the evidence there. Keyboard: Enter opens with Pillars focused,
 * arrows move, Escape closes. Hidden outside a project. */
export function PinButton({ make, what, label = "Add to thesis", compact = false }: {
  /** the Markdown to pin: an embed line, a quote with its citation */
  make: () => Promise<string>;
  /** what is pinned, for the confirmation ("chart", "passage") */
  what: string;
  label?: string;
  /** an icon-sized button (beside a paragraph) */
  compact?: boolean;
}) {
  const ctx = useContext(PinContext);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const menu = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const away = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node) && e.target !== button.current) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  if (!ctx) return null;
  const pick = async (target: PinTarget) => {
    setOpen(false);
    setBusy(true);
    try {
      await ctx.pin(target, make, what);
    } finally {
      setBusy(false);
      button.current?.focus();
    }
  };
  const onMenuKey = (e: KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      button.current?.focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  const items = open ? pinMenu(ctx.pillars()) : [];
  return (
    <span className={compact ? "pin compact" : "pin"}>
      <button ref={button} type="button" className={compact ? "btn sm ghost pin-btn" : "btn sm pin-btn"} disabled={busy}
        aria-haspopup="menu" aria-expanded={open} aria-controls={open ? `${id}-menu` : undefined}
        aria-label={compact ? `${label}: this ${what}` : undefined}
        title={`Pin this ${what} into a section of the project's thesis`} onClick={() => setOpen((o) => !o)}>
        <PinIcon />{compact ? "" : ` ${busy ? "Pinning…" : label}`}
      </button>
      {open && (
        <span ref={menu} id={`${id}-menu`} className="pin-menu" role="menu" aria-label={`Add this ${what} to the thesis section`} onKeyDown={onMenuKey}>
          <span className="faint small pin-menu-h" aria-hidden="true">Add to the thesis under</span>
          {items.map((m) => (
            <button key={m.key} type="button" role="menuitem" onClick={() => void pick(m.target)}>
              {m.text}{m.hint && <span className="faint"> · {m.hint}</span>}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
