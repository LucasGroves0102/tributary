/** Loading, empty and error states shared by every data panel: skeletons (not spinners) shaped
 * like what is coming, empty states that say what to do next, errors with a retry. */

import { useEffect, useState, type ReactNode } from "react";

import { NOT_ANSWERING } from "../api";

/** Grey placeholder bars while a panel loads: `lines` rows of a table or list, or a chart
 * area. Announced once to screen readers as `label`. */
export function Skeleton({ lines = 4, chart = false, label = "Loading" }: { lines?: number; chart?: boolean; label?: string }) {
  return (
    <div className="skel" role="status" aria-live="polite" aria-busy="true">
      <span className="visually-hidden">{label}…</span>
      {chart ? (
        <div className="skel-chart" aria-hidden="true" />
      ) : (
        Array.from({ length: lines }, (_, i) => <div key={i} className={`skel-line w${(i % 3) + 1}`} aria-hidden="true" />)
      )}
    </div>
  );
}

/** A ledger's shape while it loads (spec §7.6): its real column headers and rules, each value a blank `well`
 * block, nothing shimmering; after a second the blanks pulse slowly (`data-loading="slow"`). */
export function LedgerShape({ first, columns, rows = 8, label = "Loading" }: {
  /** the first (name) column's header */
  first: string;
  /** the other columns' headers, as the table will show them */
  columns: string[];
  rows?: number;
  label?: string;
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 1000);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="skel ledger-shape" role="status" aria-live="polite" aria-busy="true" data-loading={slow ? "slow" : undefined}>
      <span className="visually-hidden">{label}…</span>
      <table className="t shape" aria-hidden="true">
        <thead>
          <tr>
            <th className="first">{first}</th>
            {columns.map((c, i) => <th key={i} className="n">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              <td className="first"><span className={`blank-v w-${[160, 120, 140, 104][r % 4]}`} /></td>
              {columns.map((_, i) => <td key={i} className="n"><span className={`blank-v w-${[56, 48, 40][(r + i) % 3]}`} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A passage's shape while it loads (spec §7.6: the section's own shape, not generic bars): three lines of
 * the measure, blank (company.css `.blank-v`). */
export function TextShape() {
  return (
    <div className="text-shape" aria-hidden="true">
      <span className="blank-v w-480" /><span className="blank-v w-480" /><span className="blank-v w-240" />
    </div>
  );
}

/** `detail` as a sentence: reasons are fragments, some ending in a period. */
export function sentence(detail: string): string {
  const t = detail.trim();
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}`;
}

/** A friendly error: what couldn't load, why if known (failureReason), and a retry. Only a
 * failure that clears by itself (a 5xx or no answer: failureReason's NOT_ANSWERING) says it
 * usually clears in a moment; a 4xx's reason (a request the API refused) stands on its own. */
export function ErrorPanel({ what, detail, onRetry }: { what: string; detail?: string; onRetry?: () => void }) {
  const transient = detail === undefined || detail === NOT_ANSWERING;
  return (
    <div className="errpanel" role="alert">
      <p>
        <b>Couldn't load {what}.</b> {sentence(detail ?? NOT_ANSWERING)}
        {onRetry && transient ? " It usually clears in a moment." : ""}
      </p>
      {onRetry && (
        <button className="btn sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/** An empty panel phrased as the next step, with an optional action. */
export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty next">
      <p>{children}</p>
      {action}
    </div>
  );
}
