import { useId, useState } from "react";

import type { ColumnInfo } from "../types";
import { columnSpec, HOME_CARD_LABELS, isDefaultHome, move, sameColumns, toggleCard, type HomeCard } from "./model";

/** Home's "Customize": show or hide each card and change their order; saved per user. */
export function HomeLayoutEditor({
  cards,
  onChange,
  onReset,
  onClose,
}: {
  cards: HomeCard[];
  onChange: (cards: HomeCard[]) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  return (
    <section className="card view-editor" aria-label="Customize Home">
      <div className="card-h">
        <h2>Customize Home</h2>
        <span className="sub">show, hide and order the cards; saved to your account</span>
        <span className="grow" />
        <button className="btn sm ghost" onClick={onReset} disabled={isDefaultHome(cards)}>
          Reset to default
        </button>
        <button className="btn sm" onClick={onClose}>
          Done
        </button>
      </div>
      <ol className="view-list">
        {cards.map((c, i) => (
          <li key={c.id}>
            <label className="chk grow">
              <input type="checkbox" checked={!c.hidden} onChange={() => onChange(toggleCard(cards, c.id))} />
              {HOME_CARD_LABELS[c.id]}
            </label>
            <button className="btn sm ghost" aria-label={`Move ${HOME_CARD_LABELS[c.id]} up`} disabled={i === 0}
              onClick={() => onChange(move(cards, i, -1))}>
              ↑
            </button>
            <button className="btn sm ghost" aria-label={`Move ${HOME_CARD_LABELS[c.id]} down`} disabled={i === cards.length - 1}
              onClick={() => onChange(move(cards, i, 1))}>
              ↓
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** A table's "Columns": choose and order its value columns from what the API offers. */
export function ColumnPicker({
  table,
  columns,
  options,
  defaults,
  max,
  onChange,
  onReset,
}: {
  /** "screener" or "watchlist", for labels */
  table: string;
  columns: string[];
  options: ColumnInfo[] | null;
  defaults: string[];
  max: number;
  onChange: (columns: string[]) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const byKey = new Map((options ?? []).map((o) => [o.key, o]));
  const spec = (k: string) => columnSpec(k, byKey.get(k));
  const name = (k: string) => {
    const s = spec(k);
    return `${s.long} (${s.period})`;
  };
  const rest = (options ?? [])
    .filter((o) => !columns.includes(o.key))
    .map((o) => ({ key: o.key, name: name(o.key) }))
    .sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="col-picker">
      <button className="btn sm" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
        disabled={options === null} title={options === null ? "Column choices are unavailable right now" : undefined}>
        Columns
      </button>
      {open && (
        <div className="col-pop" id={id} role="dialog" aria-label={`Choose ${table} columns`}>
          <ol className="view-list">
            {columns.map((k, i) => (
              <li key={k}>
                <span className="grow">{name(k)}</span>
                <button className="btn sm ghost" aria-label={`Move ${name(k)} left`} disabled={i === 0}
                  onClick={() => onChange(move(columns, i, -1))}>
                  ↑
                </button>
                <button className="btn sm ghost" aria-label={`Move ${name(k)} right`} disabled={i === columns.length - 1}
                  onClick={() => onChange(move(columns, i, 1))}>
                  ↓
                </button>
                <button className="btn sm ghost" aria-label={`Remove ${name(k)}`} disabled={columns.length === 1}
                  onClick={() => onChange(columns.filter((x) => x !== k))}>
                  ×
                </button>
              </li>
            ))}
          </ol>
          <div className="row">
            <select
              aria-label={`Add a ${table} column`}
              value=""
              disabled={columns.length >= max}
              onChange={(e) => e.target.value && onChange([...columns, e.target.value])}
            >
              <option value="">{columns.length >= max ? `At most ${max} columns` : "+ Add a column…"}</option>
              {rest.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.name}
                </option>
              ))}
            </select>
            <span className="grow" />
            <button className="btn sm ghost" onClick={onReset} disabled={sameColumns(columns, defaults)}>
              Reset
            </button>
            <button className="btn sm" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
