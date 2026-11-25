/** The motion system (.interface-design/system.md §7): what moves, for how long, and what never does.
 *
 * Motion has three jobs only: say where you went (a page change), confirm the tool heard you (a press,
 * a popover), and hand loading over to content. It uses transform and opacity only, nothing longer than
 * 280ms, exits faster than entrances, no ease-in, no spring. The shell, figures and anything started from
 * the keyboard never move.
 *
 * Page changes run as same-document View Transitions (`document.startViewTransition`): `useRoute` calls
 * `transition()` with the update, and the CSS in ui.css says what each named part does (`topbar`, `rail`,
 * `assist` frozen; `page-head` and `content` fade, the content sliding 12px on a depth change; a company's
 * name travels as `entity-title` from the row that was clicked into the page's h1). Without the API the
 * same choreography runs with the Web Animations API. `html[data-motion="reduced"]` follows
 * prefers-reduced-motion: nothing travels or overlaps, the new view fades in over 120ms. */

import { pageOf, type Route } from "./route";

/** §7.3, in milliseconds (the CSS custom properties in tokens.css say the same). */
export const T = {
  press: 120, hoverOut: 80, out: 90, in: 160, delayIn: 60, popIn: 140, popOut: 90, tab: 180, shared: 260, frame: 280,
  panelIn: 240, panelOut: 180, values: 120,
} as const;
export const EASE_ENTER = "cubic-bezier(0.23, 1, 0.32, 1)";
export const EASE_MOVE = "cubic-bezier(0.32, 0.72, 0, 1)";

export type NavKind = "none" | "forward" | "back" | "lateral" | "tab";

const html = () => document.documentElement;

/** Under prefers-reduced-motion (or a test's override): nothing travels, popovers fade 100ms. */
export function reduced(): boolean {
  return html().dataset.motion === "reduced";
}

let lastKeyboard = 0;
let installed = false;

/** Whether what just happened was started from the keyboard (Enter on a link, a shortcut, the
 * palette): such a change is instant (§7.2). A click with `detail === 0` is a keyboard activation. */
export function viaKeyboard(now = performance.now()): boolean {
  return now - lastKeyboard < 500;
}

/** Marks the next change as keyboard-started (a shortcut that navigates or opens a panel). */
export function markKeyboard(): void {
  lastKeyboard = performance.now();
}

/** Once, at start: the reduced-motion flag on <html>, and which input started the next change. */
export function installMotion(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const apply = () => {
    if (query?.matches) html().dataset.motion = "reduced";
    else delete html().dataset.motion;
  };
  apply();
  query?.addEventListener?.("change", apply);
  window.addEventListener("keydown", (e) => {
    // typing in a field never counts; Enter, Space and shortcuts do
    if (e.key === "Enter" || e.key === " " || e.ctrlKey || e.metaKey || e.altKey || /^[a-z?/]$/i.test(e.key)) {
      const t = e.target as HTMLElement | null;
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && t?.closest?.("input, textarea, select, [contenteditable]")) return;
      lastKeyboard = performance.now();
    }
  }, true);
  window.addEventListener("pointerdown", () => {
    lastKeyboard = 0;
  }, true);
  window.addEventListener("click", (e) => {
    if (e.detail === 0 && e.isTrusted) lastKeyboard = performance.now();
  }, true);
  // the row a pointer clicked: its company's name travels into the page title (§7.4)
  window.addEventListener("click", (e) => {
    if (e.detail === 0) return;
    const a = (e.target as HTMLElement | null)?.closest?.<HTMLAnchorElement>('a[href^="#/company/"]');
    clicked = a ? { el: a.querySelector<HTMLElement>("[data-entity-name]") ?? a, href: a.getAttribute("href") ?? "" } : null;
  }, true);
}

let clicked: { el: HTMLElement; href: string } | null = null;

/** How deep a page sits: lists 1, a company 2, a filing 3. Direction comes from depth, not history. */
function depth(r: Route): number {
  return r.view === "company" ? 2 : r.view === "filing" ? 3 : 1;
}

export function kindOf(prev: Route | null, next: Route): NavKind {
  if (!prev || pageOf(prev) === pageOf(next)) return "none";
  if (prev.view === "company" && next.view === "company" && prev.companyId === next.companyId) return "tab";
  const a = depth(prev);
  const b = depth(next);
  return b > a ? "forward" : b < a ? "back" : "lateral";
}

/** What a page change says to a screen reader, and where focus goes (§7.7): the h1, without a ring;
 * a tab change keeps the focus where it is. */
function settle(kind: NavKind) {
  const live = document.getElementById("route-announce");
  const h1 = document.querySelector<HTMLElement>("main.content h1");
  if (live) live.textContent = document.title.replace(/ · Tributary$/, "");
  if (kind === "tab" || kind === "none" || !h1) return;
  // a control inside the page that already took the focus (a page's own autofocus) keeps it
  const active = document.activeElement;
  if (active && active !== document.body && document.querySelector("main.content")?.contains(active)) return;
  if (!h1.hasAttribute("tabindex")) h1.setAttribute("tabindex", "-1");
  h1.focus({ preventScroll: true });
}

function clearNames() {
  for (const el of document.querySelectorAll<HTMLElement>(".vt-entity")) el.classList.remove("vt-entity");
  delete html().dataset.nav;
  delete html().dataset.title;
}

/** The page title's name when it shows the company the clicked row named: both ends of the shared
 * element must be the same entity (§7.4: never a row to a different company). */
function entityTarget(next: Route): HTMLElement | null {
  if (next.view !== "company") return null;
  return document.querySelector<HTMLElement>(`main.content [data-entity-title="${next.companyId}"]`);
}

function entitySource(prev: Route, next: Route): HTMLElement | null {
  if (next.view !== "company" || !clicked || !clicked.el.isConnected) return null;
  const want = `#/company/${next.companyId}`;
  if (clicked.href !== want && !clicked.href.startsWith(`${want}/`) && !clicked.href.startsWith(`${want}?`)) return null;
  // only from a list (the screener, Home's watchlist), never from another company's page
  return prev.view === "company" ? null : clicked.el;
}

/** Back from a company to its list: the row naming it, if the list shows it. */
function rowFor(companyId: string): HTMLElement | null {
  const a = document.querySelector<HTMLAnchorElement>(`main.content a[href="#/company/${companyId}"]`);
  return a ? (a.querySelector<HTMLElement>("[data-entity-name]") ?? a) : null;
}

let running: ViewTransition | null = null;

/** Runs `update` (a synchronous React commit, flushSync) as the page change from `prev` to `next`. */
export function transition(prev: Route | null, next: Route, update: () => void): void {
  const kind = kindOf(prev, next);
  const keyboard = viaKeyboard();
  const source = prev && !reduced() && !keyboard && kind === "forward" ? entitySource(prev, next) : null;
  clicked = null;
  if (kind === "none" || keyboard || typeof document === "undefined" || document.visibilityState === "hidden") {
    running?.skipTransition();
    update();
    if (kind !== "none") settle(kind);
    return;
  }
  const start = (document as Document & { startViewTransition?: (cb: () => void) => ViewTransition }).startViewTransition;
  if (typeof start === "function") {
    running?.skipTransition();
    clearNames();
    html().dataset.nav = kind;
    if (source) {
      source.classList.add("vt-entity");
      html().dataset.title = "";
    }
    const back = kind === "back" && prev?.view === "company" && !reduced() ? prev.companyId : null;
    const vt = start.call(document, () => {
      if (source) source.classList.remove("vt-entity");
      update();
      if (source) entityTarget(next)?.classList.add("vt-entity");
      if (back) {
        const row = rowFor(back);
        if (row) {
          row.classList.add("vt-entity");
          html().dataset.title = "";
        }
      }
    });
    running = vt;
    // an aborted transition still updates the DOM: never let its promises reject unhandled (§7.7)
    vt.ready.catch(() => undefined);
    vt.updateCallbackDone.catch(() => undefined);
    vt.finished.catch(() => undefined).finally(() => {
      if (running === vt) running = null;
      clearNames();
      settle(kind);
    });
    return;
  }
  fallback(kind, source, update);
  settle(kind);
}

/** The same choreography without View Transitions: the new content fades in (and slides on a depth
 * change), the head after the title has passed, and the title flies from the row (FLIP). */
function fallback(kind: NavKind, source: HTMLElement | null, update: () => void) {
  const from = source?.getBoundingClientRect() ?? null;
  update();
  const r = reduced();
  const dx = r ? 0 : kind === "forward" ? 12 : kind === "back" ? -12 : 0;
  const fade = (el: Element | null, delay: number) => {
    if (!el || typeof (el as HTMLElement).animate !== "function") return;
    (el as HTMLElement).animate(
      [{ opacity: 0, transform: `translateX(${dx}px)` }, { opacity: 1, transform: "none" }],
      { duration: r ? 120 : T.in, delay: r ? 0 : delay, easing: r ? "linear" : EASE_ENTER, fill: "backwards" },
    );
  };
  const content = document.querySelector(".vt-content");
  if (kind !== "tab") fade(document.querySelector(".vt-head"), kind === "forward" || kind === "back" ? 100 : T.delayIn);
  fade(content, T.delayIn);
  const to = from && !r ? document.querySelector<HTMLElement>("main.content [data-entity-title]") : null;
  if (from && to && typeof to.animate === "function") {
    const box = to.getBoundingClientRect();
    if (box.height > 0) {
      to.animate(
        [{ transformOrigin: "0 0", transform: `translate(${from.left - box.left}px, ${from.top - box.top}px) scale(${from.height / box.height})` },
          { transformOrigin: "0 0", transform: "none" }],
        { duration: T.shared, easing: EASE_MOVE },
      );
    }
  }
}

/** A popover's entrance (source card, menu): scale from 0.97 at its trigger's corner with opacity,
 * 140ms; 100ms opacity only under reduced motion; none when opened by keyboard or `quick` (another
 * closed under 400ms ago, the tooltip rule). */
export function popIn(el: HTMLElement, { keyboard = false, quick = false } = {}): void {
  if (keyboard || quick || typeof el.animate !== "function") return;
  const r = reduced();
  el.animate(r ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 0, transform: "scale(0.97)" }, { opacity: 1, transform: "none" }],
    { duration: r ? 100 : T.popIn, easing: EASE_ENTER });
}

/** A popover's exit: opacity only, 90ms, then `done`. */
export function popOut(el: HTMLElement, done: () => void): void {
  if (typeof el.animate !== "function") return done();
  el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: T.popOut, easing: EASE_ENTER }).finished.then(done, done);
}
