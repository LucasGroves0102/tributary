import { useEffect, useRef, type RefObject } from "react";

/** A popover's ways out, one behaviour for every hand-made one (Marek 5 and 50: the screener's Save and
 * Columns ignored Escape, and Columns stayed open under the Shortcuts sheet): while `open`, Escape closes
 * it and gives the focus back to what opened it (`back()`), and a press outside it (and outside its
 * opener) closes it. A Radix menu or dialog has its own; this is for the forms that float under a
 * button. */
export function useDismiss(
  open: boolean,
  close: () => void,
  panel: RefObject<HTMLElement | null>,
  back: () => HTMLElement | null | undefined,
): void {
  const latest = useRef({ close, back });
  latest.current = { close, back };
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      const to = latest.current.back();
      latest.current.close();
      to?.focus();
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target;
      if (!(t instanceof Node) || panel.current?.contains(t)) return;
      const opener = latest.current.back();
      if (opener?.contains(t)) return;
      // a press in a menu, list or dialog over the page (portalled to the body) isn't on the page
      if (t instanceof Element && t.closest("[role=menu], [role=listbox], [role=dialog], [role=alertdialog]")) return;
      latest.current.close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, panel]);
}
