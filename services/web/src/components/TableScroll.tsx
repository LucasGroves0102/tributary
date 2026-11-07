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
 * The frozen `.first` column keeps numbers from passing under it unmarked (it shows a shadow once
 * something is under it), a fade marks the cut edge, and a line above the table says how many
 * columns are off screen on each side and moves to them. A table that fits shows none of it. Arrow
 * keys scroll it once it has focus; it is a tab stop only while there is something to scroll. */
export function TableScroll({ children, atEnd = false, resetKey, label = "Table" }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [off, setOff] = useState<Off>(NONE);
  // the user moved away from the far end: a resize must not take them back
  const moved = useRef(false);

  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const scrolls = el.scrollWidth - el.clientWidth > 1;
    const view = el.getBoundingClientRect();
    const cells = scrolls
      ? [...el.querySelectorAll<HTMLTableCellElement>("thead tr:first-child > th")].map((c) => {
          const r = c.getBoundingClientRect();
          return { left: r.left, right: r.right, width: r.width, frozen: c.classList.contains("first") };
        })
      : [];
    const next = { ...columnsOff(cells, { left: view.left, right: view.right }), scrolls };
    setOff((o) => (o.left === next.left && o.right === next.right && o.scrolls === next.scrolls ? o : next));
  }, []);

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
    const onScroll = () => {
      moved.current = el.scrollLeft < el.scrollWidth - el.clientWidth - 2;
      measure();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    if (typeof ResizeObserver === "undefined") return () => el.removeEventListener("scroll", onScroll);
    const onResize = () => {
      if (atEnd && !moved.current) el.scrollLeft = el.scrollWidth;
      measure();
    };
    const seen = new ResizeObserver(onResize);
    seen.observe(el);
    if (el.firstElementChild) seen.observe(el.firstElementChild);
    return () => {
      seen.disconnect();
      el.removeEventListener("scroll", onScroll);
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
