import { useEffect, useMemo, useState, type ReactNode } from "react";

import { failureReason, peerCheck, peers as fetchPeers, screener } from "../api";
import { NOT_APPLICABLE, NOT_REPORTED } from "../blanks";
import { UNNAMED } from "../displayNames";
import {
  chartMetric,
  COMPARE_COLUMNS,
  columnsOf,
  compareCsv,
  compareParams,
  compareRows,
  companiesOf,
  countText,
  isBank,
  MASKED_TITLE,
  MAX_PEERS,
  medianTitle,
  nextSort,
  peerStats,
  short,
  sortRows,
  statText,
  type CompareSort,
  type CompareState,
} from "../compare/state";
import { MAX_COMPANY_LINES } from "../chart/model";
import { SmallMultiples } from "../compare/SmallMultiples";
import { CompanySearch } from "../components/CompanySearch";
import { FigureText } from "../components/FigureText";
import { ErrorPanel, Skeleton } from "../components/States";
import { navRow, onTableKey } from "../components/tableNav";
import { download } from "../download";
import { day } from "../format";
import { go, href } from "../route";
import { inScope, useScope } from "../scope";
import { peerRefusal, suggestionTitle } from "../compare/peers";
import type { Peers, Screener } from "../types";
import { TableScroll } from "../components/TableScroll";

interface Props {
  token: string;
  compare: CompareState;
  /** "Chart these": one line per company for a metric */
  onChart: (companyIds: string[], metric: string, transform: "ttm" | "q" | "yoy") => void;
  /** Save / Save as (workspaces/SaveBar) */
  saveBar?: ReactNode;
  /** inside an analysis project: the comps are the project's (state handed in, changes handed
   * back instead of going to the URL), the target is fixed and the page title is a section's */
  embedded?: { onChange: (next: CompareState) => void };
}

type Load<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "error"; message: string };

const SUGGESTIONS = 10;

/** Compare: a comps table of a target and its peers over /v1/screener (`company_id`), each
 * value with its period and source, the peer median, suggestions from /v1/companies/{id}/peers.
 * The comps live in the URL, so a table can be bookmarked, shared and undone with Back. */
export function Compare({ token, compare: asked, onChart, saveBar, embedded }: Props) {
  // a demo sandbox suggests, finds and shows only its own companies (scope.ts); a link with
  // others (an old or shared one) drops them, with a note
  const scope = useScope();
  const scoped = scope !== null;
  const compare = useMemo(
    (): CompareState =>
      scope
        ? {
            ...asked,
            target: asked.target && inScope(scope, asked.target) ? asked.target : undefined,
            peers: asked.peers?.filter((p) => inScope(scope, p)),
          }
        : asked,
    [scope, asked],
  );
  const leftOut = companiesOf(asked).length - companiesOf(compare).length;
  const ids = companiesOf(compare);
  const params = useMemo(() => compareParams(compare), [compare]);
  const key = JSON.stringify(params);
  const [table, setTable] = useState<Load<Screener> | null>(null);
  const [suggested, setSuggested] = useState<Load<Peers> | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // a bank target (035's rule, from its row) gets a bank's own columns unless some were chosen
  const bank = table?.state === "ok" && isBank(table.data.rows.find((r) => r.company_id === compare.target));
  const cols = columnsOf(compare, bank);
  const [chosen, setChartKey] = useState(cols[0]!);
  const [multiples, setMultiples] = useState(false);
  const [sort, setSort] = useState<CompareSort | null>(null);
  const chartKey = cols.includes(chosen) ? chosen : cols[0]!;

  useEffect(() => {
    if (!ids.length) {
      setTable(null);
      return;
    }
    let live = true;
    setTable((t) => (t?.state === "ok" ? t : { state: "loading" }));
    screener(token, params)
      .then((data) => live && setTable({ state: "ok", data }))
      .catch(
        (e) =>
          live &&
          setTable({ state: "error", message: failureReason(e) }),
      );
    return () => {
      live = false;
    };
    // params is keyed by its JSON so an equal comps set doesn't refetch
  }, [token, key, attempt]);

  useEffect(() => {
    if (!compare.target) {
      setSuggested(null);
      return;
    }
    let live = true;
    setSuggested({ state: "loading" });
    fetchPeers(token, compare.target, ["revenue_ttm"], SUGGESTIONS, scoped)
      .then((data) => live && setSuggested({ state: "ok", data }))
      .catch(() => live && setSuggested({ state: "error", message: "no suggestions" }));
    return () => {
      live = false;
    };
  }, [token, compare.target, scoped]);

  const set = (patch: Partial<CompareState>) =>
    embedded ? embedded.onChange({ ...compare, ...patch }) : go({ view: "compare", compare: { ...compare, ...patch } });
  const peerIds = compare.peers ?? [];
  const addPeer = (id: string) => peerIds.length < MAX_PEERS && set({ peers: [...peerIds, id] });
  /** A picked company joins only if it has figures to compare; otherwise say why (F4: a foreign
   * filer like Birkenstock, no XBRL yet, no annual report). An older API has no check: add it. */
  const pickPeer = (id: string, name: string) => {
    setRefused(null);
    if (!compare.target) return addPeer(id);
    peerCheck(token, compare.target, id)
      .then((check) => (check && check.ok === false ? setRefused(peerRefusal(name, check)) : addPeer(id)))
      .catch(() => addPeer(id));
  };
  const removePeer = (id: string) => set({ peers: peerIds.filter((p) => p !== id) });
  const toggleColumn = (k: string) =>
    set({ cols: cols.includes(k) ? cols.filter((c) => c !== k) : COMPARE_COLUMNS.map((c) => c.key).filter((c) => c === k || cols.includes(c)) });
  const offered = COMPARE_COLUMNS.filter((c) => !c.bank || bank || cols.includes(c.key));

  const rows = useMemo(
    () => (table?.state === "ok" ? compareRows(compare, table.data.rows, bank) : []),
    [table, compare, bank],
  );
  const stats = useMemo(() => peerStats(rows, cols), [rows, cols]);
  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);
  /** A sortable header: a button in the th, so the keyboard reaches it. */
  const sortHead = (key: string, content: ReactNode, title?: string, unit?: string) => (
    <button type="button" className="th-sort" title={title} onClick={() => setSort((s) => nextSort(s, key))}>
      {content}
      {sort?.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
      {unit && <span className="unit"> {unit}</span>}
    </button>
  );
  const ariaSort = (key: string) =>
    sort?.key === key ? (sort.desc ? ("descending" as const) : ("ascending" as const)) : undefined;
  const bankCols = cols
    .filter((k) => rows.some((r) => r.cells[k]?.status === "not_applicable"))
    .map((k) => COMPARE_COLUMNS.find((c) => c.key === k)!.long.toLowerCase());
  const target = rows.find((r) => r.isTarget);
  const names = new Map(rows.map((r) => [r.companyId, r.ticker ?? r.name]));
  const masked = rows.filter((r) => !r.readable);
  const readableIds = rows.filter((r) => r.readable).map((r) => r.companyId);
  const fyes = rows.filter((r) => r.readable && r.fye).map((r) => `${r.ticker ?? r.name} ${r.fye}`);
  const candidates =
    suggested?.state === "ok" ? suggested.data.peers.filter((p) => !ids.includes(p.company_id)) : [];

  const exportCsv = () =>
    download(
      `comps-${target?.ticker?.toLowerCase() ?? "table"}.csv`,
      new Blob([compareCsv(compare, rows, bank)], { type: "text/csv" }),
    );
  const chartThese = () => {
    const m = chartMetric(chartKey);
    if (m && readableIds.length) onChart(readableIds, m.metric, m.transform);
  };

  return (
    <div className={embedded ? "compare-embedded" : "page"}>
      <div className={embedded ? "row wrap" : "row"}>
        {embedded ? <h2 className="grow">Comps</h2> : <h1 className="grow">Compare</h1>}
        {target && !embedded && <span className="muted">Comps: {target.name}</span>}
        <button className="btn" onClick={exportCsv} disabled={!rows.length}
          title="Exact values with each one's period end and source filing, plus the peer median">
          Export CSV
        </button>
        {saveBar}
      </div>

      <section className="card">
        <div className="card-b comps-setup">
          <div className="row wrap">
            <span className="faint">Target</span>
            {compare.target ? (
              <span className="tag">
                {names.get(compare.target) ?? UNNAMED}{" "}
                {!embedded && <button className="link" aria-label="Change target" onClick={() => set({ target: undefined })}>✕</button>}
              </span>
            ) : (
              <CompanySearch token={token} label="Target company" placeholder="Company to compare, e.g. PEP"
                exclude={peerIds} onPick={(id) => set({ target: id })} />
            )}
          </div>
          <div className="row wrap">
            <span className="faint">Peers ({peerIds.length}/{MAX_PEERS})</span>
            {peerIds.map((p) => (
              <span key={p} className="tag">
                {names.get(p) ?? UNNAMED}{" "}
                <button className="link" aria-label={`Remove ${names.get(p) ?? "this company"}`} onClick={() => removePeer(p)}>✕</button>
              </span>
            ))}
            {peerIds.length < MAX_PEERS && (
              <CompanySearch token={token} label="Add peer" placeholder="+ Add peer" exclude={ids}
                onPick={(id, name) => pickPeer(id, name)} />
            )}
          </div>
          {refused && (
            <p className="notice small" role="status">
              {refused}{" "}
              <button className="link" onClick={() => setRefused(null)}>Dismiss</button>
            </p>
          )}
          {compare.target && (
            <div className="row wrap">
              <span className="faint">Suggested</span>
              {suggested?.state === "loading" && <span className="faint">…</span>}
              {suggested?.state === "error" && <span className="faint">no suggestions right now</span>}
              {suggested?.state === "ok" && candidates.length === 0 && (
                <span className="faint">
                  {scoped ? "no more companies in your demo share its industry" : "no more suggestions"}
                </span>
              )}
              {candidates.map((p) => (
                <button key={p.company_id} className="chip" onClick={() => addPeer(p.company_id)}
                  disabled={peerIds.length >= MAX_PEERS}
                  title={suggestionTitle(p, suggested?.state === "ok" ? suggested.data.company.name : null)}>
                  + {p.tickers?.[0] ?? p.name ?? p.company_id}
                  {!p.access.includes("financials") && <span className="faint"> (metadata only)</span>}
                </button>
              ))}
              {suggested?.state === "ok" && (
                <span className="faint small" title={suggested.data.method}>
                  {suggested.data.basis === "business" ? "by what they do (10-K Business sections)" : "same SIC, then size"}
                </span>
              )}
            </div>
          )}
          {leftOut > 0 && (
            <p className="faint small">
              {leftOut === 1 ? "One company" : `${leftOut} companies`} from this link{" "}
              {leftOut === 1 ? "isn't" : "aren't"} in this demo, so {leftOut === 1 ? "it's" : "they're"} left out.
            </p>
          )}
          <div className="row wrap">
            <span className="faint">Columns</span>
            {offered.map((c) => (
              <label key={c.key} className="chk">
                <input type="checkbox" checked={cols.includes(c.key)}
                  disabled={cols.includes(c.key) && cols.length === 1}
                  onChange={() => toggleColumn(c.key)} />
                {c.label}
              </label>
            ))}
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-h wrap">
          <h2>Comparable companies</h2>
          <span className="sub">
            each company's latest reported quarter · TTM to that quarter, balances at its end · median
            excludes the target
          </span>
          <span className="grow" />
          {rows.length > 0 && (
            <>
              <select className="field narrow" aria-label="Metric to chart" value={chartKey}
                onChange={(e) => setChartKey(e.target.value)}>
                {cols.map((k) => (
                  <option key={k} value={k}>{COMPARE_COLUMNS.find((c) => c.key === k)!.long}</option>
                ))}
              </select>
              <button className="btn sm" onClick={chartThese} disabled={!readableIds.length}
                title={`One line per company you can read, calendar quarters${readableIds.length > MAX_COMPANY_LINES ? `: the first ${MAX_COMPANY_LINES} of ${readableIds.length}` : ""}`}>
                Chart these
              </button>
            </>
          )}
        </div>
        {!ids.length ? (
          <p className="empty">
            Pick a target company, then add peers or take the suggested ones (companies whose 10-K
            describes a similar business). Or open a company and choose Compare.
          </p>
        ) : table?.state === "error" ? (
          <ErrorPanel what="the comps" detail={`${table.message}. Fewer columns or peers may help`}
            onRetry={() => {
              setTable({ state: "loading" });
              setAttempt((n) => n + 1);
            }} />
        ) : !table || table.state === "loading" ? (
          <Skeleton lines={Math.min(ids.length, 8) + 2} label="Loading the comps" />
        ) : (
          <TableScroll label="Comparable companies">
            <table className="t comps">
              <thead>
                <tr>
                  <th className="first" aria-sort={ariaSort("name")}>{sortHead("name", "Company")}</th>
                  <th aria-sort={ariaSort("latest")}>{sortHead("latest", "Latest quarter")}</th>
                  {cols.map((k) => {
                    const c = COMPARE_COLUMNS.find((x) => x.key === k)!;
                    return (
                      <th key={k} className="n" aria-sort={ariaSort(k)}>
                        {sortHead(k, c.label, c.long, c.period)}
                      </th>
                    );
                  })}
                  <th><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody onKeyDown={onTableKey}>
                {sorted.map((r, i) => (
                  <tr key={r.companyId} className={[r.isTarget ? "sel" : "", r.readable ? "" : "masked"].join(" ").trim() || undefined}
                    {...navRow(i, r.name)}>
                    <td className="first">
                      <a className="co" href={href({ view: "company", companyId: r.companyId })}>
                        <b>{r.name}</b>
                        {r.ticker && <span className="faint mono">{r.ticker}</span>}
                        {r.isTarget && <span className="tag q">target</span>}
                        {!r.readable && <span className="tag" title={MASKED_TITLE}>metadata only</span>}
                      </a>
                    </td>
                    <td className="muted" title={r.latest ? `${r.latest.fiscal ? `${short(r.latest.fiscal)}. ` : ""}Period ended ${day(r.latest.periodEnd, true)}; ${r.latest.form} filed ${day(r.latest.filed, true)}` : undefined}>
                      {r.latest ? (
                        <>
                          {short(r.latest.calendar)}
                          {r.latest.fiscal && <span className="faint wide-only"> ({short(r.latest.fiscal)})</span>}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    {cols.map((k) => {
                      const cell = r.cells[k]!;
                      const growth = k === "revenue_growth_ttm" && cell.value !== null;
                      return (
                        <td key={k} className={`n ${cell.value === null ? "faint" : growth ? (cell.value >= 0 ? "pos" : "neg") : ""}`}
                          title={cell.title}>
                          <FigureText cell={cell} />
                        </td>
                      );
                    })}
                    <td className="n actions">
                      {!r.isTarget && (
                        <>
                          <button className="btn sm ghost" onClick={() => set({ target: r.companyId, peers: [...(compare.target ? [compare.target] : []), ...peerIds.filter((p) => p !== r.companyId)] })}
                            aria-label={`Make ${r.name} the target`}>
                            Target
                          </button>
                          <button className="btn sm ghost" onClick={() => removePeer(r.companyId)} aria-label={`Remove ${r.name}`}>
                            ✕
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                <tr className="agg">
                  <td className="first">Peer median</td>
                  <td className="faint" title="Each median says how many of the peers it uses; hover it for why the others are left out">n of N peers</td>
                  {cols.map((k) => (
                    <td key={k} className="n" title={medianTitle(stats[k]!)}>
                      {stats[k]!.n === 0 && stats[k]!.banks > 0 ? NOT_APPLICABLE : statText(k, stats[k]!.median)}
                      <span className="unit count">{countText(stats[k]!)}</span>
                    </td>
                  ))}
                  <td />
                </tr>
                <tr className="agg">
                  <td className="first">High</td>
                  <td />
                  {cols.map((k) => (
                    <td key={k} className="n">{stats[k]!.n ? statText(k, stats[k]!.high) : stats[k]!.banks ? NOT_APPLICABLE : NOT_REPORTED}</td>
                  ))}
                  <td />
                </tr>
                <tr className="agg">
                  <td className="first">Low</td>
                  <td />
                  {cols.map((k) => (
                    <td key={k} className="n">{stats[k]!.n ? statText(k, stats[k]!.low) : stats[k]!.banks ? NOT_APPLICABLE : NOT_REPORTED}</td>
                  ))}
                  <td />
                </tr>
              </tbody>
            </table>
          </TableScroll>
        )}
        <div className="foot">
          USD. No valuation multiples: Tributary has no market prices. Each company's own latest
          quarter, labelled by calendar quarter (fiscal in brackets); hover a value for its
          period and source filing. Click a column to sort, again to reverse, a third time for
          the order you added them. Keyboard: ↑ ↓ move between rows, Enter opens the company.
          {bankCols.length > 0 &&
            ` Banks show n/a for ${bankCols.join(", ")} (deposits are a bank's debt and lending its operations) and are left out of ${bankCols.length === 1 ? "that column's" : "those columns'"} median.`}
          {fyes.length > 1 &&` Fiscal years end in different months (${fyes.join(", ")}); TTM figures compare regardless.`}
          {masked.length > 0 &&
            ` ${masked.length} ${masked.length === 1 ? "company shows" : "companies show"} metadata only: your plan doesn't cover ${masked.length === 1 ? "its" : "their"} financials, so ${masked.length === 1 ? "it is" : "they are"} left out of the median and charts.`}
        </div>
      </section>

      {rows.length > 0 && (
        <section className="card">
          <div className="card-h wrap">
            <h2>Side by side</h2>
            <span className="sub">
              one chart per column · every company you can read a line, in the same colour on each · calendar quarters, last 3 years
              {readableIds.length > MAX_COMPANY_LINES ? ` · the first ${MAX_COMPANY_LINES} of ${readableIds.length} companies` : ""}
            </span>
            <span className="grow" />
            <button className="btn sm" aria-expanded={multiples} onClick={() => setMultiples((m) => !m)}
              disabled={!readableIds.length}>
              {multiples ? "Hide charts" : `Show ${cols.length} chart${cols.length === 1 ? "" : "s"}`}
            </button>
          </div>
          {multiples ? (
            <SmallMultiples token={token} companyIds={readableIds} cols={cols} label={(id) => names.get(id) ?? UNNAMED} />
          ) : (
            <p className="empty">
              {readableIds.length
                ? "Show the selected columns as small charts over time, one line per company; hover for values and sources, click to open the filing."
                : "Charts need financials access to at least one of these companies."}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
