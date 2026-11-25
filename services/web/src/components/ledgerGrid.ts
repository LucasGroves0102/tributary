/** The keyboard grid every ledger shares (.interface-design/system.md §4): one tab stop per table. Tab
 * reaches the table once, at its roving cell; ↑↓←→ move between its figures, Home/End jump to the row's
 * ends, Ctrl (or Cmd) + Home/End to the table's, Enter or Space opens the figure's source card (focus
 * goes into the card; Escape there returns it to the cell). Moving is instant (§7.2). The cells are the
 * table's `[data-cell]` elements; anything else (a row's name, an opened row's parts) is passed over. */

import type { KeyboardEvent } from "react";

const cellsOf = (row: Element) =>
  [...row.querySelectorAll<HTMLElement>(":scope > [data-cell]")].filter((c) => window.getComputedStyle(c).display !== "none");

/** tabIndex for a cell: the table's first figure is its one tab stop until another takes it. */
export const cellTab = (row: number, col: number) => (row === 0 && col === 0 ? 0 : -1);

function focusCell(table: HTMLElement, cell: HTMLElement) {
  for (const c of table.querySelectorAll<HTMLElement>("[data-cell]")) c.tabIndex = c === cell ? 0 : -1;
  cell.focus();
  cell.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}

/** onKeyDown for the <table>. */
export function onGridKey(e: KeyboardEvent<HTMLElement>) {
  const cell = (e.target as HTMLElement).closest<HTMLElement>("[data-cell]");
  if (!cell || cell !== e.target) return; // a control inside a cell keeps its keys
  const table = e.currentTarget;
  const rows = [...table.querySelectorAll<HTMLElement>("tr")].filter((r) => cellsOf(r).length > 0);
  const row = cell.closest("tr");
  const i = row ? rows.indexOf(row) : -1;
  if (i < 0) return;
  const cols = cellsOf(rows[i]!);
  const j = cols.indexOf(cell);
  const jump = e.ctrlKey || e.metaKey;
  const at = (ri: number, cj: number) => {
    const r = rows[Math.max(0, Math.min(rows.length - 1, ri))]!;
    const cs = cellsOf(r);
    return cs[Math.max(0, Math.min(cs.length - 1, cj))] ?? null;
  };
  let to: HTMLElement | null = null;
  switch (e.key) {
    case "ArrowDown":
      to = at(jump ? rows.length - 1 : i + 1, j);
      break;
    case "ArrowUp":
      to = at(jump ? 0 : i - 1, j);
      break;
    case "ArrowRight":
      to = at(i, jump ? cols.length - 1 : j + 1);
      break;
    case "ArrowLeft":
      to = at(i, jump ? 0 : j - 1);
      break;
    case "Home":
      to = jump ? at(0, 0) : at(i, 0);
      break;
    case "End":
      to = jump ? at(rows.length - 1, Infinity) : at(i, Infinity);
      break;
    case "Enter":
    case " ":
      e.preventDefault();
      // a keyboard activation (detail 0): the card takes the focus
      cell.click();
      return;
    default:
      return;
  }
  e.preventDefault();
  if (to && to !== cell) focusCell(table, to);
}
