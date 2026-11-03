import { useEffect, useRef } from "react";

/** Editor pages (thesis, model): Ctrl/Cmd+S saves, and leaving the page with unsaved changes
 * (reload, close) asks the browser to confirm. */
export function useEditorKeys(dirty: boolean, onSave: () => void): void {
  const save = useRef(onSave);
  save.current = onSave;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const onLeave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [dirty]);
}
