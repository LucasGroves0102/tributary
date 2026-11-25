import { useEffect, useState, type ReactNode } from "react";

import { annualPerShare, ApiError, filingIndex, financialsView, perShare as fetchPerShare, segments as fetchSegments } from "../api";
import { NO_DATA, NO_DATA_TITLE, NOT_APPLICABLE, NOT_MEANINGFUL } from "../blanks";
import { download } from "../download";
import { change, day, exactAmount, moneyRow, paren, pct } from "../format";
import { newestPeriodic } from "../thesis/useRevisit";
import type { Callout, FinancialRow, IndexedFiling } from "../types";
import { calloutLines, calloutText } from "./callouts";
import { ComponentsPanel } from "./Components";
import { INPUT_ROLES, inputLabel, partName } from "./components";
import {
  BANK_NOT_SHOWN,
  BANK_STATEMENTS,
  F5_METRICS,
  financeArmNote,
  financialsCsv,
  type Line,
  metricTable,
  RANGE_QUARTERS,
  BANK_RATIOS,
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
  withPerShare,
  withBridges,
  statementSection,
  statementPlace,
  figuresBehind,
  type FiguresBehind,
} from "./data";
import { go, href } from "../route";
import type { StatementView } from "./statementView";
import { Choices } from "../components/Choices";
import { MenuButton } from "../components/MenuButton";
import { Folio } from "../components/Folio";
import { onGridKey } from "../components/ledgerGrid";
import { useSourceCard, type SourceInfo } from "../components/SourceCard";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { useLoad, type Load } from "./useLoad";
import { ResultsPending } from "./ResultsPending";

const PERIODS: [Period, string][] = [
  ["q", "Quarterly"],
  ["a", "Annual"],
  ["ttm", "TTM"],
];

const BASES: [Basis, string][] = [
  ["fiscal", "Fiscal"],
  ["calendar", "Calendar"],
];
const SHARE_BASES: [string, string][] = [["adjusted", "Split-adjusted"], ["filed", "As filed"]];

const RANGE_WORDS: Record<StatementRange, string> = { "8q": "8 quarters", "5y": "5 years", "10y": "10 years", max: "All periods" };

const RANGES: [StatementRange, string][] = [
  ["8q", "8 quarters"],
  ["5y", "5y"],
  ["10y", "10y"],
  ["max", "Max"],
];

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

/** A table figure's mark (§5.1): ✓ when its filing prints it as shown; d when derived (a fiscal Q4, a
 * `derived:` tag, a per-share figure split-adjusted from the filing's own). From the API's facts only. */
export function cellMark(cell: TableCell): "ok" | "d" {
  return cell.derived || cell.adjusted || !!cell.source?.tag?.startsWith("derived:") ? "d" : "ok";
}

/** What a table figure's card says: the figure as Tributary holds it, its mark, its filing and where in
 * it; a callout's sentence or a restatement as its note; the filing in the reader and on EDGAR. */
function cellCard(cell: TableCell, row: string, column: { label: string; sub: string }, companyId: string, note?: string, currency = "USD"): SourceInfo {
  const { reader, edgar } = cellLinks(cell, companyId);
  const mark = cellMark(cell);
  const n = Number(cell.raw);
  const exact = cell.raw !== "" && Number.isFinite(n) && /^-?([$€£¥₹₩]|CN¥|[A-Z]{3} )[\d,.]+[TBMK]?$/.test(cell.text.replace(/^\(|\)$/g, ""));
  return {
    title: `${row} · ${column.label}, to ${column.sub.replace(/^to /, "")}`,
    value: exact ? exactAmount(n, currency) : paren(cell.text),
    mark,
    ...(mark === "d" ? { how: cell.adjusted ? "Derived: the filing's own figure, split-adjusted to today's share basis. Shown in italic."
      : "Derived: worked out from the filings, not printed in one as shown (a fiscal Q4 is the 10-K's year less its three 10-Qs). Shown in italic." } : {}),
    where: cell.source ? [`${cell.source.form}${cell.source.filed ? ` filed ${day(cell.source.filed, true)}` : ""}`, statementPlace(cell.source.form)] : [],
    note: [note, cell.restated].filter(Boolean).join(" ") || undefined,
    reader: reader ? { href: reader, label: `Open the ${cell.source?.form ?? "filing"} at its statements` } : null,
    edgar,
  };
}

function Cell({ cell, blank = NO_DATA_CELL, companyId, note, row = "", column = { label: "", sub: "" }, tab = -1, currency }: {
  cell: TableCell | null;
  blank?: BlankCell;
  companyId: string;
  note?: string;
  /** the row's and column's names, for the card's title */
  row?: string;
  column?: { label: string; sub: string };
  /** the table's one tab stop (components/ledgerGrid) */
  tab?: 0 | -1;
  currency?: string;
}) {
  const live = !!cell && cell.text !== NOT_MEANINGFUL && !cell.text.startsWith(NOT_APPLICABLE);
  const { props, card } = useSourceCard(() => (live ? cellCard(cell!, row, column, companyId, note, currency) : null), { hover: false });
  if (!cell) return <td className="n faint" title={blank.title}>{blank.text}</td>;
  if (!live) {
    return <td className="n faint" title={cell.title}>{cell.text}</td>;
  }
  const mark = cellMark(cell);
  const classes = ["n", mark === "d" && "derived", cell.callout && "called", cell.restated && "restated", cell.tone && `tone-${cell.tone}`].filter(Boolean).join(" ");
  // a negative amount in parentheses, as a filing prints it; the card and the CSV keep the sign. The cell
  // is the figure's target (§4): it opens its card; the reader and EDGAR are in the card.
  return (
    <td className={classes} data-cell="" tabIndex={tab} {...props}>
      {/* a callout's mark: the sentence is in the card and listed under the table */}
      {cell.callout && <span className="callout-mark" aria-hidden="true" />}
      {/* restated since first reported: the first figure, its filing and day are in the card */}
      {cell.restated && <span className="restated-mark" aria-hidden="true" />}
      {paren(cell.text)}
      {card}
    </td>
  );
}

type TableRow = MetricTable["rows"][number];

/** The lines an opened derived row reads across its columns: its cells' `inputs` (F5), in the order
 * and with the signs of INPUT_ROLES, then any other key. */
function inputLines(row: TableRow): [string, string | undefined][] {
  const keys = new Set<string>();
  for (const c of [...row.cells].reverse()) for (const k of Object.keys(c?.inputs ?? {})) if (!k.endsWith("_year_ago")) keys.add(k);
  const order = INPUT_ROLES[row.metric] ?? [];
  const known = order.filter(([k]) => keys.has(k));
  return [...known, ...[...keys].filter((k) => !order.some(([m]) => m === k)).sort().map((k): [string, undefined] => [k, undefined])];
}

/** A column's source line (§5.1: tables don't put marks in cells; each column names its source): the
 * filing its figures share, "10-Q Aug 26, 2026", or its form when they differ; d when they are derived. */
function columnSource(table: MetricTable, i: number): { text: string; derived: boolean } | null {
  const cells = table.rows.map((r) => r.cells[i]).filter((c): c is TableCell => !!c?.source);
  if (!cells.length) return null;
  const accessions = new Set(cells.map((c) => c.source!.accessionNo));
  const forms = [...new Set(cells.map((c) => c.source!.form))];
  // derived when the column's revenue (its first line) is: a fiscal Q4 from the 10-K
  const derived = cellMark(table.rows[0]?.cells[i] ?? cells[0]!) === "d";
  const first = cells[0]!.source!;
  const text = accessions.size === 1 ? `${first.form}${first.filed ? ` ${day(first.filed, true)}` : ""}` : forms.join(", ");
  return { text, derived };
}

/** A metric × period ledger (§4): the unit's statement in the first header cell, each column's label,
 * period end and source; a 1px ink rule under the header, no vertical rules; totals in weight under an
 * ink rule, the bottom line double-ruled; derived figures italic. Every figure opens its source card:
 * one tab stop, arrows inside, Enter or Space opens a card with focus in it, Escape returns
 * (components/ledgerGrid). Columns run oldest to newest, as statements read, so a wide one opens at its
 * newest end. A standardised row (F5: derived, computed or a sum of tags) opens into its lines. A quarter
 * the API calls out carries a mark, its sentence in its card and in the list under the table. */
export function MetricTableView({ table, first, companyId, open = null, onOpen }: {
  table: MetricTable;
  first: string;
  companyId: string;
  /** the opened row's metric */
  open?: string | null;
  onOpen?: (metric: string | null) => void;
}) {
  const notes = new Map<string, string>();
  for (const r of table.rows) {
    r.cells.forEach((c, i) => {
      if (c?.callout) notes.set(`${r.metric}|${i}`, calloutText(c.callout, r.label, table.columns[i]!.label, r.opts?.outflow ? -1 : 1));
    });
  }
  // the one tab stop: the first row's newest figure (the table opens at its newest end)
  const stopRow = table.rows.findIndex((r) => r.cells.some(Boolean));
  const stopCol = stopRow >= 0 ? table.rows[stopRow]!.cells.map((c, i) => (c ? i : -1)).filter((i) => i >= 0).at(-1)! : -1;
  return (
    <TableScroll atEnd resetKey={table.columns.length} label={first}>
      <table className="t led statement" onKeyDown={onGridKey}>
        <thead>
          <tr>
            <th className="first">{first}<span className="pe">{table.currency ?? "USD"}</span></th>
            {table.columns.map((c, i) => {
              const src = columnSource(table, i);
              return (
                <th key={c.periodEnd} className={src?.derived ? "n derived" : "n"}>
                  {c.label}
                  <span className="pe unit">{c.sub}</span>
                  {src && <span className="src">{src.text}{src.derived && <i> d</i>}</span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r, ri) => {
            const opens = !!onOpen && r.cells.some((c) => c?.opens);
            const isOpen = opens && open === r.metric;
            const lines = isOpen ? inputLines(r) : [];
            return [
              <tr key={r.metric} className={[r.opts?.sub && "sub", r.opts?.total && "total", r.opts?.grand && "grand", isOpen && "opened"].filter(Boolean).join(" ") || undefined}>
                <td className="first" title={r.opts?.define}>
                  {opens ? (
                    <button className="link open-row" aria-expanded={isOpen} onClick={() => onOpen!(isOpen ? null : r.metric)}
                      title={isOpen ? "Close" : `Open ${r.label}: what it is made of, with the tags and filings behind it`}>
                      {/* the caret is CSS: the cell's text stays the label (copied tables, tests) */}
                      <span className="caret" aria-hidden="true" />{r.label}
                    </button>
                  ) : r.opts?.define ? <span className="defined">{r.label}</span> : r.label}
                </td>
                {r.cells.map((c, i) => (
                  <Cell key={table.columns[i]!.periodEnd} cell={c} blank={r.blanks[i]} companyId={companyId} note={notes.get(`${r.metric}|${i}`)}
                    row={r.label} column={table.columns[i]!} tab={ri === stopRow && i === stopCol ? 0 : -1} currency={table.currency} />
                ))}
              </tr>,
              ...lines.map(([k, role]) => {
                const values = r.cells.map((c) => (c?.inputs && k in c.inputs ? c.inputs[k]! : null));
                // one unit across the line, as every statement row
                const texts = moneyRow(values);
                return (
                  <tr key={`${r.metric}|${k}`} className="input-line">
                    <td className="first">{inputLabel(k, role)}</td>
                    {texts.map((t, i) => (
                      <td key={table.columns[i]!.periodEnd} className={t === null ? "n faint" : "n"}
                        title={t === null ? `${partName(k)}: not in this period's figure` : `${partName(k)}, ${table.columns[i]!.label}: ${t}, as ${r.label.toLowerCase()} read it`}>
                        {t === null ? NO_DATA : paren(t)}
                      </td>
                    ))}
                  </tr>
                );
              }),
            ];
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

/** "Worth a look": the table's callouts in words, each with its filing (F5, #106, #112). */
export function CalloutList({ table, companyId }: { table: MetricTable; companyId: string }) {
  const lines = calloutLines(table);
  if (!lines.length) return null;
  return (
    <div className="callouts card-b" role="note" aria-label="Worth a look">
      <b className="small">Worth a look</b>
      <ul>
        {lines.map((l) => (
          <li key={l.key}>
            <span className="callout-mark" aria-hidden="true" /> {l.text}{" "}
            {l.cells.map(({ label, cell }) => {
              const { reader, edgar } = cellLinks(cell, companyId);
              const link = reader ?? edgar;
              return link ? (
                <a key={label} className="callout-link" href={link} {...(reader ? {} : { target: "_blank", rel: "noopener noreferrer" })}>
                  {l.cells.length > 1 ? label : `read the ${cell.source?.form ?? "filing"}`}
                </a>
              ) : null;
            })}
          </li>
        ))}
      </ul>
      <p className="small muted">Figures are as filed. A mark means a quarter far from the ones beside it, or of the other sign: something to read, not an error.</p>
    </div>
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
const ANNUAL_RANGE = "Annual shows the last ten years; the range is for Quarterly and TTM.";

/** The figures stop before the newest 10-K/10-Q on EDGAR (H9): how far behind, in periods and days,
 * and where to read it meanwhile. No promise of when it catches up: the SEC's data keeps none. */
function BehindNotice({ behind, companyId, name, url }: { behind: FiguresBehind; companyId: string; name: string; url: string | null }) {
  const word = /^(10-K|20-F|40-F)$/.test(behind.form) ? "year" : "quarter";
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

/** What an empty cell reads (blanks.ts): each mark with its meaning, as a list, not a dotted string. */
export function BlanksLegend() {
  return (
    <ul className="blanks-legend" aria-label="What an empty cell reads">
      <li><span className="blank-mark">{NO_DATA}</span> no data in this period</li>
      <li><span className="blank-mark">—</span> not reported</li>
      <li><span className="blank-mark">$0</span> a real zero</li>
    </ul>
  );
}

/** Statement, period, basis and range live in the address (route.ts), so a view can be linked,
 * bookmarked and undone with Back. Range applies to Quarterly and TTM; Annual is ten years. */
export function Statements({ token, companyId, name, ticker, notApplicable = [], view = {}, asOf, canRead = false, fiscalYearEnd = null }: TableProps & {
  view?: StatementView;
  asOf?: string;
  /** the user reads the company's filings: a quarter's results out before its report are said (ResultsPending) */
  canRead?: boolean;
  fiscalYearEnd?: string | null;
}) {
  const kind: Statement = view.kind ?? "is";
  // the address keeps TTM for the statements that have one
  const period: Period = kind === "bs" && view.period === "ttm" ? "q" : view.period ?? "q";
  const basis: Basis = view.basis ?? "fiscal";
  const range: StatementRange = view.range ?? "8q";
  const set = (patch: StatementView) =>
    go({ view: "company", companyId, tab: "statements", statements: { ...view, ...patch }, ...(asOf ? { asOf } : {}) });
  const req = statementRequest(period, range, kind);
  // a bank (035's rule) gets its own lines (F4); an older API refuses their names (422): the
  // standard statement then, as before
  const bank = notApplicable.length > 0;
  const annual = period === "a" && kind === "is";
  const asFiled = view.shares === "filed";
  const load = useLoad(`${companyId}|${kind}|${period}|${basis}|${req.quarters}|${bank}|${asFiled}|${asOf ?? ""}`, async (): Promise<{ rows: FinancialRow[]; metrics: Line[]; callouts: Callout[]; annualOnly: boolean; perShare: boolean; history: boolean | null }> => {
    // each figure a later filing changed comes with its first version (`original`): its cell is marked
    const once = (rows: Line[]) => financialsView(token, companyId, req.quarters, rows.map(([m]) => m), req.view, basis, false, period === "q", asOf, true)
      .then((f) => ({ f, metrics: rows }));
    // an API older than migration 049 refuses its metrics (422): the statement without them
    const ask = (rows: Line[]) => once(rows).catch((e) => {
      const older = rows.filter(([m]) => !F5_METRICS.has(m));
      if (e instanceof ApiError && e.status === 422 && older.length < rows.length) return once(older);
      throw e;
    });
    const [fin, perShare] = await Promise.all([
      bank
        ? ask(BANK_STATEMENTS[kind].rows)
          .catch((e) => {
            if (e instanceof ApiError && e.status === 422) return ask(STATEMENTS[kind].rows);
            throw e;
          })
        : ask(STATEMENTS[kind].rows),
      // EPS and shares on one share basis, derived Q4 and TTM (owner decisions 1-2); an older API
      // (null) or a failure leaves the statement's own figures, the annual table the 10-K's
      kind === "is"
        ? fetchPerShare(token, companyId, period === "a" ? "annual" : period === "ttm" ? "ttm" : "quarter", basis, asFiled,
          Math.min(req.quarters, 120), asOf).catch(() => null)
        : null,
    ]);
    const older = kind === "is" && perShare === null && annual
      ? await annualPerShare(token, companyId, 10, asOf).then((a) => a.rows).catch(() => [] as FinancialRow[])
      : null;
    const rows = perShare
      ? withPerShare(fin.f.rows, perShare.rows, period, basis)
      : older ? withAnnualPerShare(fin.f.rows, older, basis) : fin.f.rows;
    return { rows, metrics: fin.metrics, callouts: fin.f.callouts ?? [],
      // a 20-F/40-F filer's figures are years (migration 056); an older API doesn't say
      annualOnly: fin.f.cadence === "annual", perShare: perShare !== null,
      // whether a restated figure can be marked yet (086's history; an older API doesn't say)
      history: fin.f.history ?? null };
  });
  const metrics = load?.state === "ok" ? load.data.metrics : (bank ? BANK_STATEMENTS : STATEMENTS)[kind].rows;
  const table = load?.state === "ok"
    ? withBridges(metricTable(load.data.rows, metrics, period, basis, notApplicable, RANGE_QUARTERS[range], [], load.data.callouts), load.data.rows, kind)
    : null;
  // F5: a standardised line opened into its parts; the panel shows one period of it
  const [opened, setOpened] = useState<{ metric: string; periodEnd: string } | null>(null);
  useEffect(() => setOpened(null), [companyId, kind, period, basis]);
  const openRow = (metric: string | null) => {
    const row = table?.rows.find((r) => r.metric === metric);
    const newest = row ? [...row.cells].map((c, i) => (c?.opens ? table!.columns[i]!.periodEnd : null)).filter(Boolean).at(-1) : null;
    setOpened(metric && newest ? { metric, periodEnd: newest } : null);
  };
  const openedRow = opened ? table?.rows.find((r) => r.metric === opened.metric) : undefined;
  // H9: a newer 10-K/10-Q in the index than the figures hold (public index, asked once per company).
  // Today's index against today's figures only: as of a past day the figures are those filed by
  // then, so a report filed since isn't missing from them (Riley, test 10, finding 3)
  const [newest, setNewest] = useState<IndexedFiling | null>(null);
  // a company that reports once a year (20-F, 40-F) is behind its own annual report, not a 10-K/10-Q
  const yearly = load?.state === "ok" && load.data.annualOnly;
  useEffect(() => {
    let live = true;
    setNewest(null);
    if (asOf) return;
    const ask = yearly
      ? filingIndex(token, companyId, { forms: ["20-F", "40-F"], limit: 1 }).then((r) => r.filings[0] ?? null).catch(() => null)
      : newestPeriodic(token, companyId);
    void ask.then((f) => live && setNewest(f));
    return () => {
      live = false;
    };
  }, [token, companyId, asOf, yearly]);
  const behind = load?.state === "ok" && !asOf ? figuresBehind(load.data.rows, newest) : null;
  const exportCsv = () =>
    load?.state === "ok" && table &&
    download(`${ticker.toLowerCase()}-${kind}-${period}${period !== "a" && range !== "8q" ? `-${range}` : ""}${basis === "calendar" ? "-calendar" : ""}.csv`, new Blob([financialsCsv(load.data.rows, table, req.view)], { type: "text/csv" }));

  // a balance sheet says why it has no TTM in its margin, not only in a disabled option's hover (Riley, test 10, finding 61)
  const periodWord = period === "a" ? "by year" : period === "ttm" ? "twelve months to each quarter end"
    : kind === "bs" ? "at each quarter end (a balance has no twelve months)" : "by quarter";
  return (
    <Folio name="Statement" className="statements"
      line={`${STATEMENTS[kind].label}, ${periodWord}`}
      aside={`${basis === "calendar" ? "Calendar" : "Fiscal"} ${period === "a" ? "years" : "quarters"}; ${table?.currency ?? "US dollars"}; derived figures in italic`}>
      <div className="statement-bar">
        <Choices label="Statement" value={kind} set={(k) => set({ kind: k })}
          options={(Object.keys(STATEMENTS) as Statement[]).map((k) => [k, STATEMENTS[k].label])} />
        <Choices label="Period" value={period} set={(p) => set({ period: p })} options={PERIODS}
          off={kind === "bs" ? { ttm: BALANCE_NOT_TTM } : undefined} />
        {/* the rest of the view in one menu (§6 Statements: "8 quarters · Fiscal ▾"): the range (for Quarterly and
            TTM; Annual is ten years), the basis and, on the income statement, the per-share basis */}
        <MenuButton className="btn view-menu" count={false} align="start"
          title="Fiscal names each quarter by the company's own fiscal year; Calendar by the calendar quarter it ends in, so companies with different years line up. For one company the figures are the same, only the column names change."
          label={<>{period === "a" ? "10 years" : RANGE_WORDS[range]} · {basis === "calendar" ? "Calendar" : "Fiscal"}{kind === "is" && asFiled ? " · As filed" : ""}</>}
          items={[
            ...RANGES.map(([r]) => ({ label: RANGE_WORDS[r], checked: range === r, disabled: period === "a", title: period === "a" ? ANNUAL_RANGE : undefined,
              onCheckedChange: () => set({ range: r }) })),
            ...BASES.map(([b, l]) => ({ label: l, checked: basis === b, onCheckedChange: () => set({ basis: b }) })),
            ...(kind === "is" ? SHARE_BASES.map(([v, l]) => ({ label: `Per share: ${l.toLowerCase()}`, checked: (view.shares === "filed" ? "filed" : "adjusted") === v,
              title: `EPS and shares: split-adjusted restates every period to ${asOf ? `the share basis of ${day(asOf, true)} (the splits a filing had restated by then)` : "today's share basis"}, using the company's own restated figures where it filed them and the split's ratio before that; as filed is each filing's own figure for its period.`,
              onCheckedChange: () => set({ shares: v === "filed" ? "filed" : undefined }) })) : []),
          ]} />
        <span className="grow" />
        <button className="btn" onClick={exportCsv} disabled={!table?.rows.length}>Export CSV</button>
      </div>
      {behind && <BehindNotice behind={behind} companyId={companyId} name={name} url={newest?.filing_url ?? null} />}
      {!asOf && !behind && canRead && load?.state === "ok" && !load.data.annualOnly && (
        <ResultsPending token={token} companyId={companyId} fiscalYearEnd={fiscalYearEnd} />
      )}
      {asOf && kind === "is" && !asFiled && load?.state === "ok" && load.data.perShare && (
        <p className="foot" role="note">
          EPS and shares are split-adjusted to the share basis of {day(asOf, true)}, as it stood then: only the stock splits a filing
          had restated by that day apply, not today's basis.
        </p>
      )}
      {load?.state === "ok" && load.data.history === false && (
        <p className="foot restated-pending" role="note">
          Restated figures aren't marked for {name} yet: a mark needs the history Tributary keeps of every figure as first
          reported, which is being built company by company and isn't built for {name}. Data notes lists its restatements now.
        </p>
      )}
      {load?.state === "ok" && load.data.annualOnly && (
        <p className="foot" role="note">
          {name} reports once a year (an annual report on Form 20-F or 40-F), so its figures are fiscal years and the
          balance sheets at their ends. A year is never split into quarters here.
          {period !== "a" && <> <button className="btn sm" onClick={() => set({ period: "a" })}>Show Annual</button></>}
        </p>
      )}
      {load?.state === "ok" && table?.currency && (
        <p className="foot" role="note">
          Figures in {table.currency}, as {name} reports them. They aren't converted, and screens and peer medians leave
          them out of US-dollar comparisons.
        </p>
      )}
      {bank && load?.state === "ok" && metrics !== STATEMENTS[kind].rows && (
        // the Overview says what isn't shown; here one sentence, the rest under How to read (Riley, test 10, finding 61)
        <p className="foot bank-note">{name} is a bank, so it is shown with a bank's own lines.</p>
      )}
      {kind === "bs" && load?.state === "ok" && financeArmNote(companyId, name) && (
        <p className="foot debt-note" role="note">{financeArmNote(companyId, name)}</p>
      )}
      <Gate load={load} name={name} what="Financials">
        {() =>
          table!.rows.length ? (
            <>
              <MetricTableView table={table!} first={STATEMENTS[kind].label} companyId={companyId} open={opened?.metric ?? null} onOpen={openRow} />
              {opened && openedRow && (
                <ComponentsPanel token={token} companyId={companyId} metric={opened.metric} label={openedRow.label}
                  view={req.view} columns={table!.columns.filter((_, i) => openedRow.cells[i]?.opens)} periodEnd={opened.periodEnd}
                  onPeriod={(periodEnd) => setOpened({ ...opened, periodEnd })} onClose={() => setOpened(null)} />
              )}
              {period === "q" && <CalloutList table={table!} companyId={companyId} />}
            </>
          ) : (
            <p className="empty">
              No {STATEMENTS[kind].label.toLowerCase()} values for these periods. Try{" "}
              {period === "a" ? "Quarterly" : "Annual"} or another statement.
            </p>
          )
        }
      </Gate>
      {/* the period table's blank said in the line under it, not only in the legend (Riley, test 10, finding 57) */}
      <p className="foot read-first">Each column names its filing; italic columns are fiscal Q4s derived from the 10-K (d). Select a figure, or move with the arrow keys and press Enter, for its source. A negative amount is in parentheses, and {NO_DATA} marks a period with no figure.</p>
      <details className="how-to-read">
        <summary>How to read these figures</summary>
        <div className="foot">
          {period === "a"
            ? basis === "calendar"
              ? "Annual = the calendar year: the four quarters ending nearest each calendar Q4, balances at its end. "
              : "Annual = the fiscal year: four quarters to each fiscal Q4, balances at the year end, EPS and shares as the 10-K states them. "
            : period === "ttm"
              ? "TTM = trailing twelve months to each quarter end; TTM EPS is the four quarters' EPS added, TTM shares their average by days. "
              : kind === "is"
                ? "A fiscal Q4's EPS is derived: its net income over its diluted shares, worked out from the year's shares less the first three quarters'. "
                : ""}
          {kind === "is" && (asFiled
            ? "EPS and shares are as each period's own filing first stated them, so periods before a stock split are on the old share basis. "
            : asOf
              ? `EPS and shares are split-adjusted to the share basis of ${day(asOf, true)}: the stock splits a filing had restated by then, the company's own restated figure where it filed one, else the split's ratio (the figure as filed is in the hover). A split after that day isn't applied. `
              : "EPS and shares are split-adjusted: every period on today's share basis, the company's own restated figure where it filed one, else the split's ratio (the figure as filed is in the hover). ")}
          {kind === "cf" && "Outflows (capex, dividends, buybacks) are shown negative, as the cash flow statement prints them; the CSV too. Their XBRL tags carry the amount paid, so the chart builder and screener list them as positive amounts. "}
          {kind === "cf" && "Cash at the start of a period is the end of the period before; the reconciliation checks start + change = end. "}
          {period === "q" && "A marked quarter is far from the quarters beside it, or of the other sign (listed under the table). "}
          {bank && `${BANK_NOT_SHOWN} `}
          {"A line with ▸ opens into what it is made of, with the tags and filings behind it. "}
          Italic values are derived (e.g. Q4 = the 10-K's year minus nine months year-to-date), the rest are reported. A
          figure opens its source: its filing, where it sits, and the filing in the reader and on EDGAR. A negative amount is in
          parentheses; the CSV and the card keep its sign.
        </div>
        <BlanksLegend />
      </details>
    </Folio>
  );
}

export function Ratios({ token, companyId, name, ticker, notApplicable = [], asOf }: TableProps & { asOf?: string }) {
  const [opened, setOpened] = useState<{ metric: string; periodEnd: string } | null>(null);
  // with readings, the API says where debt/equity and ROE read n/m on negative equity
  // (`not_meaningful_periods`, from the company's own equity periods), so those aren't gaps;
  // equity comes along so its dates are columns
  // a bank (the API's not_applicable list, 035/058) reads its own ratios; an older API refuses a bank
  // line's name (422): the usual ratios then, as before
  const bank = notApplicable.length > 0;
  const load = useLoad(`${companyId}|${bank}|${asOf ?? ""}`, async () => {
    const ask = (lines: Line[]) => financialsView(token, companyId, 8, [...lines.map(([m]) => m), "stockholders_equity"], "ttm", "fiscal", true, false, asOf)
      .then((f) => ({ ...f, lines }));
    return bank
      ? ask(BANK_RATIOS).catch((e) => {
        if (e instanceof ApiError && e.status === 422) return ask(RATIOS);
        throw e;
      })
      : ask(RATIOS);
  });
  const lines = load?.state === "ok" ? load.data.lines : bank ? BANK_RATIOS : RATIOS;
  const table = load?.state === "ok"
    ? metricTable(load.data.rows, lines, "ttm", "fiscal", notApplicable, undefined, load.data.not_meaningful_periods,
        [], load.data.not_computed ?? {})
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
        {() => {
          if (!table!.rows.length) return <p className="empty">No ratios for these periods yet: they come from 10-Q and 10-K XBRL values. Statements shows what {name} reports.</p>;
          const row = opened ? table!.rows.find((r) => r.metric === opened.metric) : undefined;
          const openRow = (metric: string | null) => {
            const r = table!.rows.find((x) => x.metric === metric);
            const newest = r ? r.cells.map((c, i) => (c?.opens ? table!.columns[i]!.periodEnd : null)).filter(Boolean).at(-1) : null;
            setOpened(metric && newest ? { metric, periodEnd: newest } : null);
          };
          return (
            <>
              <MetricTableView table={table!} first="Ratio" companyId={companyId} open={opened?.metric ?? null} onOpen={openRow} />
              {lines === BANK_RATIOS && (
                <p className="foot bank-note">
                  {name} is a bank, so it reads a bank's ratios: the efficiency ratio (noninterest expense over net interest
                  and noninterest income) and CET1 where it tags one. Net interest margin, tangible book value and return on
                  tangible equity aren't here: no standard tag carries their inputs each quarter.
                </p>
              )}
              {opened && row && (
                <ComponentsPanel token={token} companyId={companyId} metric={opened.metric} label={row.label} view="ttm"
                  columns={table!.columns.filter((_, i) => row.cells[i]?.opens)} periodEnd={opened.periodEnd}
                  onPeriod={(periodEnd) => setOpened({ ...opened, periodEnd })} onClose={() => setOpened(null)} />
              )}
            </>
          );
        }}
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
      // shares are of consolidated revenue; without it the table says what they're of instead. Gross
      // profit and operating income: the totals an "All other" row must tie to (#171)
      financialsView(token, companyId, 8, ["revenue", "gross_profit", "operating_income"], "quarter")
        .catch(() => ({ rows: [] as FinancialRow[] })),
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
            {/* #115: what this tab is for, against Revenue */}
            <p className="tab-purpose muted">
              {name}'s segments quarter by quarter, as filed: revenue, gross profit and operating income by segment, with
              each segment's share of revenue and its growth on the same quarter a year earlier. Three-year trends, what
              drove growth and revenue quality are on{" "}
              <a href={href({ view: "company", companyId, tab: "revenue" })}>Revenue</a>.
            </p>
            {tables.map((t) => (
              <section key={`${t.axis}|${t.metric}`} className="card">
                <div className="card-h">
                  <h2>{t.title}</h2>
                  <span className="sub">quarterly · USD · as reported in the filings' XBRL</span>
                </div>
                {t.staleNote && <p className="card-b stale-note" role="note">⚠ {t.staleNote}</p>}
                <TableScroll atEnd resetKey={t.columns.length} label={t.title}>
                  <table className="t aligned" onKeyDown={onGridKey}>
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
                      {t.rows.map((r, ri) => (
                        <tr key={r.member} className={[r.until ? "retired" : r.reconciling ? "reconciling" : "", r.parent ? "child" : "", r.otherCut ? "other-cut" : ""].filter(Boolean).join(" ") || undefined}>
                          <td className="first" title={r.until ? `${r.member}: last reported for ${r.until}`
                            : r.reconciling ? "What the company reports outside its segments (corporate costs, items it doesn't allocate), shown for the quarters where it and the segments come to the reported total"
                            : r.parent ? `Part of ${t.rows.find((x) => x.member === r.parent)?.label ?? r.parent}: already in its figure, so never added to it`
                            : r.otherCut ? "Another breakdown of the same revenue: it overlaps the lines above, so it isn't added to them"
                            : r.member}>
                            {r.label}
                            {r.until && <span className="until">until {r.until}</span>}
                          </td>
                          {r.cells.map((c, i) => (
                            <Cell key={t.columns[i]!.periodEnd} cell={c} companyId={companyId} row={r.label}
                              column={{ label: t.columns[i]!.label, sub: day(t.columns[i]!.periodEnd, true) }} tab={ri === 0 && i === r.cells.length - 1 ? 0 : -1} />
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
                {t.rows.some((r) => r.reconciling) && (
                  <p className="small muted card-b">
                    All other: what the company reports outside its segments, as it files it, shown only for quarters where
                    the segments and it add up to the reported {t.metric === "revenue" ? "revenue" : t.metric === "gross_profit" ? "gross profit" : "operating income"}
                    {" "}(“{NO_DATA}” elsewhere); it's not a segment, so it has no share or growth rate.
                  </p>
                )}
                {t.metric === "revenue" && t.shareOf === "none" && (
                  <p className="small muted card-b">No shares: consolidated revenue isn't known for this quarter, and these lines overlap, so their sum isn't a total either.</p>
                )}
              </section>
            ))}
            <div className="foot">
              Share: of consolidated revenue in the latest quarter, so lines that overlap each read
              their true share. YoY: latest quarter vs the quarter ending a year earlier. {NO_DATA} marks a quarter
              with no figure for that member. A member marked
              "until" stopped being reported (an old segmentation) and is listed after the current ones.
            </div>
          </>
        )
      }
    </Gate>
  );
}
