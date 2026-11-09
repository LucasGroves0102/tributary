import { TableScroll } from "../components/TableScroll";
import type { Recap } from "../types";
import { recapRows } from "./data";

/** A 10-Q/10-K's quarter against the prior and the year-ago quarter (the recap route): the
 * Overview's "Latest quarter" and the Earnings view's reported figures. n/m readings come from the
 * API (migration 035, `recapRows`); each row's hover names its filings. `yearAgo`: the quarter and
 * the same quarter a year earlier only (the Earnings view, beside the release, at half width). */
export function RecapTable({ data, only, label, yearAgo = false }: { data: Recap; only?: readonly string[]; label: string; yearAgo?: boolean }) {
  const table = recapRows(data, only);
  return (
    <TableScroll label={label}>
      <table className="t">
        <thead>
          <tr>
            <th className="first">Metric</th>
            <th className="n">{table.labels[0]}</th>
            {!yearAgo && <th className="n">{table.labels[1]}</th>}
            {!yearAgo && <th className="n">QoQ</th>}
            <th className="n">{table.labels[2]}</th>
            <th className="n">YoY</th>
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.label} title={r.title}>
              <td className="first">{r.label}</td>
              <td className="n"><b>{r.current}</b></td>
              {!yearAgo && <td className="n">{r.prior}</td>}
              {!yearAgo && <td className={`n ${!r.qoq ? "faint" : r.qoq.sign >= 0 ? "pos" : "neg"}`}>{r.qoq?.text ?? "—"}</td>}
              <td className="n">{r.yearAgo}</td>
              <td className={`n ${!r.yoy ? "faint" : r.yoy.sign >= 0 ? "pos" : "neg"}`}>{r.yoy?.text ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
