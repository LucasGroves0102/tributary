import { useId, useState, type MouseEvent } from "react";

/** A short term with its reason, readable without a hover (Riley, test 10, findings 40 and 47: the
 * confidence mark's reasons were a native tooltip only, which a phone can't show). The term is a quiet
 * button with a dotted underline; a tap, click or Enter opens the reason on its own line under it and
 * closes it again. The hover keeps the same words for a mouse. Quiet on purpose: never a colour. */
export function Why({ label, why, className }: { label: string; why: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const toggle = (e: MouseEvent) => {
    // inside a clickable row (the screener's), the term opens its reason, not the company
    e.stopPropagation();
    setOpen((o) => !o);
  };
  return (
    <span className={["why", open && "open", className].filter(Boolean).join(" ")}>
      <button type="button" className="why-term" aria-expanded={open} aria-controls={id} title={open ? undefined : why} onClick={toggle}>
        {label}
      </button>
      <span id={id} className="why-text" hidden={!open}>{why}</span>
    </span>
  );
}
