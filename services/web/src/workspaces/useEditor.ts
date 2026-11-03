import { useEffect, useRef } from "react";

/** Editor pages (thesis, model): Ctrl/Cmd+S saves, and leaving the page with unsaved changes
 * (reload, close) asks the browser to confirm. `active`: false while the editor is mounted but
 * hidden (an analysis project keeps its other steps mounted), so only the visible one saves;
 * the leave-page warning stays on for every dirty editor. */
export function useEditorKeys(dirty: boolean, onSave: () => void, active = true): void {
  const save = useRef(onSave);
  save.current = onSave;
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
  useEffect(() => {
    if (!dirty) return;
    const onLeave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [dirty]);
}
