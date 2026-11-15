import { useEffect, useSyncExternalStore } from "react";

/** One toast system (Riley 101, 113, pattern 8: the chart builder's and Analysis's confirmations were
 * banners that stayed on the page, and carried over to the next step). A toast says what just happened,
 * with at most one action (Undo, Restore, Open), and goes by itself: 6 seconds, 10 with an action, an
 * error stays until dismissed. They stack at the foot of the window with the delete undo notes
 * (workspaces/Actions `UndoDeletes`), so a toast never covers the top bar. A module store, so anything
 * (a hook, a page, App) can raise one without passing props. */

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: number;
  text: string;
  action?: ToastAction;
  /** an error: role alert, stays until dismissed */
  error?: boolean;
  /** what the toast is about, so a newer one on the same subject replaces it */
  topic?: string;
}

const PLAIN_MS = 6000;
const ACTION_MS = 10000;

let toasts: Toast[] = [];
let seq = 0;
const listeners = new Set<() => void>();
const timers = new Map<number, number>();

function emit() {
  for (const l of listeners) l();
}

/** Shows a toast; returns its id. A toast with the same `topic` as one on screen replaces it. */
export function toast(t: Omit<Toast, "id">): number {
  const id = ++seq;
  const replaced = t.topic ? toasts.filter((x) => x.topic === t.topic) : [];
  for (const r of replaced) clearTimer(r.id);
  toasts = [...toasts.filter((x) => !replaced.includes(x)), { ...t, id }];
  if (!t.error) {
    timers.set(id, window.setTimeout(() => dismissToast(id), t.action ? ACTION_MS : PLAIN_MS));
  }
  emit();
  return id;
}

function clearTimer(id: number) {
  const t = timers.get(id);
  if (t !== undefined) window.clearTimeout(t);
  timers.delete(id);
}

export function dismissToast(id: number) {
  clearTimer(id);
  if (!toasts.some((t) => t.id === id)) return;
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

/** Clears the toasts about a subject (a step changed, the chart was replaced again). */
export function dismissTopic(topic: string) {
  for (const t of toasts.filter((x) => x.topic === topic)) dismissToast(t.id);
}

/** For tests: forget every toast. */
export function clearToasts() {
  for (const id of [...timers.keys()]) clearTimer(id);
  toasts = [];
  emit();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(subscribe, () => toasts, () => toasts);
}

/** The toasts on screen, as notes in the foot stack (rendered by UndoDeletes' portal). */
export function ToastNotes() {
  const list = useToasts();
  return (
    <>
      {list.map((t) => (
        <p key={t.id} className={t.error ? "undo-note failed" : "undo-note"} role={t.error ? "alert" : "status"}>
          <span>{t.text}</span>
          {t.action && (
            <button className="btn sm" onClick={() => {
              dismissToast(t.id);
              t.action!.onClick();
            }}>
              {t.action.label}
            </button>
          )}
          <button className="btn sm ghost icon" aria-label="Dismiss" title="Dismiss" onClick={() => dismissToast(t.id)}>×</button>
        </p>
      ))}
    </>
  );
}

/** Raises a toast whenever `text` becomes non-null (for a page that keeps its message in state). */
export function useToastOn(text: string | null | undefined, opts: Omit<Toast, "id" | "text"> = {}) {
  useEffect(() => {
    if (text) toast({ ...opts, text });
    // opts is a fresh object each render: the text decides
  }, [text]);
}
