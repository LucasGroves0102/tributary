import { paren } from "../format";

/** A table cell's figure: its text and, when it isn't from the company's latest period, the date it
 * is from. An old one (blanks.ts) is the not-reported mark with "since" its last date, dimmed: the
 * figure then is in the cell's hover, never shown as the current one. A negative amount reads in
 * parentheses (`paren`), as in the statements and the screener: one negative-number format on every
 * table (Riley 95: "-$66.00B" on Home, Peers and Compare beside "($29.03B)" in the screener); a
 * percentage keeps its minus. */
export function FigureText({ cell }: { cell: { text: string; asOf?: string | null; old?: boolean } }) {
  if (!cell.asOf) return <>{paren(cell.text)}</>;
  return (
    <span className={cell.old ? "fig old" : "fig"}>
      {paren(cell.text)}
      {/* "none since", not a bare "since" under the dash (Riley, test 10, finding 59: "— since Mar '24" read as cryptic) */}
      <span className="asof">{cell.old ? `none since ${cell.asOf}` : cell.asOf}</span>
    </span>
  );
}
