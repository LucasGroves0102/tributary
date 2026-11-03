import { useEffect, useId, useMemo, useState } from "react";

import { ApiError, openItem } from "../api";
import { ErrorPanel, Skeleton } from "../components/States";
import { href } from "../route";
import type { SavedItemFull } from "../types";
import { ITEM_NAME_MAX, nameProblem } from "../workspaces/model";
import { useEditorKeys } from "../workspaces/useEditor";
import type { Saved } from "../workspaces/useSaved";
import {
  defaultAssumptions,
  GROWTH_RANGE,
  historyStats,
  MARGIN_RANGE,
  MAX_HISTORY,
  MAX_YEARS,
  MIN_HISTORY,
  modelState,
  NOTE_MAX,
  parsePercent,
  resizeAssumptions,
  restoreModel,
  statLabel,
  type Assumption,
  type ModelState,
} from "./model";
import { HistoryState, ModelView } from "./ModelView";
import { useHistory } from "./useHistory";

interface Props {
  token: string;
  itemId: string;
  saved: Saved;
  /** company id → ticker or name */
  label: (companyId: string) => string;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; item: SavedItemFull; model: ModelState }
  | { state: "error"; message: string; notFound: boolean };

/** One assumption as typed (strings, so "-" or "12." can be typed on the way to a number). */
interface Draft {
  growth: string;
  margin: string;
  note: string;
}

const toDraft = (a: Assumption): Draft => ({ growth: String(a.growth), margin: String(a.margin), note: a.note ?? "" });

/** The typed assumptions as numbers, or null while any is out of range or not a number. */
export function parseDrafts(drafts: Draft[]): Assumption[] | null {
  const out: Assumption[] = [];
  for (const d of drafts) {
    const growth = parsePercent(d.growth, GROWTH_RANGE);
    const margin = parsePercent(d.margin, MARGIN_RANGE);
    if (growth === null || margin === null) return null;
    out.push({ growth, margin, ...(d.note.trim() ? { note: d.note.trim() } : {}) });
  }
  return out;
}

/** A revenue model: the company's fiscal-year history (read now, as the viewer) and the user's
 * growth and operating-margin assumptions per forecast year, each next to its historical
 * average; the forecast drawn dashed after the history, and the years as a table. */
export function ModelPage({ token, itemId, saved, label }: Props) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let live = true;
    openItem(token, itemId)
      .then((item) => {
        if (!live) return;
        const model = item.kind === "model" ? restoreModel(item.state) : null;
        setLoaded(model ? { state: "ok", item, model } : { state: "error", message: "This item isn't a model.", notFound: false });
      })
      .catch((e) => live && setLoaded({
        state: "error",
        message: e instanceof ApiError ? e.message : "the Query API didn't answer",
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, itemId, retry]);

  if (loaded.state === "loading") return <div className="page"><Skeleton lines={8} label="Loading the model" /></div>;
  if (loaded.state === "error") {
    return (
      <div className="page">
        {loaded.notFound ? (
          <p className="empty">This model doesn't exist, or isn't yours. Models are visible only to their owner.</p>
        ) : (
          <ErrorPanel what="the model" detail={loaded.message} onRetry={() => setRetry((r) => r + 1)} />
        )}
      </div>
    );
  }
  return <ModelEditor key={loaded.item.item_id} token={token} item={loaded.item} initial={loaded.model} saved={saved} label={label} />;
}

function ModelEditor({ token, item, initial, saved, label }: {
  token: string;
  item: SavedItemFull;
  initial: ModelState;
  saved: Saved;
  label: (companyId: string) => string;
}) {
  const id = useId();
  const company = label(initial.companyId);
  const history = useHistory(token, initial.companyId);
  const [name, setName] = useState(item.name);
  const [historyYears, setHistoryYears] = useState(initial.historyYears);
  const [drafts, setDrafts] = useState<Draft[]>(() => initial.assumptions.map(toDraft));
  const [savedAs, setSavedAs] = useState(() => JSON.stringify({ name: item.name, state: modelState(initial) }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const assumptions = useMemo(() => parseDrafts(drafts), [drafts]);
  const stats = useMemo(() => (history.state === "ok" ? historyStats(history.years) : null), [history]);
  const current = assumptions ? JSON.stringify({ name: name.trim(), state: modelState({ companyId: initial.companyId, historyYears, assumptions }) }) : null;
  const dirty = current !== savedAs;
  const nameError = nameProblem(name, ITEM_NAME_MAX);

  const save = async () => {
    if (!assumptions || nameError || busy || !dirty) return;
    setBusy(true);
    setError(null);
    try {
      const state = modelState({ companyId: initial.companyId, historyYears, assumptions });
      const next = await saved.update(item.item_id, { name, state });
      setSavedAs(JSON.stringify({ name: next.name, state: modelState(restoreModel(next.state) ?? { ...initial, assumptions }) }));
      setName(next.name);
    } catch (e) {
      setError(`Couldn't save: ${e instanceof ApiError ? e.message : "the Query API didn't answer"}.`);
    } finally {
      setBusy(false);
    }
  };
  useEditorKeys(dirty, () => void save());

  const setDraft = (i: number, patch: Partial<Draft>) => setDrafts((d) => d.map((x, k) => (k === i ? { ...x, ...patch } : x)));
  const resize = (n: number) =>
    setDrafts((d) => {
      const parsed = parseDrafts(d);
      if (!parsed) return d.length >= n ? d.slice(0, n) : [...d, ...Array.from({ length: n - d.length }, () => ({ ...d.at(-1)!, note: "" }))];
      const notes = d.map((x) => x.note);
      return resizeAssumptions(parsed, n).map((a, k) => ({ ...toDraft(a), note: notes[k] ?? "" }));
    });
  const growthHint = stats ? statLabel(stats.cagr, stats.cagrYears, "CAGR") : "history loading";
  const marginHint = stats ? statLabel(stats.avgMargin, stats.marginYears, "avg") : "history loading";

  return (
    <div className="page model-page">
      <div className="row wrap editor-bar">
        <a className="btn sm ghost" href={href({ view: "workspace", workspaceId: item.workspace_id })}>
          ← {item.workspace_name ?? "Workspace"}
        </a>
        <span className="grow" />
        <span className={dirty ? "dirty small" : "faint small"} role="status">
          {dirty ? "● unsaved changes" : "saved"}
        </span>
        <button className="btn primary" onClick={() => void save()} disabled={!dirty || busy || !assumptions || !!nameError}
          title={assumptions ? "Save (Ctrl+S)" : "Fix the highlighted assumptions first"}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {error && <p className="err small" role="alert">{error}</p>}
      <div className="editor-title">
        <label className="visually-hidden" htmlFor={`${id}-name`}>Model name</label>
        <input id={`${id}-name`} className="field title-input" value={name} maxLength={ITEM_NAME_MAX}
          aria-invalid={!!nameError} onChange={(e) => setName(e.target.value)} />
        <p className="muted small">
          Revenue-first model of{" "}
          <a href={href({ view: "company", companyId: initial.companyId })}>{company}</a>: fiscal-year history from its
          filings, then your growth and operating-margin assumptions (operating income = revenue × margin).
          {nameError && <span className="err"> {nameError}</span>}
        </p>
      </div>

      <section className="card" aria-labelledby={`${id}-a`}>
        <div className="card-h">
          <h2 id={`${id}-a`}>Assumptions</h2>
          <span className="sub">percent per fiscal year; each next to its history</span>
          <span className="grow" />
          <label className="small" htmlFor={`${id}-years`}>Forecast years</label>
          <select id={`${id}-years`} className="field narrow" value={drafts.length} onChange={(e) => resize(Number(e.target.value))}>
            {Array.from({ length: MAX_YEARS }, (_, k) => k + 1).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <label className="small" htmlFor={`${id}-hist`}>History years</label>
          <select id={`${id}-hist`} className="field narrow" value={historyYears} onChange={(e) => setHistoryYears(Number(e.target.value))}>
            {Array.from({ length: MAX_HISTORY - MIN_HISTORY + 1 }, (_, k) => k + MIN_HISTORY).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button className="btn sm" disabled={!stats}
            title="Set every year to the historical revenue CAGR and average operating margin"
            onClick={() => stats && setDrafts((d) => defaultAssumptions(stats, d.length).map((a, k) => ({ ...toDraft(a), note: d[k]?.note ?? "" })))}>
            Reset to history
          </button>
        </div>
        <div className="tw">
          <table className="t assumptions">
            <caption className="visually-hidden">Assumptions per forecast year</caption>
            <thead>
              <tr>
                <th className="first" scope="col">Year</th>
                <th scope="col">Revenue growth</th>
                <th scope="col">Operating margin</th>
                <th scope="col">Why (note)</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((d, i) => {
                const base = history.state === "ok" ? [...history.years].reverse().find((y) => y.revenue !== null)?.fy : undefined;
                const year = base ? `FY${base + i + 1}E` : `Year ${i + 1}`;
                const badGrowth = parsePercent(d.growth, GROWTH_RANGE) === null;
                const badMargin = parsePercent(d.margin, MARGIN_RANGE) === null;
                return (
                  <tr key={i}>
                    <th className="first" scope="row">{year}</th>
                    <td>
                      <span className="pct-in">
                        <input className="field" inputMode="decimal" value={d.growth} aria-label={`${year} revenue growth, percent`}
                          aria-invalid={badGrowth} aria-describedby={`${id}-g${i}`} onChange={(e) => setDraft(i, { growth: e.target.value })} />
                        <span aria-hidden="true">%</span>
                      </span>
                      <span id={`${id}-g${i}`} className={badGrowth ? "err small" : "faint small"}>
                        {badGrowth ? `${GROWTH_RANGE[0]} to ${GROWTH_RANGE[1]}` : growthHint}
                      </span>
                    </td>
                    <td>
                      <span className="pct-in">
                        <input className="field" inputMode="decimal" value={d.margin} aria-label={`${year} operating margin, percent`}
                          aria-invalid={badMargin} aria-describedby={`${id}-m${i}`} onChange={(e) => setDraft(i, { margin: e.target.value })} />
                        <span aria-hidden="true">%</span>
                      </span>
                      <span id={`${id}-m${i}`} className={badMargin ? "err small" : "faint small"}>
                        {badMargin ? `up to ${MARGIN_RANGE[1]}` : marginHint}
                      </span>
                    </td>
                    <td className="note-cell">
                      <input className="field" value={d.note} maxLength={NOTE_MAX} placeholder="e.g. new product ramps"
                        aria-label={`${year} note`} onChange={(e) => setDraft(i, { note: e.target.value })} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card pad" aria-label="History and forecast">
        <HistoryState history={history} company={company} render={(years, asOf) => (
          <ModelView history={years} historyYears={historyYears} assumptions={assumptions} company={company} asOf={asOf} />
        )} />
      </section>
    </div>
  );
}
