/** Loading, empty and error states shared by every data panel: skeletons (not spinners) shaped
 * like what is coming, empty states that say what to do next, errors with a retry. */

import type { ReactNode } from "react";

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

/** A friendly error: what couldn't load, why if known, and a retry. */
export function ErrorPanel({ what, detail, onRetry }: { what: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className="errpanel" role="alert">
      <p>
        <b>Couldn't load {what}.</b> {detail ?? "The Query API didn't answer; it may be restarting."}
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
