import { useEffect, useId, useRef } from "react";

import { NAV } from "../route";

/** Every keyboard shortcut, grouped where it works. Keys are shown as on a PC; Cmd replaces
 * Ctrl on a Mac. Keep in step with shortcuts.ts, components/tableNav.ts, chart/LayerChart.tsx
 * and the chart builder's formula field. */
export const SHORTCUTS: { group: string; keys: [string[], string][] }[] = [
  {
    group: "Anywhere",
    keys: [
      [["Ctrl", "K"], "Command palette: companies, metrics, filings, actions"],
      [["Ctrl", "J"], "Open or close the assistant"],
      [["/"], "Ask the assistant"],
      ...NAV.map((n): [string[], string] => [["g", n.key], `Go to ${n.label}`]),
      [["?"], "This list"],
    ],
  },
  {
    group: "Tables (screener, watchlist, comps)",
    keys: [
      [["↑", "↓"], "Previous or next row"],
      [["Page Up", "Page Down"], "Ten rows up or down"],
      [["Home", "End"], "First or last row"],
      [["Ctrl", "↑ / ↓"], "First or last row (also from a cell, keeping the column)"],
      [["→"], "Into the row's cells; ← → then move along it"],
      [["Ctrl", "← / →"], "First or last column"],
      [["Enter"], "Open the row (or the cell's link)"],
      [["Esc"], "From a cell back to its row"],
    ],
  },
  {
    group: "Charts",
    keys: [
      [["← →"], "Previous or next quarter"],
      [["↑ ↓"], "Highlight another layer"],
      [["Home", "End"], "First or last quarter"],
      [["Enter"], "Open the highlighted value's filing"],
    ],
  },
  {
    group: "Formula field",
    keys: [
      [["Tab"], "Take the highlighted suggestion"],
      [["↑ ↓", "Enter"], "Choose another suggestion"],
      [["Esc"], "Close the suggestions"],
    ],
  },
];

/** The "?" sheet: a modal list of shortcuts. Escape, the close button or a click outside
 * closes it; focus returns where it was. */
export function ShortcutSheet({ onClose }: { onClose: () => void }) {
  const title = useId();
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    close.current?.focus();
    return () => before?.focus?.();
  }, []);
  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet card" role="dialog" aria-modal="true" aria-labelledby={title}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape" || e.key === "?") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
          if (e.key === "Tab") e.preventDefault(); // one control: focus stays in the sheet
        }}>
        <div className="card-h">
          <h2 id={title} className="grow">Keyboard shortcuts</h2>
          <button ref={close} className="btn sm ghost" onClick={onClose} aria-label="Close keyboard shortcuts">
            Esc
          </button>
        </div>
        <div className="sheet-b">
          {SHORTCUTS.map((g) => (
            <section key={g.group}>
              <h3>{g.group}</h3>
              <dl>
                {g.keys.map(([keys, what]) => (
                  <div key={what}>
                    <dt>
                      {keys.map((k, i) => (
                        <kbd key={i} className="kbd">{k}</kbd>
                      ))}
                    </dt>
                    <dd>{what}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <div className="foot">On a Mac, Cmd works wherever Ctrl is shown.</div>
      </div>
    </div>
  );
}
