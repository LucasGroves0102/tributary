import { useEffect, useRef } from "react";

import { NAV, type NavView } from "./route";

export type Shortcut =
  | { type: "palette" }
  | { type: "assistant" }
  | { type: "focus-assistant" }
  | { type: "go"; view: NavView };

/** How long after "g" the second key still counts (prototype v3: 900 ms). */
export const G_WINDOW_MS = 900;

type Key = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey"> & {
  target: EventTarget | null;
};

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName) || target.isContentEditable;
}

/**
 * Ctrl/Cmd-K palette and Ctrl/Cmd-J assistant work everywhere; "/" (focus the assistant) and
 * g-then-letter (switch screens) only when not typing. Returns a function that maps one key
 * event to a shortcut, keeping the pending "g" between calls.
 */
export function shortcutReader(now: () => number = Date.now) {
  let gAt = -Infinity;
  return (e: Key): Shortcut | null => {
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (mod && !e.altKey && key === "k") return { type: "palette" };
    if (mod && !e.altKey && key === "j") return { type: "assistant" };
    if (mod || e.altKey || typing(e.target)) return null;
    if (e.key === "/") return { type: "focus-assistant" };
    if (key === "g") {
      gAt = now();
      return null;
    }
    if (now() - gAt < G_WINDOW_MS) {
      gAt = -Infinity;
      const nav = NAV.find((n) => n.key === key);
      if (nav) return { type: "go", view: nav.view };
    }
    return null;
  };
}

/** Listens on window; a matched shortcut's default action (e.g. the browser's Ctrl-K) is
 * prevented. The latest handler is always used, without re-subscribing. */
export function useShortcuts(handler: (s: Shortcut) => void): void {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    const read = shortcutReader();
    const onKey = (e: KeyboardEvent) => {
      const s = read(e);
      if (!s) return;
      e.preventDefault();
      latest.current(s);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
