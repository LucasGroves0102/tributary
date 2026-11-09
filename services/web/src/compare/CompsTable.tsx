import { useEffect, useMemo, useState } from "react";

import { failureReason, screener } from "../api";
import { NOT_APPLICABLE, NOT_REPORTED } from "../blanks";
import { FigureText } from "../components/FigureText";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { href } from "../route";
import type { Screener } from "../types";
import {
  COMPARE_COLUMNS,
  columnsOf,
  compareParams,
  compareRows,
  companiesOf,
  countText,
  isBank,
  MASKED_TITLE,
  medianTitle,
  peerStats,
  short,
  statText,
  type CompareState,
} from "./state";

type Load = { state: "loading" } | { state: "ok"; data: Screener } | { state: "error"; message: string };

/** A saved comps set as the Compare page's table, read-only (a thesis or report embed: wishlist
 * B.8, "keep pinned comps as a table"): every column the set saved, each company's latest
 * reported quarter, the peer median with n of N. Values are read now through /v1/screener as the
 * viewer: a company whose financials they can't read shows metadata only, as on Compare. */
export function CompsTable({ token, compare, label }: {
  token: string;
  compare: CompareState;
  /** company id → its name, for the caption when the table hasn't loaded */
  label: (companyId: string) => string;
}) {
  const params = useMemo(() => compareParams(compare), [compare]);
  const key = JSON.stringify(params);
  const ids = companiesOf(compare);
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!ids.length) return;
    let live = true;
    setLoad({ state: "loading" });
    screener(token, params)
      .then((data) => live && setLoad({ state: "ok", data }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
    // keyed by the parameters' JSON
  }, [token, key, attempt]);

  const bank = load.state === "ok" && isBank(load.data.rows.find((r) => r.company_id === compare.target));
  const cols = columnsOf(compare, bank).filter((k) => COMPARE_COLUMNS.some((c) => c.key === k));
  const rows = useMemo(() => (load.state === "ok" ? compareRows(compare, load.data.rows, bank) : []), [load, compare, bank]);
  const stats = useMemo(() => peerStats(rows, cols), [rows, cols]);

  if (!ids.length) return <p className="empty">This comps set has no companies.</p>;
  if (load.state === "loading") return <Skeleton lines={Math.min(ids.length, 6) + 1} label="Loading the comps" />;
  if (load.state === "error") {
    return <ErrorPanel what="the comps" detail={load.message} onRetry={() => setAttempt((n) => n + 1)} />;
  }
  return (
    <>
      <TableScroll label={`Comps: ${compare.target ? label(compare.target) : "companies"}`}>
        <table className="t comps embed-comps">
          <thead>
            <tr>
              <th className="first">Company</th>
              <th>Latest quarter</th>
              {cols.map((k) => {
                const c = COMPARE_COLUMNS.find((x) => x.key === k)!;
                return (
                  <th key={k} className="n" title={c.long}>
                    {c.label} <span className="unit">{c.period}</span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.companyId} className={[r.isTarget ? "sel" : "", r.readable ? "" : "masked"].join(" ").trim() || undefined}>
                <td className="first">
                  <a className="co" href={href({ view: "company", companyId: r.companyId })}>
                    <b>{r.name}</b>
                    {r.ticker && <span className="faint mono">{r.ticker}</span>}
                    {r.isTarget && <span className="tag q">target</span>}
                    {!r.readable && <span className="tag" title={MASKED_TITLE}>metadata only</span>}
                  </a>
                </td>
                <td className="muted">{r.latest ? short(r.latest.calendar) : "—"}</td>
                {cols.map((k) => {
                  const cell = r.cells[k]!;
                  return (
                    <td key={k} className={`n ${cell.value === null ? "faint" : ""}`} title={cell.title}>
                      <FigureText cell={cell} />
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr className="agg">
              <td className="first">Peer median</td>
              <td className="faint">n of N peers</td>
              {cols.map((k) => (
                <td key={k} className="n" title={medianTitle(stats[k]!)}>
                  {stats[k]!.n === 0 && stats[k]!.banks > 0 ? NOT_APPLICABLE : stats[k]!.n ? statText(k, stats[k]!.median) : NOT_REPORTED}
                  <span className="unit count">{countText(stats[k]!)}</span>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </TableScroll>
      <p className="faint small">
        USD · each company's latest reported quarter (TTM to it; balances at its end) · median excludes the target · hover a value for its period and filing
      </p>
    </>
  );
}
