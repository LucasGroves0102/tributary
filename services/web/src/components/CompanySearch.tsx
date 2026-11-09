import { useEffect, useId, useState } from "react";

import type { Readable } from "../access";
import { searchCompanies } from "../api";
import { useScope } from "../scope";
import type { SearchResult } from "../types";

/** A company a picker already knows: chosen before, or offered with nothing typed yet. */
export interface PickedCompany {
  companyId: string;
  name: string;
  ticker?: string | null;
}

/** What the API may rank before the page's own `exclude` and `readable` filters; 8 are shown. */
const ASK = 20;
const SHOW = 8;
const DELAY_MS = 150;

/** The ticker to show beside a result: the one typed if it matched, else the company's first. */
export function resultTicker(r: SearchResult): string | null {
  return r.matched_on === "ticker" && r.is_current ? r.matched_alias : (r.tickers?.[0] ?? null);
}

type Asked = { q: string; results: SearchResult[]; failed?: boolean };

/** The one way to pick a company: type a name or ticker, choose from what the server finds, best
 * match first (`silver.search_companies`), with arrows and Enter or the mouse. Never a list loaded
 * into the browser: 6,000+ names don't fit a dropdown and a truncated one hides companies silently.
 * Identity is public, so every company is listed; ones without a financials grant say so (picking
 * them shows metadata only). In a demo sandbox (scope.ts) only its companies are. `suggestions`
 * are offered while nothing is typed (a watchlist, a user's few grants). */
export function CompanySearch({
  token,
  label,
  placeholder,
  exclude = [],
  onPick,
  readable,
  autoFocus = false,
  disabled = false,
  suggestions,
  onCancel,
}: {
  token: string;
  label: string;
  placeholder: string;
  exclude?: string[];
  onPick: (companyId: string, name: string, ticker: string | null) => void;
  /** list only companies the user can read (access.ts readableFor): asked of the API with
   * access=financials and filtered here too (display only; the API decides) */
  readable?: Readable;
  autoFocus?: boolean;
  disabled?: boolean;
  suggestions?: PickedCompany[];
  /** Escape on an empty box, or leaving the box (CompanyPicker closes) */
  onCancel?: () => void;
}) {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState<Asked | null>(null);
  const [active, setActive] = useState(0);
  const [focused, setFocused] = useState(autoFocus);
  // clicking away closes the list, keeping what was typed; coming back opens it again
  const [left, setLeft] = useState(false);
  const list = useId();
  const scoped = useScope() !== null || readable !== undefined;
  const text = q.trim();

  useEffect(() => {
    if (text.length < 1) {
      setAsked(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      searchCompanies(token, text, scoped, ASK)
        .then((results) => live && setAsked({ q: text, results }))
        .catch(() => live && setAsked({ q: text, results: [], failed: true }));
    }, DELAY_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [token, text, scoped]);

  // nothing typed: the suggestions; typing: the latest answer (the previous one until it comes)
  const rows: { id: string; name: string; ticker: string | null; note: string | null }[] = left
    ? []
    : text
    ? (asked?.results ?? [])
        .filter((r) => !exclude.includes(r.company_id) && (!readable || readable.has(r.company_id)))
        .slice(0, SHOW)
        .map((r) => ({
          id: r.company_id,
          name: r.name,
          ticker: resultTicker(r),
          note: !r.access.includes("financials")
            ? "metadata only"
            : !r.is_current
              ? `formerly ${r.matched_alias}`
              : null,
        }))
    : focused
      ? (suggestions ?? [])
          .filter((s) => !exclude.includes(s.companyId))
          .slice(0, SHOW)
          .map((s) => ({ id: s.companyId, name: s.name, ticker: s.ticker ?? null, note: null }))
      : [];
  const on = Math.min(active, Math.max(rows.length - 1, 0));
  const searching = !!text && asked?.q !== text;
  const nothing = !!text && !left && !searching && rows.length === 0;

  const pick = (r: (typeof rows)[number]) => {
    onPick(r.id, r.name, r.ticker);
    setQ("");
    setActive(0);
  };

  return (
    <div className="co-search">
      <input
        className="field"
        type="search"
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={rows.length > 0}
        aria-controls={list}
        aria-activedescendant={rows.length ? `${list}-${on}` : undefined}
        autoComplete="off"
        placeholder={placeholder}
        value={q}
        maxLength={100}
        autoFocus={autoFocus}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
          setLeft(false);
        }}
        onFocus={() => {
          setFocused(true);
          setLeft(false);
        }}
        onBlur={() => {
          setFocused(false);
          setLeft(true);
          onCancel?.();
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && rows.length) setActive((on + 1) % rows.length);
          else if (e.key === "ArrowUp" && rows.length) setActive((on + rows.length - 1) % rows.length);
          else if (e.key === "Enter" && rows[on]) pick(rows[on]);
          else if (e.key === "Escape") {
            if (text) setQ("");
            else onCancel?.();
          } else return;
          e.preventDefault();
        }}
      />
      {rows.length > 0 && (
        <ul className="co-results" id={list} role="listbox" aria-label={`${label} results`}>
          {rows.map((r, i) => (
            <li key={r.id} role="presentation">
              <button
                type="button"
                id={`${list}-${i}`}
                role="option"
                aria-selected={i === on}
                className={i === on ? "on" : undefined}
                // keep the focus in the box: a click must not count as leaving it
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(r)}
              >
                {r.ticker && <span className="mono co-ticker">{r.ticker}</span>}
                <b>{r.name}</b>
                {r.note && <span className="tag">{r.note}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {nothing && (
        <p className="co-results co-none" role="status">
          {asked?.failed ? "Search didn't answer. Keep typing to try again." : `No company matches “${text}”.`}
        </p>
      )}
    </div>
  );
}

/** A company chosen in a form: shows the choice, opens CompanySearch to change it (or clear it,
 * with `onClear`). The same search everywhere a company is picked. */
export function CompanyPicker({
  token,
  label,
  value,
  placeholder = "Name or ticker",
  emptyLabel,
  onChange,
  onClear,
  readable,
  suggestions,
  title,
}: {
  token: string;
  label: string;
  value: PickedCompany | null;
  placeholder?: string;
  /** what the button says with nothing chosen */
  emptyLabel: string;
  onChange: (company: PickedCompany) => void;
  /** offers "clear": back to no company */
  onClear?: () => void;
  readable?: Readable;
  suggestions?: PickedCompany[];
  title?: string;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <CompanySearch token={token} label={label} placeholder={placeholder} autoFocus readable={readable}
        suggestions={suggestions} onCancel={() => setEditing(false)}
        onPick={(companyId, name, ticker) => {
          setEditing(false);
          onChange({ companyId, name, ticker });
        }} />
    );
  }
  return (
    <span className="co-picked" title={title}>
      <button type="button" className="field co-value" aria-label={`${label}: ${value ? value.name : emptyLabel}. Change`}
        onClick={() => setEditing(true)}>
        {value ? (
          <>
            {value.ticker && <span className="mono co-ticker">{value.ticker}</span>}
            <span className="co-name">{value.name}</span>
          </>
        ) : (
          <span className="faint">{emptyLabel}</span>
        )}
        <span className="faint" aria-hidden="true">▾</span>
      </button>
      {value && onClear && (
        <button type="button" className="btn sm ghost icon" aria-label={`Clear ${label}`} title="Clear" onClick={onClear}>
          ✕
        </button>
      )}
    </span>
  );
}
