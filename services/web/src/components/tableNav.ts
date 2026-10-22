/** Keyboard navigation for data tables whose rows open something (screener, watchlist, comps):
 * rows are a roving tab stop (one row is tabbable at a time).
 *
 * On a row: Arrow Up/Down move one row, Page Up/Down ten, Home/End and Ctrl (or Cmd) + Up/Down
 * the first and last row, Arrow Right (or Ctrl + Left/Right: the first/last column) steps into
 * the row's cells, Enter opens the row (its first link, or the element marked data-open).
 * On a cell: Left/Right move along the row (Left from the first cell returns to the row),
 * Up/Down keep the column, Ctrl (or Cmd) + an arrow jumps to the first/last row or column,
 * Home/End the row's first/last cell, Escape returns to the row, Enter opens the cell's own
 * link or button, else the row. Keys typed in a control inside a cell are left alone. The
 * shortcut sheet ("?") lists all of this. */

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

const cellsOf = (row: HTMLTableRowElement) =>
  [...row.children].filter((c): c is HTMLTableCellElement => c instanceof HTMLTableCellElement);

/** Focuses a cell (focusable by script only: the row stays the table's tab stop). */
function focusCell(rows: HTMLTableRowElement[], row: HTMLTableRowElement, col: number) {
  const cells = cellsOf(row);
  const cell = cells[Math.max(0, Math.min(cells.length - 1, col))];
  if (!cell) return;
  for (const r of rows) r.tabIndex = r === row ? 0 : -1;
  cell.tabIndex = -1;
  cell.focus();
}

function open(el: HTMLElement): boolean {
  const target = el.querySelector<HTMLElement>("[data-open]") ?? el.querySelector<HTMLElement>("a[href], button:not([disabled])");
  if (!target) return false;
  target.click();
  return true;
}

/** onKeyDown for the <tbody>. */
export function onTableKey(e: KeyboardEvent<HTMLTableSectionElement>) {
  const target = e.target as HTMLElement;
  const row = target.closest("tr");
  if (!row) return;
  const cell = target instanceof HTMLTableCellElement && target.parentElement === row ? target : null;
  if (target !== row && !cell) return; // a control inside the row
  const rows = [...e.currentTarget.querySelectorAll<HTMLTableRowElement>("tr[data-nav]")];
  const i = rows.indexOf(row);
  if (i < 0) return;
  const jump = e.ctrlKey || e.metaKey;
  const to = (j: number) => rows[Math.max(0, Math.min(rows.length - 1, j))]!;
  const vertical: Record<string, HTMLTableRowElement> = {
    ArrowDown: jump ? rows.at(-1)! : to(i + 1),
    ArrowUp: jump ? rows[0]! : to(i - 1),
    PageDown: to(i + PAGE),
    PageUp: to(i - PAGE),
  };
  const act = (move: () => void) => {
    e.preventDefault();
    move();
  };

  if (!cell) {
    if (e.key in vertical) return act(() => focusRow(rows, vertical[e.key]!));
    if (e.key === "Home" || e.key === "End") return act(() => focusRow(rows, e.key === "Home" ? rows[0]! : rows.at(-1)!));
    if (e.key === "ArrowRight" || (jump && e.key === "ArrowLeft")) {
      return act(() => focusCell(rows, row, e.key === "ArrowRight" && jump ? Infinity : 0));
    }
    if (e.key === "Enter") {
      const link = row.querySelector<HTMLElement>("[data-open]") ?? row.querySelector<HTMLElement>("a[href]");
      if (link) act(() => link.click());
    }
    return;
  }

  const col = cellsOf(row).indexOf(cell);
  if (e.key in vertical) return act(() => focusCell(rows, vertical[e.key]!, col));
  if (e.key === "ArrowLeft" && !jump && col === 0) return act(() => focusRow(rows, row));
  const horizontal: Record<string, number> = {
    ArrowLeft: jump ? 0 : col - 1,
    ArrowRight: jump ? Infinity : col + 1,
    Home: 0,
    End: Infinity,
  };
  if (e.key in horizontal) return act(() => focusCell(rows, row, horizontal[e.key]!));
  if (e.key === "Escape") return act(() => focusRow(rows, row));
  if (e.key === "Enter" && (open(cell) || open(row))) e.preventDefault();
}
