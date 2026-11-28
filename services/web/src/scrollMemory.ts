/** Where each page was left, kept on its own history entry (`history.state.scroll`), so Back and Forward
 * come back to the same place (Marek 68: Back walked the addresses right but always landed at the top). A
 * new navigation is a new entry with no state: it starts at the top. Written once the scrolling stops, so
 * the browser's limit on history calls is never near. */

const SETTLE_MS = 150;
/** how long a page that is still loading its content may take to be tall enough to go back to */
const RESTORE_FOR_MS = 1500;

export function rememberScroll(): () => void {
  let timer = 0;
  const save = () => {
    timer = 0;
    try {
      const state = (window.history.state ?? {}) as Record<string, unknown>;
      if (state.scroll === window.scrollY) return;
      window.history.replaceState({ ...state, scroll: window.scrollY }, "");
    } catch {
      // a sandboxed frame or a browser refusing: Back starts at the top, as before
    }
  };
  const onScroll = () => {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(save, SETTLE_MS);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  return () => {
    if (timer) window.clearTimeout(timer);
    window.removeEventListener("scroll", onScroll);
  };
}

/** The place this entry was left at, if it was (a Back or Forward to it), else null. */
export function savedScroll(): number | null {
  const state = window.history.state as { scroll?: unknown } | null;
  return typeof state?.scroll === "number" && state.scroll > 0 ? state.scroll : null;
}

/** Scrolls back to `y`, waiting a moment for a page whose content is still arriving to be tall enough. */
export function restoreScroll(y: number, now: () => number = () => performance.now()): void {
  const until = now() + RESTORE_FOR_MS;
  const attempt = () => {
    const room = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo(0, Math.min(y, Math.max(0, room)));
    if (room < y && now() < until) window.requestAnimationFrame(attempt);
  };
  attempt();
}
