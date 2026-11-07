import type { ReactNode } from "react";

import { financialsView, segments as fetchSegments } from "../api";
import { download } from "../download";
import { BANK_NA, bankTitle, change, pct } from "../format";
import {
  financialsCsv,
  metricTable,
  RANGE_QUARTERS,
  RATIOS,
  segmentTables,
  statementRequest,
  STATEMENTS,
  type Basis,
  type MetricTable,
  type Period,
  type BlankCell,
  type Statement,
  type StatementRange,
  type TableCell,
  withAnnualPerShare,
} from "./data";
import { go } from "../route";
import type { StatementView } from "./statementView";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
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

const RANGES: [StatementRange, string][] = [
  ["8q", "8 quarters"],
  ["5y", "5y"],
  ["10y", "10y"],
  ["max", "Max"],
];

function Seg<T extends string>({ value, options, set, label, off }: {
  value: T;
  options: [T, string][];
  set: (v: T) => void;
  label: string;
  /** an option that doesn't apply here, and why (its hover) */
  off?: Partial<Record<T, string>>;
}) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} className={v === value ? "on" : undefined} aria-pressed={v === value} onClick={() => set(v)}
          disabled={!!off?.[v]} title={off?.[v]}>
          {l}
        </button>
      ))}
    </span>
  );
}

const NO_DATA_CELL: BlankCell = { text: NO_DATA, title: NO_DATA_TITLE };

function Cell({ cell, blank = NO_DATA_CELL }: { cell: TableCell | null; blank?: BlankCell }) {
  if (!cell) return <td className="n faint" title={blank.title}>{blank.text}</td>;
  if (cell.text === NOT_MEANINGFUL || cell.text.startsWith(NOT_APPLICABLE)) {
    return <td className="n faint" title={cell.title}>{cell.text}</td>;
  }
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

/** A metric × period table; every cell's hover names its filing and links to it. Columns run
 * oldest to newest, as statements read, so a wide one opens at its newest end. */
export function MetricTableView({ table, first }: { table: MetricTable; first: string }) {
  return (
    <TableScroll atEnd resetKey={table.columns.length} label={first}>
      <table className="t statement">
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
                <Cell key={table.columns[i]!.periodEnd} cell={c} blank={r.blanks[i]} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function Gate({ load, name, what, children }: { load: Load<unknown> | null; name: string; what: string; children: () => ReactNode }) {
  if (!load || load.state === "loading") return <Skeleton lines={5} label={`Loading ${what.toLowerCase()}`} />;
  if (load.state === "denied") return <p className="empty locked">{what} for {name} aren't included in your access.</p>;
  if (load.state === "error") return <ErrorPanel what={what.toLowerCase()} detail={load.message} onRetry={load.retry} />;
  return <>{children()}</>;
}

interface TableProps {
  token: string;
  companyId: string;
  name: string;
  ticker: string;
  /** SIC description: a bank's operating margin / FCF / net debt rows read n/a */
  industry?: string | null;
}

/** Statement, period, basis and range live in the address (route.ts), so a view can be linked,
 * bookmarked and undone with Back. Range applies to Quarterly and TTM; Annual is ten years. */
export function Statements({ token, companyId, name, ticker, industry = null, view = {} }: TableProps & { view?: StatementView }) {
  const kind: Statement = view.kind ?? "is";
  const period: Period = view.period ?? "q";
  const basis: Basis = view.basis ?? "fiscal";
  const range: StatementRange = view.range ?? "8q";
  const set = (patch: StatementView) =>
    go({ view: "company", companyId, tab: "statements", statements: { ...view, ...patch } });
  const req = statementRequest(period, range);
  const metrics = STATEMENTS[kind].rows;
  const load = useLoad(`${companyId}|${kind}|${period}|${basis}|${req.quarters}`, () =>
    financialsView(token, companyId, req.quarters, metrics.map(([m]) => m), req.view, basis),
  );
  const table = load?.state === "ok" ? metricTable(load.data.rows, metrics, period, basis, industry, RANGE_QUARTERS[range]) : null;
  const exportCsv = () =>
    load?.state === "ok" && table &&
    download(`${ticker.toLowerCase()}-${kind}-${period}${period !== "a" && range !== "8q" ? `-${range}` : ""}${basis === "calendar" ? "-calendar" : ""}.csv`, new Blob([financialsCsv(load.data.rows, table, req.view)], { type: "text/csv" }));

  return (
    <section className="card">
      <div className="card-h wrap">
        <Seg label="Statement" value={kind} set={(k) => set({ kind: k })}
          options={(Object.keys(STATEMENTS) as Statement[]).map((k) => [k, STATEMENTS[k].label])} />
        <Seg label="Period" value={period} set={(p) => set({ period: p })} options={PERIODS} />
        {period !== "a" && <Seg label="Range" value={range} set={(r) => set({ range: r })} options={RANGES} />}
        <Seg label="Basis" value={basis} set={(b) => set({ basis: b })} options={BASES} />
        <span className="sub">{basis === "calendar" ? "calendar quarters" : "fiscal periods"} · USD · hover a value for its filing</span>
        <span className="grow" />
        <button className="btn sm" onClick={exportCsv} disabled={!table?.rows.length}>Export CSV</button>
      </div>
      <Gate load={load} name={name} what="Financials">
        {() =>
          table!.rows.length ? (
            <MetricTableView table={table!} first={STATEMENTS[kind].label} />
          ) : (
            <p className="empty">
              No {STATEMENTS[kind].label.toLowerCase()} values for these periods. Try{" "}
              {period === "a" ? "Quarterly" : "Annual"} or another statement.
            </p>
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

export function Ratios({ token, companyId, name, ticker, notApplicable = [] }: TableProps) {
  // equity too: debt/equity and ROE on negative equity read n/m, not as a gap
  const load = useLoad(companyId, () => financialsView(token, companyId, 8, [...RATIOS.map(([m]) => m), "stockholders_equity"], "ttm", "fiscal", true));
  const table = load?.state === "ok" ? metricTable(load.data.rows, RATIOS, "ttm", "fiscal", notApplicable) : null;
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
        {() => (table!.rows.length ? <MetricTableView table={table!} first="Ratio" /> : <p className="empty">No ratios for these periods yet: they come from 10-Q and 10-K XBRL values. Statements shows what {name} reports.</p>)}
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
                <TableScroll atEnd resetKey={t.columns.length} label={t.title}>
                  <table className="t aligned">
                    <thead>
                      <tr>
                        <th className="first">Member</th>
                        {t.columns.map((c) => (
                          <th key={c.periodEnd} className="n">{c.label}</th>
                        ))}
                        {/* only revenue has a share; the blank column keeps the periods lined up across tables */}
                        {t.metric === "revenue" ? <th className="n">Share</th> : <th aria-hidden="true" />}
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
                          {t.metric === "revenue" ? <td className="n">{r.share === null ? "—" : pct(r.share)}</td> : <td aria-hidden="true" />}
                          <td className={`n ${r.yoy === null ? "faint" : r.yoy >= 0 ? "pos" : "neg"}`}>
                            {r.yoy === null ? "—" : change(r.yoy)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
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
