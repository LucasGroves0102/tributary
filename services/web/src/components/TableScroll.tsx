import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Header cells out of view on each side of a scrolled table. A column counts as off screen when
 * any of it is: a clipped number reads as a different number. `left` starts at the frozen
 * column's right edge, because what's under the frozen column can't be read either. Pure. */
export function columnsOff(
  cells: { left: number; right: number; frozen: boolean; width: number }[],
  view: { left: number; right: number },
): { left: number; right: number } {
  const edge = cells.reduce((e, c) => (c.frozen ? Math.max(e, c.right) : e), view.left);
  let left = 0;
  let right = 0;
  for (const c of cells) {
    if (c.frozen || c.width === 0) continue;
    if (c.right > view.right + 1) right++;
    else if (c.left < edge - 1) left++;
  }
  return { left, right };
}

/** Columns showing only in part: under the frozen column, or cut by the far edge. Their text is
 * blanked while they are (`.tw-cut`), so a number is never read with digits missing. Pure. */
export function cutColumns(
  cells: { left: number; right: number; frozen: boolean; width: number }[],
  view: { left: number; right: number },
): number[] {
  const edge = cells.reduce((e, c) => (c.frozen ? Math.max(e, c.right) : e), view.left);
  return cells.flatMap((c, i) => {
    if (c.frozen || c.width === 0) return [];
    const under = c.left < edge - 1 && c.right > edge + 1;
    const past = c.left < view.right - 1 && c.right > view.right + 1;
    return under || past ? [i] : [];
  });
}

/** How far a table's header moves down so it stays in view while the page scrolls past the table:
 * from 0 (the table's top is below `viewTop`, the sticky top bar's bottom) up to the table's height
 * less the header's, so the header never leaves its own table. Pure. */
export function headerShift(table: { top: number; height: number }, head: number, viewTop: number): number {
  const shift = viewTop - table.top;
  if (shift <= 0) return 0;
  return Math.round(Math.min(shift, Math.max(0, table.height - head)));
}

/** The line a table's header sticks to: the bottom of the app's sticky top bar (`.top`), or the
 * window's top on a page without one (the public status page). */
function stickyTop(): number {
  const bar = document.querySelector(".top");
  return bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0;
}

interface Props {
  children: ReactNode;
  /** open at the far end: statements run oldest to newest, so the newest stays in view when narrow */
  atEnd?: boolean;
  /** what changing means "start over" for `atEnd`, e.g. the number of periods */
  resetKey?: unknown;
  /** names the scrollable region for screen readers */
  label?: string;
}

type Off = { left: number; right: number; scrolls: boolean };
const NONE: Off = { left: 0, right: 0, scrolls: false };

/** Every data table sits in one of these (styles.css "Tables"): a scroller that says when it scrolls.
 * The frozen `.first` column shows a shadow once something is under it, the far edge shows one while
 * there's more that way, a column showing only in part is blanked rather than cut mid-number, and a
 * line above the table says how many columns are off screen on each side and moves to them. A table
 * that fits shows none of it. Arrow keys scroll it once it has focus; it is a tab stop only while
 * there is something to scroll. The header row stays under the top bar while the page scrolls past
 * a long table (`headerShift`). */
export function TableScroll({ children, atEnd = false, resetKey, label = "Table" }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [off, setOff] = useState<Off>(NONE);
  // the user moved away from the far end: a resize must not take them back
  const moved = useRef(false);

  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const scrolls = el.scrollWidth - el.clientWidth > 1;
    const rect = el.getBoundingClientRect();
    const view = { left: rect.left, right: rect.right };
    const header = [...el.querySelectorAll<HTMLTableCellElement>("thead tr:first-child > th")];
    const cells = scrolls
      ? header.map((c) => {
          const r = c.getBoundingClientRect();
          return { left: r.left, right: r.right, width: r.width, frozen: c.classList.contains("first") };
        })
      : [];
    const next = { ...columnsOff(cells, view), scrolls };
    setOff((o) => (o.left === next.left && o.right === next.right && o.scrolls === next.scrolls ? o : next));
    const cut = new Set(cutColumns(cells, view));
    for (const row of el.querySelector("table")?.rows ?? []) {
      if (row.cells.length !== header.length) continue; // a heading across the table
      for (let i = 0; i < row.cells.length; i++) row.cells[i]!.classList.toggle("tw-cut", cut.has(i));
    }
  }, []);

  // The header stays in view while the page scrolls past a long table. `.tw` scrolls sideways, so a
  // `position: sticky` header can only stick inside it, never to the page; instead the real header
  // moves down (`--tw-head-y`, set through the CSSOM, which the CSP's style-src allows, unlike a
  // style attribute). Being the real one, its sort buttons, the frozen first column, the sideways
  // scroll and the cut-column measure all keep working; no copy to keep in step, no nested scroller.
  const placeHead = useRef(() => {});
  placeHead.current = () => {
    const el = box.current;
    const table = el?.querySelector("table");
    const head = table?.tHead;
    if (!el || !table || !head) return;
    const r = table.getBoundingClientRect();
    const y = headerShift({ top: r.top, height: r.height }, head.offsetHeight, stickyTop());
    if (y > 0) {
      el.style.setProperty("--tw-head-y", `${y}px`);
      el.dataset.headMoved = "";
    } else if ("headMoved" in el.dataset) {
      el.style.removeProperty("--tw-head-y");
      delete el.dataset.headMoved;
    }
  };
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (!frame) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          placeHead.current();
        });
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true, capture: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll, { capture: true });
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  // a new sort or page can bring new rows without a scroll or a resize
  useEffect(() => {
    measure();
    placeHead.current();
  });

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    moved.current = false;
    if (atEnd) el.scrollLeft = el.scrollWidth;
    measure();
  }, [atEnd, resetKey, measure]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let frame = 0;
    const onScroll = () => {
      moved.current = el.scrollLeft < el.scrollWidth - el.clientWidth - 2;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    const stop = () => {
      if (frame) cancelAnimationFrame(frame);
      el.removeEventListener("scroll", onScroll);
    };
    if (typeof ResizeObserver === "undefined") return stop;
    const onResize = () => {
      if (atEnd && !moved.current) el.scrollLeft = el.scrollWidth;
      measure();
    };
    const seen = new ResizeObserver(onResize);
    seen.observe(el);
    if (el.firstElementChild) seen.observe(el.firstElementChild);
    return () => {
      seen.disconnect();
      stop();
    };
  }, [atEnd, measure]);

  const go = (direction: 1 | -1) => {
    const el = box.current;
    if (!el) return;
    const frozen = el.querySelector<HTMLElement>("thead th.first")?.offsetWidth ?? 0;
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollBy({ left: direction * (el.clientWidth - frozen) * 0.8, behavior: calm ? "auto" : "smooth" });
  };
  const columns = (n: number) => `${n} ${n === 1 ? "column" : "columns"}`;
  const port = ["tw-port", off.scrolls && "scrolls", off.left > 0 && "more-l", off.right > 0 && "more-r"].filter(Boolean).join(" ");

  return (
    <div className="tw-frame">
      {off.scrolls && (
        <div className="tw-cue">
          {off.left > 0 && (
            <button type="button" className="link" tabIndex={-1} onClick={() => go(-1)} aria-label={`Scroll to ${columns(off.left)} on the left`}>
              ‹ {columns(off.left)}
            </button>
          )}
          <span className="grow" />
          {off.right > 0 && (
            <button type="button" className="link" tabIndex={-1} onClick={() => go(1)} aria-label={`Scroll to ${columns(off.right)} on the right`}>
              {columns(off.right)} ›
            </button>
          )}
        </div>
      )}
      <div className={port}>
        <div
          className="tw"
          ref={box}
          {...(off.scrolls ? { role: "region", "aria-label": `${label}, scrolls sideways`, tabIndex: 0 } : {})}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
