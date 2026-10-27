import { useEffect, useState } from "react";

import { searchCompanies } from "../api";
import { useScope } from "../scope";
import type { SearchResult } from "../types";

/** Type a name or ticker, pick a company. Identity is public, so every company is listed;
 * ones without a financials grant say so (picking them shows metadata only). In a demo
 * sandbox (scope.ts) only its companies are. */
export function CompanySearch({
  token,
  label,
  placeholder,
  exclude,
  onPick,
}: {
  token: string;
  label: string;
  placeholder: string;
  exclude: string[];
  onPick: (companyId: string, name: string) => void;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const scoped = useScope() !== null;

  useEffect(() => {
    const text = q.trim();
    if (text.length < 1) {
      setResults([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      searchCompanies(token, text, scoped)
        .then((r) => live && setResults(r))
        .catch(() => live && setResults([]));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [token, q, scoped]);

  const shown = results.filter((r) => !exclude.includes(r.company_id)).slice(0, 8);
  return (
    <div className="co-search">
      <input
        className="field"
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={q}
        maxLength={100}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && shown[0]) {
            onPick(shown[0].company_id, shown[0].name);
            setQ("");
          }
          if (e.key === "Escape") setQ("");
        }}
      />
      {shown.length > 0 && (
        <ul className="co-results" role="listbox" aria-label={`${label} results`}>
          {shown.map((r) => (
            <li key={r.company_id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => {
                  onPick(r.company_id, r.name);
                  setQ("");
                }}
              >
                <b>{r.name}</b>
                {r.matched_on === "ticker" && <span className="mono faint">{r.matched_alias}</span>}
                {!r.access.includes("financials") && <span className="tag">metadata only</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
