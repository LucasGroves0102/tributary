/** A table cell's figure: its text and, when it isn't from the company's latest period, the date it
 * is from; dimmed and marked when it's old (blanks.ts). The hover on the cell has the rest. */
export function FigureText({ cell }: { cell: { text: string; asOf?: string | null; old?: boolean } }) {
  if (!cell.asOf) return <>{cell.text}</>;
  return (
    <span className={cell.old ? "fig old" : "fig"}>
      {cell.text}
      <span className="asof">{cell.old ? `old · ${cell.asOf}` : cell.asOf}</span>
    </span>
  );
}
