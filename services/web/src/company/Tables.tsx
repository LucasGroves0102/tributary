import { useEffect, useState, type ReactNode } from "react";

import { annualPerShare, ApiError, financialsView, segments as fetchSegments } from "../api";
import { NO_DATA, NO_DATA_TITLE, NOT_APPLICABLE, NOT_MEANINGFUL } from "../blanks";
import { download } from "../download";
import { change, day, pct } from "../format";
import { newestPeriodic } from "../thesis/useRevisit";
import type { FinancialRow, IndexedFiling } from "../types";
import {
  BANK_NOT_SHOWN,
  BANK_STATEMENTS,
  financialsCsv,
  type Fmt,
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
  withBridges,
  statementSection,
  figuresBehind,
  type FiguresBehind,
} from "./data";
import { go, href } from "../route";
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

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** Where a value can be read (#113): the value opens its filing in Tributary's reader, at the
 * financial statements when the form says where they are; "↗" beside it (shown on hover or
 * focus) is the filing on EDGAR. Only ids of the right shape make a reader link. */
export function cellLinks(cell: TableCell, companyId: string): { reader: string | null; edgar: string | null } {
  const s = cell.source;
  const reader = s && CIK.test(companyId) && ACCESSION.test(s.accessionNo)
    ? href({ view: "filing", companyId, accessionNo: s.accessionNo, section: statementSection(s.form) })
    : null;
  return { reader, edgar: cell.url?.startsWith("https://www.sec.gov/") ? cell.url : null };
}

function Cell({ cell, blank = NO_DATA_CELL, companyId }: { cell: TableCell | null; blank?: BlankCell; companyId: string }) {
  if (!cell) return <td className="n faint" title={blank.title}>{blank.text}</td>;
  if (cell.text === NOT_MEANINGFUL || cell.text.startsWith(NOT_APPLICABLE)) {
    return <td className="n faint" title={cell.title}>{cell.text}</td>;
  }
  const { reader, edgar } = cellLinks(cell, companyId);
  const form = cell.source?.form ?? "filing";
  return (
    <td className={cell.derived ? "n derived" : "n"} title={cell.title}>
      {reader ? (
        <a href={reader} aria-label={`${cell.text}: open the ${form} in Tributary's reader`}>{cell.text}</a>
      ) : edgar ? (
        <a href={edgar} target="_blank" rel="noopener noreferrer">{cell.text}</a>
      ) : (
        cell.text
      )}
      {/* the arrow is drawn by CSS, so a copied table and the cell's text stay the figure */}
      {reader && edgar && (
        <a className="cell-edgar" href={edgar} target="_blank" rel="noopener noreferrer"
          aria-label={`The ${form} on EDGAR, sec.gov`} title={`The ${form} on EDGAR, sec.gov`} />
      )}
    </td>
  );
}

/** A metric × period table; every cell's hover names its filing, the value opens it in the
 * reader and "↗" on EDGAR. Columns run oldest to newest, as statements read, so a wide one opens
 * at its newest end. */
export function MetricTableView({ table, first, companyId }: { table: MetricTable; first: string; companyId: string }) {
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
                <Cell key={table.columns[i]!.periodEnd} cell={c} blank={r.blanks[i]} companyId={companyId} />
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
  /** metrics that don't describe this company (ScreenerRow.not_applicable): they read n/a */
  notApplicable?: string[];
}

const BALANCE_NOT_TTM = "A balance is a point in time, so it has no trailing twelve months. Quarterly shows every quarter end.";

/** The figures stop before the newest 10-K/10-Q on EDGAR (H9): how far behind, in periods and days,
 * and where to read it meanwhile. No promise of when it catches up: the SEC's data keeps none. */
function BehindNotice({ behind, companyId, name, url }: { behind: FiguresBehind; companyId: string; name: string; url: string | null }) {
  const word = behind.form === "10-K" ? "year" : "quarter";
  return (
    <p className="card-b stale-note" role="note">
      ⚠ Not in these figures yet: {name}'s {behind.form} for the {word} ending {day(behind.period, true)}, filed{" "}
      {day(behind.filed, true)} ({behind.days === 1 ? "1 day" : `${behind.days} days`} ago). These figures end with the
      period ending {day(behind.through, true)}, {behind.quarters === 1 ? "a quarter" : `${behind.quarters} quarters`} behind
      it. Until they include it (Data notes says why when it's known), read the filing:{" "}
      <a href={href({ view: "filing", companyId, accessionNo: behind.accessionNo, section: statementSection(behind.form) })}>its financial statements</a>
      {url?.startsWith("https://www.sec.gov/") && <> · <a href={url} target="_blank" rel="noopener noreferrer">EDGAR ↗</a></>}.
    </p>
  );
}

/** Statement, period, basis and range live in the address (route.ts), so a view can be linked,
 * bookmarked and undone with Back. Range applies to Quarterly and TTM; Annual is ten years. */
export function Statements({ token, companyId, name, ticker, notApplicable = [], view = {} }: TableProps & { view?: StatementView }) {
  const kind: Statement = view.kind ?? "is";
  // the address keeps TTM for the statements that have one
  const period: Period = kind === "bs" && view.period === "ttm" ? "q" : view.period ?? "q";
  const basis: Basis = view.basis ?? "fiscal";
  const range: StatementRange = view.range ?? "8q";
  const set = (patch: StatementView) =>
    go({ view: "company", companyId, tab: "statements", statements: { ...view, ...patch } });
  const req = statementRequest(period, range);
  // a bank (035's rule) gets its own lines (F4); an older API refuses their names (422): the
  // standard statement then, as before
  const bank = notApplicable.length > 0;
  const annual = period === "a" && kind === "is";
  const load = useLoad(`${companyId}|${kind}|${period}|${basis}|${req.quarters}|${bank}`, async (): Promise<{ rows: FinancialRow[]; metrics: [string, string, Fmt][] }> => {
    const ask = (rows: [string, string, Fmt][]) => financialsView(token, companyId, req.quarters, rows.map(([m]) => m), req.view, basis);
    const [fin, perShare] = await Promise.all([
      bank
        ? ask(BANK_STATEMENTS[kind].rows).then((f) => ({ f, metrics: BANK_STATEMENTS[kind].rows }))
          .catch((e) => {
            if (e instanceof ApiError && e.status === 422) return ask(STATEMENTS[kind].rows).then((f) => ({ f, metrics: STATEMENTS[kind].rows }));
            throw e;
          })
        : ask(STATEMENTS[kind].rows).then((f) => ({ f, metrics: STATEMENTS[kind].rows })),
      // a company with no annual per-share facts, or an older API, still has its statement
      annual ? annualPerShare(token, companyId).catch(() => ({ rows: [] as FinancialRow[] })) : { rows: [] as FinancialRow[] },
    ]);
    return { rows: annual ? withAnnualPerShare(fin.f.rows, perShare.rows, basis) : fin.f.rows, metrics: fin.metrics };
  });
  const metrics = load?.state === "ok" ? load.data.metrics : (bank ? BANK_STATEMENTS : STATEMENTS)[kind].rows;
  const table = load?.state === "ok" ? withBridges(metricTable(load.data.rows, metrics, period, basis, notApplicable, RANGE_QUARTERS[range]), load.data.rows, kind) : null;
  // H9: a newer 10-K/10-Q in the index than the figures hold (public index, asked once per company)
  const [newest, setNewest] = useState<IndexedFiling | null>(null);
  useEffect(() => {
    let live = true;
    setNewest(null);
    void newestPeriodic(token, companyId).then((f) => live && setNewest(f));
    return () => {
      live = false;
    };
  }, [token, companyId]);
  const behind = load?.state === "ok" ? figuresBehind(load.data.rows, newest) : null;
  const exportCsv = () =>
    load?.state === "ok" && table &&
    download(`${ticker.toLowerCase()}-${kind}-${period}${period !== "a" && range !== "8q" ? `-${range}` : ""}${basis === "calendar" ? "-calendar" : ""}.csv`, new Blob([financialsCsv(load.data.rows, table, req.view)], { type: "text/csv" }));

  return (
    <section className="card">
      <div className="card-h wrap">
        <Seg label="Statement" value={kind} set={(k) => set({ kind: k })}
          options={(Object.keys(STATEMENTS) as Statement[]).map((k) => [k, STATEMENTS[k].label])} />
        <Seg label="Period" value={period} set={(p) => set({ period: p })} options={PERIODS}
          off={kind === "bs" ? { ttm: BALANCE_NOT_TTM } : undefined} />
        {period !== "a" && <Seg label="Range" value={range} set={(r) => set({ range: r })} options={RANGES} />}
        <Seg label="Basis" value={basis} set={(b) => set({ basis: b })} options={BASES} />
        <span className="sub">{basis === "calendar" ? "calendar quarters" : "fiscal periods"} · USD · hover a value for its filing</span>
        <span className="grow" />
        <button className="btn sm" onClick={exportCsv} disabled={!table?.rows.length}>Export CSV</button>
      </div>
      {behind && <BehindNotice behind={behind} companyId={companyId} name={name} url={newest?.filing_url ?? null} />}
      {bank && load?.state === "ok" && metrics !== STATEMENTS[kind].rows && (
        <p className="foot bank-note">{name} is a bank, so it is shown with a bank's own lines. {BANK_NOT_SHOWN}</p>
      )}
      <Gate load={load} name={name} what="Financials">
        {() =>
          table!.rows.length ? (
            <MetricTableView table={table!} first={STATEMENTS[kind].label} companyId={companyId} />
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
            : "Annual = the fiscal year: four quarters to each fiscal Q4, balances at the year end, EPS and shares as the latest 10-K to state them, so years before a stock split may not be adjusted for it. "
          : period === "ttm"
            ? "TTM = trailing twelve months to each quarter end. Per-share figures don't add across quarters: n/a. "
            : kind === "is"
              ? "EPS and diluted shares have no fiscal Q4: a 10-K states the year, and the year less nine months isn't the quarter when the share count changes. "
              : ""}
        Italic values are derived (e.g. Q4 = the 10-K's year minus nine months year-to-date),
        the rest are reported. A value opens its filing in the reader; ↗ beside it (on hover) is the filing on EDGAR. {NO_DATA} no data in this period, — not reported, $0 a real zero.
      </div>
    </section>
  );
}

export function Ratios({ token, companyId, name, ticker, notApplicable = [] }: TableProps) {
  // with readings, the API says where debt/equity and ROE read n/m on negative equity
  // (`not_meaningful_periods`, from the company's own equity periods), so those aren't gaps;
  // equity comes along so its dates are columns
  const load = useLoad(companyId, () => financialsView(token, companyId, 8, [...RATIOS.map(([m]) => m), "stockholders_equity"], "ttm", "fiscal", true));
  const table = load?.state === "ok"
    ? metricTable(load.data.rows, RATIOS, "ttm", "fiscal", notApplicable, undefined, load.data.not_meaningful_periods)
    : null;
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
        {() => (table!.rows.length ? <MetricTableView table={table!} first="Ratio" companyId={companyId} /> : <p className="empty">No ratios for these periods yet: they come from 10-Q and 10-K XBRL values. Statements shows what {name} reports.</p>)}
      </Gate>
    </section>
  );
}

function shareBlank(of: "consolidated" | "members" | "none"): string {
  return of === "none" ? "No share: consolidated revenue isn't known for this quarter, and these lines overlap." : NO_DATA_TITLE;
}

export function Segments({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const load = useLoad(companyId, async () => {
    const [seg, fin] = await Promise.all([
      fetchSegments(token, companyId, 8),
      // shares are of consolidated revenue; without it the table says what they're of instead
      financialsView(token, companyId, 8, ["revenue"], "quarter").catch(() => ({ rows: [] as FinancialRow[] })),
    ]);
    return { rows: seg.rows, revenue: fin.rows };
  });
  const tables = load?.state === "ok" ? segmentTables(load.data.rows, load.data.revenue) : [];
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
                {t.staleNote && <p className="card-b stale-note" role="note">⚠ {t.staleNote}</p>}
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
                        <tr key={r.member} className={r.until ? "retired" : undefined}>
                          <td className="first" title={r.until ? `${r.member}: last reported for ${r.until}` : r.member}>
                            {r.label}
                            {r.until && <span className="until">until {r.until}</span>}
                          </td>
                          {r.cells.map((c, i) => (
                            <Cell key={t.columns[i]!.periodEnd} cell={c} companyId={companyId} />
                          ))}
                          {t.metric === "revenue" ? (
                            r.shareTooBig ? (
                              <td className="n faint" title={`Not meaningful: ${r.label} is larger than the consolidated revenue reported for this quarter (${pct(r.share!)}), so the two aren't on the same basis.`}>{NOT_MEANINGFUL}</td>
                            ) : (
                              <td className={r.share === null ? "n faint" : "n"} title={r.share === null ? shareBlank(t.shareOf) : t.shareOf === "members" ? "Of these members' sum: consolidated revenue isn't known for this quarter." : "Of consolidated revenue for the latest quarter."}>
                                {r.share === null ? NO_DATA : pct(r.share)}
                              </td>
                            )
                          ) : <td aria-hidden="true" />}
                          {r.yoyNotMeaningful ? (
                            <td className="n faint" title="Not meaningful: the same quarter a year earlier was a loss or zero, so there's no growth rate.">{NOT_MEANINGFUL}</td>
                          ) : (
                            <td className={`n ${r.yoy === null ? "faint" : r.yoy >= 0 ? "pos" : "neg"}`}
                              title={r.yoy === null ? NO_DATA_TITLE : undefined}>
                              {r.yoy === null ? NO_DATA : change(r.yoy)}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
                {t.overlapNote && <p className="small muted card-b">{t.overlapNote}</p>}
                {t.retiredNote && <p className="small muted card-b">{t.retiredNote}</p>}
                {t.metric === "revenue" && t.shareOf === "none" && (
                  <p className="small muted card-b">No shares: consolidated revenue isn't known for this quarter, and these lines overlap, so their sum isn't a total either.</p>
                )}
              </section>
            ))}
            <div className="foot">
              Share: of consolidated revenue in the latest quarter, so lines that overlap each read
              their true share. YoY: latest quarter vs the quarter ending a year earlier. A member marked
              "until" stopped being reported (an old segmentation) and is listed after the current ones.
            </div>
          </>
        )
      }
    </Gate>
  );
}
