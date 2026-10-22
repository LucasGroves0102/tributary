import { useState, type ReactNode } from "react";

import { financialsView, segments as fetchSegments } from "../api";
import { download } from "../download";
import { change, pct } from "../format";
import {
  financialsCsv,
  metricTable,
  RATIOS,
  segmentTables,
  statementRequest,
  STATEMENTS,
  type Basis,
  type MetricTable,
  type Period,
  type Statement,
  type TableCell,
} from "./data";
import { useLoad, type Load } from "./useLoad";

const PERIODS: [Period, string][] = [
  ["q", "Quarterly"],
  ["a", "Annual"],
  ["ttm", "TTM"],
];

const BASES: [Basis, string][] = [
  ["fiscal", "Fiscal"],
  ["calendar", "Calendar"],
];

function Seg<T extends string>({ value, options, set, label }: { value: T; options: [T, string][]; set: (v: T) => void; label: string }) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} className={v === value ? "on" : undefined} aria-pressed={v === value} onClick={() => set(v)}>
          {l}
        </button>
      ))}
    </span>
  );
}

function Cell({ cell }: { cell: TableCell | null }) {
  if (!cell) return <td className="n faint">—</td>;
  return (
    <td className={cell.derived ? "n derived" : "n"} title={cell.title}>
      {cell.url ? (
        <a href={cell.url} target="_blank" rel="noopener noreferrer">
          {cell.text}
        </a>
      ) : (
        cell.text
      )}
    </td>
  );
}

/** A metric × period table; every cell's hover names its filing and links to it. */
export function MetricTableView({ table, first }: { table: MetricTable; first: string }) {
  return (
    <div className="tw">
      <table className="t">
        <thead>
          <tr>
            <th className="first">{first}</th>
            {table.columns.map((c) => (
              <th key={c.periodEnd} className="n">
                {c.label}
                <div className="unit">{c.sub}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.metric}>
              <td className="first">{r.label}</td>
              {r.cells.map((c, i) => (
                <Cell key={table.columns[i]!.periodEnd} cell={c} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Gate({ load, name, what, children }: { load: Load<unknown> | null; name: string; what: string; children: () => ReactNode }) {
  if (!load || load.state === "loading") return <p className="empty">Loading…</p>;
  if (load.state === "denied") return <p className="empty locked">{what} for {name} aren't included in your access.</p>;
  if (load.state === "error") return <p className="empty error">Couldn't load {what.toLowerCase()} ({load.message}).</p>;
  return <>{children()}</>;
}

export function Statements({ token, companyId, name, ticker }: { token: string; companyId: string; name: string; ticker: string }) {
  const [kind, setKind] = useState<Statement>("is");
  const [period, setPeriod] = useState<Period>("q");
  const [basis, setBasis] = useState<Basis>("fiscal");
  const req = statementRequest(period);
  const metrics = STATEMENTS[kind].rows;
  const load = useLoad(`${companyId}|${kind}|${period}|${basis}`, () =>
    financialsView(token, companyId, req.quarters, metrics.map(([m]) => m), req.view, basis),
  );
  const table = load?.state === "ok" ? metricTable(load.data.rows, metrics, period, basis) : null;
  const exportCsv = () =>
    load?.state === "ok" && table &&
    download(`${ticker.toLowerCase()}-${kind}-${period}${basis === "calendar" ? "-calendar" : ""}.csv`, new Blob([financialsCsv(load.data.rows, table, req.view)], { type: "text/csv" }));

  return (
    <section className="card">
      <div className="card-h wrap">
        <Seg label="Statement" value={kind} set={setKind}
          options={(Object.keys(STATEMENTS) as Statement[]).map((k) => [k, STATEMENTS[k].label])} />
        <Seg label="Period" value={period} set={setPeriod} options={PERIODS} />
        <Seg label="Basis" value={basis} set={setBasis} options={BASES} />
        <span className="sub">{basis === "calendar" ? "calendar quarters" : "fiscal periods"} · USD · hover a value for its filing</span>
        <span className="grow" />
        <button className="btn sm" onClick={exportCsv} disabled={!table?.rows.length}>Export CSV</button>
      </div>
      <Gate load={load} name={name} what="Financials">
        {() =>
          table!.rows.length ? (
            <MetricTableView table={table!} first={STATEMENTS[kind].label} />
          ) : (
            <p className="empty">No {STATEMENTS[kind].label.toLowerCase()} values for these periods.</p>
          )
        }
      </Gate>
      <div className="foot">
        {period === "a"
          ? basis === "calendar"
            ? "Annual = the calendar year: the four quarters ending nearest each calendar Q4, balances at its end. "
            : "Annual = the fiscal year: four quarters to each fiscal Q4, balances at the year end. "
          : period === "ttm"
            ? "TTM = trailing twelve months to each quarter end; balances at the quarter end. "
            : ""}
        Italic values are derived (e.g. Q4 = the 10-K's year minus nine months year-to-date),
        the rest are reported. Every value links to its filing.
      </div>
    </section>
  );
}

export function Ratios({ token, companyId, name, ticker }: { token: string; companyId: string; name: string; ticker: string }) {
  const load = useLoad(companyId, () => financialsView(token, companyId, 8, RATIOS.map(([m]) => m), "ttm"));
  const table = load?.state === "ok" ? metricTable(load.data.rows, RATIOS, "ttm") : null;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Ratios</h2>
        <span className="sub">
          TTM to each quarter end · balance sheet at the quarter end · no price-based multiples
          (Tributary has no market prices)
        </span>
        <span className="grow" />
        <button className="btn sm" disabled={!table?.rows.length}
          onClick={() => load?.state === "ok" && table &&
            download(`${ticker.toLowerCase()}-ratios.csv`, new Blob([financialsCsv(load.data.rows, table, "ttm")], { type: "text/csv" }))}>
          Export CSV
        </button>
      </div>
      <Gate load={load} name={name} what="Financials">
        {() => (table!.rows.length ? <MetricTableView table={table!} first="Ratio" /> : <p className="empty">No ratios for these periods.</p>)}
      </Gate>
    </section>
  );
}

export function Segments({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const load = useLoad(companyId, () => fetchSegments(token, companyId, 8));
  const tables = load?.state === "ok" ? segmentTables(load.data.rows) : [];
  return (
    <Gate load={load} name={name} what="Financials">
      {() =>
        tables.length === 0 ? (
          <section className="card">
            <div className="card-h"><h2>Segments</h2></div>
            <p className="empty">
              No segment data for {name} yet. Segments come from the XBRL dimensions in each 10-Q
              and 10-K, and the segments job hasn't covered every company; companies with a single
              reportable segment have none.
            </p>
          </section>
        ) : (
          <>
            {tables.map((t) => (
              <section key={`${t.axis}|${t.metric}`} className="card">
                <div className="card-h">
                  <h2>{t.title}</h2>
                  <span className="sub">quarterly · USD · as reported in the filings' XBRL</span>
                </div>
                <div className="tw">
                  <table className="t">
                    <thead>
                      <tr>
                        <th className="first">Member</th>
                        {t.columns.map((c) => (
                          <th key={c.periodEnd} className="n">{c.label}</th>
                        ))}
                        {t.metric === "revenue" && <th className="n">Share</th>}
                        <th className="n">YoY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.rows.map((r) => (
                        <tr key={r.member}>
                          <td className="first" title={r.member}>{r.label}</td>
                          {r.cells.map((c, i) => (
                            <Cell key={t.columns[i]!.periodEnd} cell={c} />
                          ))}
                          {t.metric === "revenue" && <td className="n">{r.share === null ? "—" : pct(r.share)}</td>}
                          <td className={`n ${r.yoy === null ? "faint" : r.yoy >= 0 ? "pos" : "neg"}`}>
                            {r.yoy === null ? "—" : change(r.yoy)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
            <div className="foot">
              Share: of the latest quarter's total across members. YoY: latest quarter vs four
              quarters earlier. Members overlap when a company reports several breakdowns.
            </div>
          </>
        )
      }
    </Gate>
  );
}
