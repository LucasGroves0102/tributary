import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, peers as fetchPeers, screener } from "../api";
import {
  chartMetric,
  COMPARE_COLUMNS,
  columnsOf,
  compareCsv,
  compareParams,
  compareRows,
  companiesOf,
  MASKED_TITLE,
  MAX_PEERS,
  medianTitle,
  peerStats,
  short,
  statText,
  type CompareState,
} from "../compare/state";
import { CompanySearch } from "../components/CompanySearch";
import { download } from "../download";
import { day } from "../format";
import { go, href } from "../route";
import type { Peers, Screener } from "../types";

interface Props {
  token: string;
  compare: CompareState;
  /** "Chart these": one line per company for a metric */
  onChart: (companyIds: string[], metric: string, transform: "ttm" | "q" | "yoy", title: string) => void;
  /** Save / Save as (workspaces/SaveBar) */
  saveBar?: ReactNode;
}

type Load<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "error"; message: string };

const SUGGESTIONS = 10;

/** Compare: a comps table of a target and its peers over /v1/screener (`company_id`), each
 * value with its period and source, the peer median, suggestions from /v1/companies/{id}/peers.
 * The comps live in the URL, so a table can be bookmarked, shared and undone with Back. */
export function Compare({ token, compare, onChart, saveBar }: Props) {
  const ids = companiesOf(compare);
  const cols = columnsOf(compare);
  const params = useMemo(() => compareParams(compare), [compare]);
  const key = JSON.stringify(params);
  const [table, setTable] = useState<Load<Screener> | null>(null);
  const [suggested, setSuggested] = useState<Load<Peers> | null>(null);
  const [chosen, setChartKey] = useState(cols[0]!);
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
          setTable({ state: "error", message: e instanceof ApiError ? e.message : "the Query API didn't answer" }),
      );
    return () => {
      live = false;
    };
    // params is keyed by its JSON so an equal comps set doesn't refetch
  }, [token, key]);

  useEffect(() => {
    if (!compare.target) {
      setSuggested(null);
      return;
    }
    let live = true;
    setSuggested({ state: "loading" });
    fetchPeers(token, compare.target, ["revenue_ttm"], SUGGESTIONS)
      .then((data) => live && setSuggested({ state: "ok", data }))
      .catch(() => live && setSuggested({ state: "error", message: "no suggestions" }));
    return () => {
      live = false;
    };
  }, [token, compare.target]);

  const set = (patch: Partial<CompareState>) => go({ view: "compare", compare: { ...compare, ...patch } });
  const peerIds = compare.peers ?? [];
  const addPeer = (id: string) => peerIds.length < MAX_PEERS && set({ peers: [...peerIds, id] });
  const removePeer = (id: string) => set({ peers: peerIds.filter((p) => p !== id) });
  const toggleColumn = (k: string) =>
    set({ cols: cols.includes(k) ? cols.filter((c) => c !== k) : COMPARE_COLUMNS.map((c) => c.key).filter((c) => c === k || cols.includes(c)) });

  const rows = useMemo(
    () => (table?.state === "ok" ? compareRows(compare, table.data.rows) : []),
    [table, compare],
  );
  const stats = useMemo(() => peerStats(rows, cols), [rows, cols]);
  const bankCols = cols
    .filter((k) => rows.some((r) => r.cells[k]?.bank))
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
      new Blob([compareCsv(compare, rows)], { type: "text/csv" }),
    );
  const chartThese = () => {
    const m = chartMetric(chartKey);
    if (m && readableIds.length) onChart(readableIds, m.metric, m.transform, m.title);
  };

  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">Compare</h1>
        {target && <span className="muted">Comps: {target.name}</span>}
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
                {names.get(compare.target) ?? compare.target}{" "}
                <button className="link" aria-label="Change target" onClick={() => set({ target: undefined })}>✕</button>
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
                {names.get(p) ?? p}{" "}
                <button className="link" aria-label={`Remove ${names.get(p) ?? p}`} onClick={() => removePeer(p)}>✕</button>
              </span>
            ))}
            {peerIds.length < MAX_PEERS && (
              <CompanySearch token={token} label="Add peer" placeholder="+ Add peer" exclude={ids}
                onPick={(id) => addPeer(id)} />
            )}
          </div>
          {compare.target && (
            <div className="row wrap">
              <span className="faint">Suggested</span>
              {suggested?.state === "loading" && <span className="faint">…</span>}
              {suggested?.state === "error" && <span className="faint">no suggestions right now</span>}
              {suggested?.state === "ok" && candidates.length === 0 && (
                <span className="faint">no more peers with the same SIC</span>
              )}
              {candidates.map((p) => (
                <button key={p.company_id} className="chip" onClick={() => addPeer(p.company_id)}
                  disabled={peerIds.length >= MAX_PEERS}
                  title={`${p.name ?? p.company_id} · ${p.industry ?? "SIC " + (p.sic ?? "?")}` +
                    `${p.match.revenue_band ? ` · revenue ${p.match.revenue_band}` : ""}` +
                    `${p.access.includes("financials") ? "" : " · metadata only on your plan"}`}>
                  + {p.tickers?.[0] ?? p.name ?? p.company_id}
                  {!p.access.includes("financials") && <span className="faint"> (metadata only)</span>}
                </button>
              ))}
              {suggested?.state === "ok" && (
                <span className="faint small" title={suggested.data.method}>same SIC, then size</span>
              )}
            </div>
          )}
          <div className="row wrap">
            <span className="faint">Columns</span>
            {COMPARE_COLUMNS.map((c) => (
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
                title="One line per company you can read, calendar quarters">
                Chart these
              </button>
            </>
          )}
        </div>
        {!ids.length ? (
          <p className="empty">
            Pick a target company, then add peers or take the suggested ones (same SIC code, then
            similar size). Or open a company and choose Compare.
          </p>
        ) : table?.state === "error" ? (
          <p className="empty error">Couldn't load the comps ({table.message}).</p>
        ) : !table || table.state === "loading" ? (
          <p className="empty">Loading the comps…</p>
        ) : (
          <div className="tw">
            <table className="t comps">
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
                  <th><span className="visually-hidden">Actions</span></th>
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
                    <td className="muted" title={r.latest ? `Period ended ${day(r.latest.periodEnd, true)}; ${r.latest.form} filed ${day(r.latest.filed, true)}` : undefined}>
                      {r.latest ? (
                        <>
                          {short(r.latest.calendar)}
                          {r.latest.fiscal && <span className="faint"> ({short(r.latest.fiscal)})</span>}
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
                          {cell.text}
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
                  <td className="faint">{rows.filter((r) => !r.isTarget && r.readable).length} readable peers</td>
                  {cols.map((k) => (
                    <td key={k} className="n" title={medianTitle(stats[k]!)}>
                      {statText(k, stats[k]!.median)}
                      {stats[k]!.banks > 0 && <span className="unit"> excl. banks</span>}
                    </td>
                  ))}
                  <td />
                </tr>
                <tr className="agg">
                  <td className="first">High / low</td>
                  <td />
                  {cols.map((k) => (
                    <td key={k} className="n">
                      {stats[k]!.n ? `${statText(k, stats[k]!.high)} / ${statText(k, stats[k]!.low)}` : "—"}
                    </td>
                  ))}
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
        )}
        <div className="foot">
          USD. No valuation multiples: Tributary has no market prices. Each company's own latest
          quarter, labelled by calendar quarter (fiscal in brackets); hover a value for its
          period and source filing.
          {bankCols.length > 0 &&
            ` Banks show n/a for ${bankCols.join(", ")} (deposits are a bank's debt and lending its operations) and are left out of ${bankCols.length === 1 ? "that column's" : "those columns'"} median.`}
          {fyes.length > 1 &&` Fiscal years end in different months (${fyes.join(", ")}); TTM figures compare regardless.`}
          {masked.length > 0 &&
            ` ${masked.length} ${masked.length === 1 ? "company shows" : "companies show"} metadata only: your plan doesn't cover ${masked.length === 1 ? "its" : "their"} financials, so ${masked.length === 1 ? "it is" : "they are"} left out of the median and charts.`}
        </div>
      </section>
    </div>
  );
}
