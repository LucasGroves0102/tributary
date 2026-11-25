import { Seal } from "./Seal";

/** How a figure relates to a filing (.interface-design/system.md §5.1), always from what the API says
 * about it (its source form, `value_source`, how the figure is worked out), never a guess:
 * - `ok` ✓ in seal red: agrees to the filing, printed in one filing at this value (rounded as shown);
 * - `sum` Σ: added up from figures that carry a mark (twelve months of quarters);
 * - `d` *d*, the figure italic too: derived (a fiscal Q4 = the 10-K's year less its three 10-Qs).
 * A ratio or a difference of marked figures has no mark; its card shows the formula. */
export type MarkKind = "ok" | "sum" | "d";

export const MARK_WORDS: Record<MarkKind, string> = {
  ok: "agrees to the filing",
  sum: "added up",
  d: "derived",
};

/** The mark alone. Σ and d are drawn by CSS, so an element's text stays its figure. */
export function Mark({ kind, size = "ledger" }: { kind: MarkKind; size?: "ledger" | "hero" }) {
  if (kind === "ok") {
    return (
      <span className={`mk mk-ok mk-${size}`} aria-hidden="true">
        <Seal size="ledger" />
      </span>
    );
  }
  return <span className={`mk mk-${kind} mk-${size}`} aria-hidden="true" />;
}

/** The legend, one line directly under what it explains (§5.1): only the marks used there. */
export function MarkLegend({ kinds, derivedWords = "derived: the 10-K's year less its 10-Qs", extra }: {
  kinds: MarkKind[];
  derivedWords?: string;
  extra?: string[];
}) {
  const words: Record<MarkKind, string> = { ok: MARK_WORDS.ok, sum: MARK_WORDS.sum, d: derivedWords };
  const used = (["ok", "d", "sum"] as const).filter((k) => kinds.includes(k));
  if (!used.length && !extra?.length) return null;
  return (
    <p className="mk-legend">
      {used.map((k) => (
        <span key={k}>
          <Mark kind={k} />
          {words[k]}
        </span>
      ))}
      {extra?.map((e) => <span key={e}>{e}</span>)}
    </p>
  );
}
