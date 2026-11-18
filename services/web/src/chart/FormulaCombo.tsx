import { useEffect, useId, useMemo, useRef, useState } from "react";

import { screenerColumns, searchCompanies } from "../api";
import { resultTicker } from "../components/CompanySearch";
import { apply as applyCompletion, complete, MAX_SUGGESTIONS, type Suggestion, type Vocabulary } from "./autocomplete";
import { catalog, type MetricInfo } from "./metrics";

const KIND_LABEL: Record<Suggestion["kind"], string> = { metric: "metric", ticker: "company", function: "function", own: "your data" };

/** The metric catalog (/v1/screener/columns), the formula vocabulary's names: null while it loads,
 * [] when it can't be read. One vocabulary for every formula box (chart builder, Your data). */
export function useMetricCatalog(token: string): MetricInfo[] | null {
  const [out, setOut] = useState<MetricInfo[] | null>(null);
  useEffect(() => {
    let live = true;
    screenerColumns(token)
      .then((r) => live && setOut(catalog(r.columns)))
      .catch(() => live && setOut([]));
    return () => {
      live = false;
    };
  }, [token]);
  return out;
}

/** A formula field with autocomplete (chart/autocomplete.ts: metrics, readable companies' tickers,
 * functions, and the user's own fields when given): the chart builder's formula box and Your data's
 * metric (Riley, test 10, finding 9: Your data's said "start typing to see the metric names" and
 * offered none). Tab takes the highlighted suggestion; ↑ ↓ move, and Enter takes one once you've
 * moved to it, else `onEnter`; Escape closes the list; blur is `onCommit`. Checking the formula is
 * the caller's (the API's). */
export function FormulaCombo({ token, value, onChange, vocab, placeholder, label = "Formula", invalid = false, onCommit, onEnter,
  onFocusChange, maxLength = 500, className = "field mono", required = false }: {
  token: string;
  value: string;
  onChange: (text: string) => void;
  vocab: Vocabulary;
  placeholder?: string;
  label?: string;
  invalid?: boolean;
  onCommit?: () => void;
  /** Enter with no suggestion taken (a form submits by itself when this is absent) */
  onEnter?: () => void;
  onFocusChange?: (focused: boolean) => void;
  maxLength?: number;
  className?: string;
  required?: boolean;
}) {
  // the cursor while suggesting (null: list closed), the highlighted row, and whether the user
  // moved to it (only then does Enter take it instead of applying the formula)
  const [cursor, setCursor] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const [moved, setMoved] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<number | null>(null);
  const listId = useId();
  // a capitalized word being typed may be a ticker: asked of the server, like every company search
  const typed = cursor === null ? null : (/(?:^|[^A-Za-z0-9_.])([A-Z][A-Z0-9-]{0,9})$/.exec(value.slice(0, cursor))?.[1] ?? null);
  const [found, setFound] = useState<{ word: string; tickers: Vocabulary["tickers"] }>({ word: "", tickers: [] });
  useEffect(() => {
    if (!typed) return;
    let live = true;
    const t = setTimeout(() => {
      searchCompanies(token, typed, true, MAX_SUGGESTIONS)
        .then((rs) => {
          if (!live) return;
          setFound({
            word: typed,
            tickers: rs.flatMap((r) => {
              const ticker = resultTicker(r);
              return ticker ? [{ ticker, name: r.name }] : [];
            }),
          });
        })
        .catch(() => {});
    }, 150);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [token, typed]);
  const words = useMemo((): Vocabulary => ({
    ...vocab,
    tickers: [...new Map([...vocab.tickers, ...found.tickers].map((t) => [t.ticker, t])).values()],
  }), [vocab, found]);
  const comp = cursor === null ? null : complete(value, cursor, words);
  const open = !!comp && comp.items.length > 0;

  useEffect(() => {
    if (pending.current !== null && input.current) {
      input.current.setSelectionRange(pending.current, pending.current);
      pending.current = null;
    }
  }, [value]);

  const choose = (item: Suggestion) => {
    if (!comp) return;
    const r = applyCompletion(value, comp, item);
    pending.current = r.cursor;
    onChange(r.text);
    setCursor(r.cursor);
    setActive(0);
    setMoved(false);
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (open && comp) {
      const n = comp.items.length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => (a + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
        setMoved(true);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && moved)) {
        e.preventDefault();
        choose(comp.items[Math.min(active, n - 1)]!);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setCursor(null);
        return;
      }
    }
    if (e.key === "Enter") {
      setCursor(null);
      onEnter?.();
    }
  };

  return (
    <div className="ac">
      <input
        ref={input}
        className={className}
        value={value}
        maxLength={maxLength}
        required={required}
        placeholder={placeholder}
        aria-label={label}
        aria-invalid={invalid}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value);
          setCursor(e.target.selectionStart ?? e.target.value.length);
          setActive(0);
          setMoved(false);
        }}
        onFocus={() => onFocusChange?.(true)}
        onBlur={() => {
          onFocusChange?.(false);
          setCursor(null);
          onCommit?.();
        }}
        onKeyDown={onKey}
      />
      {open && comp && (
        <ul className="ac-list" id={listId} role="listbox" aria-label="Formula suggestions">
          {comp.items.map((item, i) => (
            <li key={`${item.kind}-${item.label}`} id={`${listId}-${i}`} role="option" aria-selected={i === active}
              className={i === active ? "on" : undefined}
              onMouseDown={(e) => {
                e.preventDefault(); // keep focus in the field
                choose(item);
              }}>
              <span className={`ac-k ${item.kind}`}>{KIND_LABEL[item.kind]}</span>
              <b className="mono">{item.label}</b>
              {item.detail && <span className="faint">{item.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
