/** Keyboard navigation for data tables whose rows open something (screener, watchlist, comps):
 * rows are a roving tab stop (one row is tabbable at a time), Arrow Up/Down move one row,
 * Page Up/Down ten, Home/End the first and last, Enter opens the row (its first link, or the
 * element marked data-open). Keys typed in a control inside the row are left alone. */

import type { KeyboardEvent } from "react";

const PAGE = 10;

/** tabIndex for the i-th navigable row: the first one is the table's tab stop. */
export const rowTab = (i: number) => (i === 0 ? 0 : -1);

/** Props for a navigable row. */
export function navRow(i: number, label?: string) {
  return { "data-nav": "", tabIndex: rowTab(i), ...(label ? { "aria-label": label } : {}) };
}

function focusRow(rows: HTMLTableRowElement[], row: HTMLTableRowElement) {
  for (const r of rows) r.tabIndex = r === row ? 0 : -1;
  row.focus();
}

/** onKeyDown for the <tbody>. */
export function onTableKey(e: KeyboardEvent<HTMLTableSectionElement>) {
  const target = e.target as HTMLElement;
  const row = target.closest("tr");
  if (!row || target !== row) return;
  const rows = [...e.currentTarget.querySelectorAll<HTMLTableRowElement>("tr[data-nav]")];
  const i = rows.indexOf(row);
  if (i < 0) return;
  const to = (j: number) => rows[Math.max(0, Math.min(rows.length - 1, j))];
  const next: Record<string, HTMLTableRowElement | undefined> = {
    ArrowDown: to(i + 1),
    ArrowUp: to(i - 1),
    PageDown: to(i + PAGE),
    PageUp: to(i - PAGE),
    Home: rows[0],
    End: rows.at(-1),
  };
  if (e.key in next) {
    e.preventDefault();
    if (next[e.key]) focusRow(rows, next[e.key]!);
    return;
  }
  if (e.key === "Enter") {
    const open = row.querySelector<HTMLElement>("[data-open]") ?? row.querySelector<HTMLElement>("a[href]");
    if (open) {
      e.preventDefault();
      open.click();
    }
  }
}
