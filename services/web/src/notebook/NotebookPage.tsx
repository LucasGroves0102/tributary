/** The SQL workspace (#/notebook, B3a): a notebook of queries over Tributary's figures, the filing
 * index and the user's own data (the `workspace` views, query-api sql_workspace.py), the user's
 * notes in Markdown and charts over a query's result. Each query runs through POST /v1/sql as the
 * signed-in user, one at a time: the server checks it is one SELECT and runs it read only under the
 * same access rules as every other screen. Results stay in this page (CSV on request); a saved
 * notebook holds only what the user wrote (notebook.ts). Nothing of it reaches the assistant. */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { failureReason, openItem, runSql, sqlSchema } from "../api";
import { LayerChart } from "../chart/LayerChart";
import { Choices } from "../components/Choices";
import { MenuButton } from "../components/MenuButton";
import { Empty, ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { toast } from "../components/Toasts";
import { useLeaveWarning } from "../components/useLeaveWarning";
import { download } from "../download";
import { localTime } from "../format";
import { renderMarkdown } from "../markdown";
import { go } from "../route";
import type { NotebookCell, NotebookChart, NotebookState, SqlRefusal, SqlResult, SqlSchema } from "../types";
import { ITEM_NAME_MAX, nameProblem } from "../workspaces/model";
import type { Saved } from "../workspaces/useSaved";
import {
  chartSeries,
  csvOf,
  defaultChart,
  isNumberColumn,
  lineOf,
  MAX_CELLS,
  MAX_SERIES,
  moved,
  newCell,
  notebookState,
  NOTEBOOKS_WORKSPACE,
  queryName,
  REFUSAL_WORDS,
  restoreNotebook,
  resultLine,
  saveProblem,
  shown,
  starter,
} from "./notebook";

/** rows drawn at first (the CSV has them all) */
const SHOWN_ROWS = 100;

type Run = { state: "running" } | { state: "done"; result: SqlResult; at: string } | { state: "refused"; refusal: SqlRefusal } | { state: "failed"; message: string };
type Schema = { state: "loading" } | { state: "ok"; schema: SqlSchema } | { state: "off" } | { state: "error"; message: string };
type Item = { state: "new" } | { state: "loading" } | { state: "ok"; id: string; name: string; saved: string } | { state: "error"; message: string };

export function NotebookPage({ token, itemId, saved }: { token: string; itemId: string | null; saved: Saved | null }) {
  const [schema, setSchema] = useState<Schema>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    sqlSchema(token)
      .then((s) => live && setSchema(s ? { state: "ok", schema: s } : { state: "off" }))
      .catch((e) => live && setSchema({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);

  const [nb, setNb] = useState<NotebookState>(() => starter());
  const [item, setItem] = useState<Item>(itemId ? { state: "loading" } : { state: "new" });
  useEffect(() => {
    if (!itemId) return;
    let live = true;
    openItem(token, itemId)
      .then((it) => {
        if (!live) return;
        if (it.kind !== "notebook") return setItem({ state: "error", message: "that saved item isn't a notebook" });
        const restored = restoreNotebook(it.state);
        setNb(restored);
        setItem({ state: "ok", id: it.item_id, name: it.name, saved: JSON.stringify(notebookState(restored)) });
      })
      .catch((e) => live && setItem({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, itemId]);

  const fingerprint = JSON.stringify(notebookState(nb));
  const dirty = item.state === "ok" ? fingerprint !== item.saved : item.state === "new" && nb.cells.some((c) => (c.sql ?? c.text ?? "").trim() && !isStarter(c));
  useLeaveWarning(dirty);

  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [editing, setEditing] = useState<Set<string>>(() => new Set());
  const runningRef = useRef(false);

  const run = useCallback(async (cell: NotebookCell): Promise<boolean> => {
    const sql = cell.sql ?? "";
    if (!sql.trim()) return false;
    setRuns((r) => ({ ...r, [cell.id]: { state: "running" } }));
    try {
      const answer = await runSql(token, sql);
      if (answer === null) {
        setRuns((r) => ({ ...r, [cell.id]: { state: "failed", message: "queries can't run on this server yet" } }));
        return false;
      }
      if ("refusal" in answer) {
        setRuns((r) => ({ ...r, [cell.id]: { state: "refused", refusal: answer.refusal } }));
        return false;
      }
      setRuns((r) => ({ ...r, [cell.id]: { state: "done", result: answer.result, at: new Date().toISOString() } }));
      return true;
    } catch (e) {
      setRuns((r) => ({ ...r, [cell.id]: { state: "failed", message: failureReason(e) } }));
      return false;
    }
  }, [token]);

  /** every query from the top, one at a time (the server runs one per user), stopping at a failure */
  const runAll = async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    try {
      for (const c of nb.cells) {
        if (c.kind !== "sql" || !(c.sql ?? "").trim()) continue;
        if (!(await run(c))) break;
      }
    } finally {
      runningRef.current = false;
    }
  };

  const setCell = (id: string, patch: Partial<NotebookCell>) =>
    setNb((n) => ({ cells: n.cells.map((c) => (c.id === id ? { ...c, ...patch } : c)) }));
  const add = (kind: NotebookCell["kind"], after?: string, body: Partial<NotebookCell> = {}): NotebookCell | null => {
    if (nb.cells.length >= MAX_CELLS) {
      toast({ text: `A notebook holds at most ${MAX_CELLS} cells.` });
      return null;
    }
    const cell = newCell(kind, after ? nb.cells.slice(0, nb.cells.findIndex((c) => c.id === after) + 1) : nb.cells, body);
    setNb((n) => {
      const at = after ? n.cells.findIndex((c) => c.id === after) + 1 : n.cells.length;
      return { cells: [...n.cells.slice(0, at), cell, ...n.cells.slice(at)] };
    });
    if (kind === "text" && !body.text) setEditing((e) => new Set(e).add(cell.id));
    return cell;
  };
  const remove = (id: string) => {
    setNb((n) => ({ cells: n.cells.filter((c) => c.id !== id) }));
    setRuns(({ [id]: _, ...rest }) => rest);
  };
  const move = (id: string, by: number) => setNb((n) => ({ cells: moved(n.cells, n.cells.findIndex((c) => c.id === id), by) }));

  const useExample = (ex: { title: string; sql: string }) => {
    const cell = add("sql", undefined, { sql: ex.sql });
    if (cell) void run(cell);
  };
  const insertInto = (name: string) => add("sql", undefined, { sql: `SELECT *\nFROM ${name}\nLIMIT 100` });

  // saving
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("Untitled notebook");
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const save = async () => {
    if (!saved) return;
    const problem = saveProblem(nb);
    if (problem) return setSaveError(problem);
    setSaveError(null);
    if (item.state === "ok") {
      setBusy(true);
      try {
        const next = await saved.update(item.id, { state: notebookState(nb) });
        setItem({ state: "ok", id: next.item_id, name: next.name, saved: JSON.stringify(notebookState(nb)) });
        toast({ text: `Saved ${next.name}.`, topic: "notebook-save" });
      } catch (e) {
        setSaveError(`Couldn't save it: ${failureReason(e)}.`);
      } finally {
        setBusy(false);
      }
      return;
    }
    setNaming(true);
  };
  const saveNew = async () => {
    if (!saved) return;
    const problem = nameProblem(name, ITEM_NAME_MAX) ?? saveProblem(nb);
    if (problem) return setSaveError(problem);
    setBusy(true);
    setSaveError(null);
    try {
      const it = await saved.create("notebook", name.trim(), notebookState(nb), { newWorkspace: NOTEBOOKS_WORKSPACE });
      setItem({ state: "ok", id: it.item_id, name: it.name, saved: JSON.stringify(notebookState(nb)) });
      setNaming(false);
      toast({ text: `Saved ${it.name} in ${NOTEBOOKS_WORKSPACE}. Results aren't saved: they're read again, as you, when you run it.` });
      window.history.replaceState(null, "", `#/notebook/${encodeURIComponent(it.item_id)}`);
    } catch (e) {
      setSaveError(`Couldn't save it: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };

  const results = useMemo(() => Object.fromEntries(Object.entries(runs).flatMap(([id, r]) => (r.state === "done" ? [[id, r.result]] : []))), [runs]);
  const anyRunning = Object.values(runs).some((r) => r.state === "running");
  const limits = schema.state === "ok" ? schema.schema.limits : null;

  if (item.state === "loading") return <div className="page"><Skeleton lines={6} label="Opening the notebook" /></div>;
  if (item.state === "error") {
    return (
      <div className="page">
        <ErrorPanel what="the notebook" detail={item.message} />
        <p><button className="btn" onClick={() => go({ view: "notebook" })}>New notebook</button></p>
      </div>
    );
  }

  return (
    <div className="page notebook-page">
      <div className="row page-bar">
        <h1 className="grow">{item.state === "ok" ? item.name : "Notebook"}</h1>
        <button className="btn" disabled={anyRunning || schema.state !== "ok"} onClick={() => void runAll()}>Run all</button>
        <MenuButton label="Add" count={false} items={[
          { label: "A query", onSelect: () => add("sql") },
          { label: "Text", onSelect: () => add("text") },
          { label: "A chart", onSelect: () => add("chart") },
        ]} />
        {saved && <button className="btn primary" disabled={busy || !dirty && item.state === "ok"} onClick={() => void save()}>Save</button>}
      </div>
      <p className="nb-status muted small">
        {item.state === "ok" ? <>Notebook in {NOTEBOOKS_WORKSPACE}{dirty ? <span className="nb-dirty">Unsaved changes</span> : null}</> : "Not saved yet"}
        {limits && <span className="nb-limits">One query at a time, up to {limits.seconds} seconds and {limits.rows.toLocaleString("en-US")} rows each; {Math.round(limits.seconds_per_day / 60)} minutes of queries a day.</span>}
      </p>
      {naming && (
        <form className="pf-inline nb-save" aria-label="Save the notebook" onSubmit={(e) => { e.preventDefault(); void saveNew(); }}>
          <label>
            <span>Name</span>
            <input className="field" value={name} maxLength={ITEM_NAME_MAX} autoFocus onChange={(e) => setName(e.target.value)} />
          </label>
          <button className="btn primary" disabled={busy}>Save in {NOTEBOOKS_WORKSPACE}</button>
          <button type="button" className="btn ghost" onClick={() => setNaming(false)}>Cancel</button>
        </form>
      )}
      {saveError && <p className="err" role="alert">{saveError}</p>}
      {!saved && <p className="muted small">Saving is unavailable right now: run queries, export them as CSV.</p>}

      <div className="nb-layout">
        <div className="nb-cells">
          {schema.state === "off" ? (
            <Empty>The SQL workspace isn't available on this server yet.</Empty>
          ) : schema.state === "error" ? (
            <ErrorPanel what="the SQL workspace" detail={schema.message} onRetry={() => setAttempt((n) => n + 1)} />
          ) : null}
          {nb.cells.map((cell, i) => (
            <CellView key={cell.id} cell={cell} index={i} cells={nb.cells} run={runs[cell.id]} results={results}
              editing={editing.has(cell.id)}
              setEditing={(on) => setEditing((e) => { const n = new Set(e); if (on) n.add(cell.id); else n.delete(cell.id); return n; })}
              onChange={(patch) => setCell(cell.id, patch)} onRun={() => void run(cell)} disabled={schema.state !== "ok"}
              onMove={(by) => move(cell.id, by)} onRemove={() => remove(cell.id)}
              onChart={(chart) => add("chart", cell.id, { chart })} />
          ))}
          {!nb.cells.length && <Empty>An empty notebook. Add a query, or start from an example.</Empty>}
          <div className="nb-add" role="group" aria-label="Add a cell">
            <button className="btn sm" onClick={() => add("sql")}>Add a query</button>
            <button className="btn sm" onClick={() => add("text")}>Add text</button>
            <button className="btn sm" onClick={() => add("chart")}>Add a chart</button>
          </div>
        </div>
        <aside className="nb-side" aria-label="The workspace's views">
          {schema.state === "loading" ? <Skeleton lines={6} label="Loading the views" />
            : schema.state === "ok" ? <SchemaBrowser schema={schema.schema} onInsert={insertInto} onExample={useExample} /> : null}
        </aside>
      </div>
    </div>
  );
}

/** The starter's cells count as untouched (no warning on leaving a notebook nobody wrote in). */
function isStarter(c: NotebookCell): boolean {
  return (c.kind === "text" && c.text === "## Notes") || (c.kind === "sql" && (c.sql ?? "").startsWith("SELECT name, tickers, sector\nFROM companies\nWHERE 'NVDA'"));
}

function CellView({ cell, index, cells, run, results, editing, setEditing, onChange, onRun, disabled, onMove, onRemove, onChart }: {
  cell: NotebookCell;
  index: number;
  cells: NotebookCell[];
  run: Run | undefined;
  results: Record<string, SqlResult>;
  editing: boolean;
  setEditing: (on: boolean) => void;
  onChange: (patch: Partial<NotebookCell>) => void;
  onRun: () => void;
  disabled: boolean;
  onMove: (by: number) => void;
  onRemove: () => void;
  onChart: (chart: NotebookChart) => void;
}) {
  const label = cell.kind === "sql" ? queryName(cells, cell.id) : cell.kind === "text" ? "Note" : "Chart";
  const more = (
    <MenuButton label="More" count={false} className="btn sm ghost more-btn" items={[
      { label: "Move up", onSelect: () => onMove(-1), disabled: index === 0 },
      { label: "Move down", onSelect: () => onMove(1), disabled: index === cells.length - 1 },
      { label: "Delete the cell", onSelect: onRemove },
    ]} />
  );
  return (
    <section className={`nb-cell nb-${cell.kind}`} aria-label={label}>
      <header className="nb-head">
        <h2 className="nb-rubric">{label}</h2>
        <span className="nb-line">
          {cell.kind === "sql" && run?.state === "done" ? resultLine(run.result)
            : cell.kind === "sql" && run?.state === "running" ? "Running…"
            : cell.kind === "chart" && cell.chart?.source ? `Over ${queryName(cells, cell.chart.source)}` : ""}
        </span>
        <span className="grow" />
        {cell.kind === "sql" && (
          <button className="btn sm" disabled={disabled || run?.state === "running" || !(cell.sql ?? "").trim()} onClick={onRun}
            title="Run this query (Ctrl+Enter)">Run</button>
        )}
        {cell.kind === "text" && (
          <button className="btn sm ghost" onClick={() => setEditing(!editing)}>{editing ? "Done" : "Edit"}</button>
        )}
        {more}
      </header>
      {cell.kind === "sql" && <SqlCell cell={cell} run={run} onChange={onChange} onRun={onRun} onChart={onChart} />}
      {cell.kind === "text" && <TextCell cell={cell} editing={editing} onChange={onChange} onDone={() => setEditing(false)} />}
      {cell.kind === "chart" && cell.chart && <ChartCell chart={cell.chart} cells={cells} results={results} onChange={(chart) => onChange({ chart })} />}
    </section>
  );
}

function SqlCell({ cell, run, onChange, onRun, onChart }: {
  cell: NotebookCell;
  run: Run | undefined;
  onChange: (patch: Partial<NotebookCell>) => void;
  onRun: () => void;
  onChart: (chart: NotebookChart) => void;
}) {
  const sql = cell.sql ?? "";
  const keys = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      onRun();
    }
  };
  const rows = Math.min(24, Math.max(3, sql.split("\n").length + 1));
  return (
    <>
      <textarea className="nb-sql" aria-label="SQL" spellCheck={false} rows={rows} value={sql}
        placeholder="SELECT … FROM financials WHERE company_id = '…'" onChange={(e) => onChange({ sql: e.target.value })} onKeyDown={keys} />
      {run?.state === "refused" && <Refusal refusal={run.refusal} sql={sql} />}
      {run?.state === "failed" && <p className="nb-refusal" role="alert"><b>Couldn't run it:</b> {ended(run.message)}</p>}
      {run?.state === "done" && <ResultView result={run.result} at={run.at} onChart={() => {
        const chart = defaultChart(run.result, cell.id);
        if (chart) onChart(chart);
        else toast({ text: "Nothing to chart: the result needs a date or text column and a column of numbers." });
      }} />}
    </>
  );
}

/** The API's words as they are (they may start with a name: pg_class, set_config), ended. */
function ended(s: string): string {
  return /[.!?)]$/.test(s) ? s : `${s}.`;
}

function Refusal({ refusal, sql }: { refusal: SqlRefusal; sql: string }) {
  const where = refusal.position !== undefined ? lineOf(sql, refusal.position) : null;
  return (
    <p className="nb-refusal" role="alert">
      <b>{REFUSAL_WORDS[refusal.code] ?? "It didn't run"}:</b> {ended(refusal.detail)}
      {where && <> Line {where.line}, column {where.column}.</>}
    </p>
  );
}

function ResultView({ result, at, onChart }: { result: SqlResult; at: string; onChart: () => void }) {
  const [all, setAll] = useState(false);
  const rows = all ? result.rows : result.rows.slice(0, SHOWN_ROWS);
  const exportCsv = () => download(`tributary-query-${at.slice(0, 10)}.csv`, new Blob([csvOf(result)], { type: "text/csv;charset=utf-8" }));
  if (!result.columns.length) return <p className="muted small">The query returned no columns.</p>;
  return (
    <div className="nb-result">
      <div className="row nb-result-bar">
        <span className="muted small grow">
          {result.truncated === "rows" ? `The first ${result.max_rows.toLocaleString("en-US")} rows` : `${result.row_count.toLocaleString("en-US")} ${result.row_count === 1 ? "row" : "rows"}`}
          , read {localTime(at)}
        </span>
        <button className="btn sm" onClick={onChart}>Chart it</button>
        <button className="btn sm" onClick={exportCsv} disabled={!result.rows.length}>Export CSV</button>
      </div>
      {result.rows.length ? (
        <TableScroll label="Query result">
          <table className="t nb-table">
            <thead>
              <tr>
                {result.columns.map((c, i) => <th key={i} className={isNumberColumn(c) ? "n" : undefined} scope="col">{c.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r}>
                  {row.map((v, i) => (
                    <td key={i} className={isNumberColumn(result.columns[i]!) ? "n" : v === null ? "nb-null" : undefined}>{v === null ? "" : shown(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      ) : (
        <p className="muted small">No rows: nothing matched, or nothing you can read did.</p>
      )}
      {!all && result.rows.length > SHOWN_ROWS && (
        <button className="btn sm ghost" onClick={() => setAll(true)}>Show all {result.rows.length.toLocaleString("en-US")} rows</button>
      )}
    </div>
  );
}

function TextCell({ cell, editing, onChange, onDone }: { cell: NotebookCell; editing: boolean; onChange: (patch: Partial<NotebookCell>) => void; onDone: () => void }) {
  const text = cell.text ?? "";
  if (editing || !text.trim()) {
    return (
      <textarea className="nb-text-edit field" aria-label="Note (Markdown)" rows={Math.min(16, Math.max(3, text.split("\n").length + 1))} value={text}
        placeholder="What this notebook shows, in Markdown" onChange={(e) => onChange({ text: e.target.value })}
        onKeyDown={(e) => { if (e.key === "Escape") onDone(); }} />
    );
  }
  // the user's own text: renderMarkdown (sanitized), never the assistant's renderer
  return <div className="nb-note answer report-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
}

function ChartCell({ chart, cells, results, onChange }: {
  chart: NotebookChart;
  cells: NotebookCell[];
  results: Record<string, SqlResult>;
  onChange: (chart: NotebookChart) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const queries = cells.filter((c) => c.kind === "sql");
  const result = results[chart.source] ?? null;
  const drawn = useMemo(() => (result ? chartSeries(result, chart) : null), [result, chart]);
  const numeric = result ? result.columns.filter(isNumberColumn) : [];
  const toggleY = (name: string) => {
    const has = chart.y.includes(name);
    if (!has && chart.y.length >= MAX_SERIES) return toast({ text: `A chart draws at most ${MAX_SERIES} columns.` });
    onChange({ ...chart, y: has ? chart.y.filter((y) => y !== name) : [...chart.y, name] });
  };
  return (
    <div className="nb-chart">
      <div className="row wrap nb-chart-controls">
        <label>
          <span>Over</span>
          <select className="field" value={chart.source} onChange={(e) => onChange({ ...chart, source: e.target.value })}>
            {!queries.some((q) => q.id === chart.source) && <option value={chart.source}>pick a query</option>}
            {queries.map((q) => <option key={q.id} value={q.id}>{queryName(cells, q.id)}</option>)}
          </select>
        </label>
        <label>
          <span>Across</span>
          <select className="field" value={chart.x} onChange={(e) => onChange({ ...chart, x: e.target.value })} disabled={!result}>
            {!result?.columns.some((c) => c.name === chart.x) && <option value={chart.x}>{chart.x || "pick a column"}</option>}
            {result?.columns.map((c, i) => <option key={i} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <Choices label="Drawn as" value={chart.type} options={[["line", "Lines"], ["bar", "Bars"]]} set={(type) => onChange({ ...chart, type })} />
      </div>
      {result && (
        <fieldset className="nb-ys">
          <legend>Columns drawn</legend>
          {numeric.length ? numeric.map((c, i) => (
            <label key={i} className="check">
              <input type="checkbox" checked={chart.y.includes(c.name)} onChange={() => toggleY(c.name)} /> {c.name}
            </label>
          )) : <span className="muted small">The result has no columns of numbers.</span>}
        </fieldset>
      )}
      {!result ? (
        <p className="muted small">Run {queryName(cells, chart.source)} to draw it. Charts are drawn from results in this page, never saved ones.</p>
      ) : drawn?.problem ? (
        <p className="muted small">{drawn.problem}</p>
      ) : drawn ? (
        <LayerChart series={drawn.series} title={chart.title ?? ""} footnote={["Drawn from your query's result."]} svgRef={svg} notes={false} />
      ) : null}
    </div>
  );
}

function SchemaBrowser({ schema, onInsert, onExample }: { schema: SqlSchema; onInsert: (name: string) => void; onExample: (ex: { title: string; sql: string }) => void }) {
  const views = schema.objects.filter((o) => o.kind === "view");
  const functions = schema.objects.filter((o) => o.kind === "function");
  return (
    <div className="nb-schema">
      <h2 className="nb-side-h">Examples</h2>
      <ul className="nb-examples">
        {schema.examples.map((ex) => (
          <li key={ex.title}>
            <button className="link" onClick={() => onExample(ex)} title="Add it as a query and run it">{ex.title}</button>
          </li>
        ))}
      </ul>
      <h2 className="nb-side-h">Views</h2>
      {views.map((v) => (
        <details key={v.name} className="nb-object">
          <summary><b>{v.name}</b></summary>
          {v.description && <p className="small">{v.description}</p>}
          <dl className="nb-columns">
            {v.columns.map((c) => (
              <div key={c.name}>
                <dt>{c.name} <span className="faint">{c.type}</span></dt>
                {c.description && <dd>{c.description}</dd>}
              </div>
            ))}
          </dl>
          <button className="btn sm" onClick={() => onInsert(v.name)}>Query it</button>
        </details>
      ))}
      <h2 className="nb-side-h">Functions</h2>
      {functions.map((f) => (
        <details key={f.name} className="nb-object">
          <summary><b>{f.name}</b>({f.arguments})</summary>
          {f.description && <p className="small">{f.description}</p>}
          <dl className="nb-columns">
            {f.columns.map((c) => <div key={c.name}><dt>{c.name} <span className="faint">{c.type}</span></dt></div>)}
          </dl>
        </details>
      ))}
      <details className="how-to-read nb-rules">
        <summary>What runs here</summary>
        <p className="foot">
          One SELECT at a time over these views and functions, read only, as you: the same companies and tiers you can read
          everywhere else, and only your own datasets. Amounts are in each company's currency (the unit column): compare
          them within one currency. Up to {schema.limits.seconds} seconds and {schema.limits.rows.toLocaleString("en-US")} rows a
          query; a query that would read too much is refused before it runs.
        </p>
      </details>
    </div>
  );
}
