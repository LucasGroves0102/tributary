import { TableScroll } from "../components/TableScroll";
import type { ReleaseGrid, ReleaseGridCell, ReleasePart } from "../types";
import { passageLines } from "./events";

/** A row's cells over all the grid's columns: the API's cells where they start, an empty cell in
 * each column no cell covers. */
export function filled(row: ReleaseGridCell[], columns: number): ReleaseGridCell[] {
  const out: ReleaseGridCell[] = [];
  const byCol = new Map(row.map((c) => [c.col, c]));
  for (let col = 0; col < columns; ) {
    const c = byCol.get(col);
    if (c) {
      out.push(c);
      col += Math.max(1, c.span);
    } else {
      out.push({ text: "", col, span: 1 });
      col += 1;
    }
  }
  return out;
}

const TOTAL = /^(?:total|adjusted|non-gaap adjusted|comparable)\b/i;

/** One of a release's own tables (an outlook, a reconciliation), as the Query API reads it from the
 * stored table (query_api/release_tables.py): head rows over the columns, the label column first,
 * figures right-aligned as the release prints them. Nothing is computed or re-read here: every cell
 * is the filing's text. Set as a Seal statement: serif tabular figures, a 2px ink rule under the
 * head, red rules between columns, a total or adjusted line in 650. */
export function ReleaseTable({ grid, label }: { grid: ReleaseGrid; label: string }) {
  return (
    <TableScroll label={label}>
      <table className="t ruled release-grid">
        {grid.head.length > 0 && (
          <thead>
            {grid.head.map((row, i) => (
              <tr key={i}>
                {filled(row, grid.columns).map((c) => (
                  <th key={c.col} scope="col" colSpan={c.span > 1 ? c.span : undefined} className={c.col === 0 ? "first" : "n"}>
                    {c.text}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
        )}
        <tbody>
          {grid.body.map((row, i) => {
            const cells = filled(row, grid.columns);
            const label0 = cells[0]?.col === 0 && cells[0].span === 1 ? cells[0].text : "";
            return (
              <tr key={i} className={TOTAL.test(label0) ? "total" : undefined}>
                {cells.map((c) => (
                  c.col === 0 && c.span === 1
                    ? <td key={c.col} className="first">{c.text}</td>
                    : <td key={c.col} colSpan={c.span > 1 ? c.span : undefined} className={c.span > 1 ? "span" : "n"}>{c.text}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

/** A quoted block's parts in order: its lines as the release wrote them (the block's heading first,
 * bullets as a list) and its tables. `skipHeading`: the heading is shown elsewhere (a reconciliation's
 * summary), so its line isn't repeated. */
export function ReleaseParts({ parts, heading, label, skipHeading = false }: {
  parts: ReleasePart[];
  heading: string | null;
  label: string;
  skipHeading?: boolean;
}) {
  return (
    <div className="release-parts">
      {parts.map((part, i) => {
        if ("table" in part) return <ReleaseTable key={i} grid={part.table} label={label} />;
        const lines = passageLines({ text: part.lines.join("\n"), heading: i === 0 ? heading : null })
          .filter((l) => !(skipHeading && l.kind === "heading"));
        if (!lines.length) return null;
        return (
          <div key={i} className="release-lines">
            {lines.map((l, j) => (
              <p key={j} className={`rl ${l.kind}`}>{l.kind === "bullet" || l.kind === "sub" ? <span aria-hidden="true">• </span> : null}{l.text}</p>
            ))}
          </div>
        );
      })}
    </div>
  );
}
