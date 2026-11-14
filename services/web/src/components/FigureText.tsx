/** A table cell's figure: its text and, when it isn't from the company's latest period, the date it
 * is from. An old one (blanks.ts) is the not-reported mark with "since" its last date, dimmed: the
 * figure then is in the cell's hover, never shown as the current one. */
export function FigureText({ cell }: { cell: { text: string; asOf?: string | null; old?: boolean } }) {
  if (!cell.asOf) return <>{cell.text}</>;
  return (
    <span className={cell.old ? "fig old" : "fig"}>
      {cell.text}
      <span className="asof">{cell.old ? `since ${cell.asOf}` : cell.asOf}</span>
    </span>
  );
}
