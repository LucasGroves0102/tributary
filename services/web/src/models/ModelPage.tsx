import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";

import { ApiError, earnings as fetchEarnings, failureReason, itemVersion, itemVersions, openItem, refusedByOlderApi } from "../api";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { day, localDay } from "../format";
import { href } from "../route";
import type { EarningsResponse, ItemVersion, SavedItemFull } from "../types";
import { ITEM_NAME_MAX, nameProblem } from "../workspaces/model";
import { useEditorKeys } from "../workspaces/useEditor";
import type { Saved } from "../workspaces/useSaved";
import {
  adjustmentProblem,
  fromPreview,
  parseAdjustments,
  parseDrafts,
  parsePriceDraft,
  resizeDrafts,
  toAdjustmentDraft,
  toDraft,
  type AdjustmentDraft,
  type Draft,
  type PriceDraft,
} from "./drafts";
import {
  ADJUSTMENT_NAME_MAX,
  builtFromQuarters,
  evBlank,
  formatCell,
  GROWTH_RANGE,
  guidanceYear,
  MARGIN_RANGE,
  MAX_ADJUSTMENTS,
  MAX_HISTORY,
  MAX_YEARS,
  MIN_HISTORY,
  modelState,
  NOTE_MAX,
  parsePercent,
  parseRate,
  quartersLabel,
  RATE_LABELS,
  RATE_RANGES,
  RATES,
  remainderLabel,
  restoreModel,
  runBase,
  SCENARIO_LABELS,
  SCENARIOS,
  type Adjustment,
  type Assumption,
  type Guidance,
  type ModelRun,
  type ModelState,
  type Price,
  type Rate,
  type Scenario,
} from "./model";
import { ModelView, RunView } from "./ModelView";
import { defaultRules } from "./rules";
import { useModelRun } from "./useModelRun";

interface Props {
  token: string;
  itemId: string;
  saved: Saved;
  /** company id → ticker or name */
  label: (companyId: string) => string;
  /** inside an analysis project: no "← workspace" link (the project's header is the context) */
  embedded?: boolean;
  /** the visible editor: only it answers Ctrl+S (a project keeps hidden steps mounted) */
  active?: boolean;
  /** after a save (a project refreshes its items) */
  onSaved?: () => void;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; item: SavedItemFull; model: ModelState }
  | { state: "error"; message: string; notFound: boolean };

/** The typed cases: base always, bear / bull once added. */
type Cases = { base: Draft[]; bear: Draft[] | null; bull: Draft[] | null };

/** Everything the editor holds as typed, so a version can be restored and undone in one step. */
interface Editing {
  name: string;
  historyYears: number;
  cases: Cases;
  ytd: boolean;
  adjustments: AdjustmentDraft[];
  price: PriceDraft;
}

function editingOf(name: string, m: ModelState): Editing {
  return {
    name,
    historyYears: m.historyYears,
    cases: {
      base: m.assumptions.map(toDraft),
      bear: m.scenarios?.bear?.map(toDraft) ?? null,
      bull: m.scenarios?.bull?.map(toDraft) ?? null,
    },
    ytd: !!m.ytd,
    adjustments: (m.adjustments ?? []).map(toAdjustmentDraft),
    price: m.price ? { value: String(m.price.value), date: m.price.date } : { value: "", date: "" },
  };
}

/** A model: the company's history and current year (read now, by the Query API, as the viewer),
 * the user's assumptions per forecast year and case, each next to its default and the rule
 * behind it, one-off adjustments, the company's latest outlook as a quoted reference, the user's
 * own price; then the chart and the one table with bear, base and bull side by side. A tab picks
 * the case being edited. */
export function ModelPage({ token, itemId, saved, label, embedded, active, onSaved }: Props) {
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
        message: failureReason(e),
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, itemId, retry]);

  const wrap = embedded ? "model-page embedded" : "page";
  if (loaded.state === "loading") return <div className={wrap}><Skeleton lines={8} label="Loading the model" /></div>;
  if (loaded.state === "error") {
    return (
      <div className={wrap}>
        {loaded.notFound ? (
          <p className="empty">This model doesn't exist, or isn't yours. Models are visible only to their owner.</p>
        ) : (
          <ErrorPanel what="the model" detail={loaded.message} onRetry={() => setRetry((r) => r + 1)} />
        )}
      </div>
    );
  }
  return (
    <ModelEditor key={loaded.item.item_id} token={token} item={loaded.item} initial={loaded.model} saved={saved} label={label}
      embedded={embedded} active={active} onSaved={onSaved} />
  );
}

function ModelEditor({ token, item, initial, saved, label, embedded = false, active = true, onSaved }: {
  token: string;
  item: SavedItemFull;
  initial: ModelState;
  saved: Saved;
  label: (companyId: string) => string;
  embedded?: boolean;
  active?: boolean;
  onSaved?: () => void;
}) {
  const id = useId();
  const company = label(initial.companyId);
  const [ed, setEd] = useState<Editing>(() => editingOf(item.name, initial));
  const [sel, setSel] = useState<Scenario>("base");
  const [savedAs, setSavedAs] = useState(() => JSON.stringify({ name: item.name, state: modelState(initial) }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; undo?: Editing; error?: boolean } | null>(null);
  const [versions, setVersions] = useState<{ state: "loading" } | { state: "ok"; list: ItemVersion[] } | { state: "error"; message: string } | null>(null);
  const [earnings, setEarnings] = useState<EarningsResponse | null>(null);
  const { name, historyYears, cases } = ed;
  const set = (patch: Partial<Editing>) => setEd((e) => ({ ...e, ...patch }));

  useEffect(() => {
    let live = true;
    // F1's earnings view: the latest release's outlook, a reference beside its year (an older
    // API, or none, shows nothing)
    fetchEarnings(token, initial.companyId, 4).then((r) => live && setEarnings(r)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token, initial.companyId]);

  const parsed = useMemo(() => ({
    base: parseDrafts(cases.base),
    bear: cases.bear ? parseDrafts(cases.bear) : null,
    bull: cases.bull ? parseDrafts(cases.bull) : null,
  }), [cases]);
  const adjustments = useMemo(() => parseAdjustments(ed.adjustments), [ed.adjustments]);
  const price = useMemo(() => parsePriceDraft(ed.price), [ed.price]);
  // every case, the adjustments and the price must read before anything is saved
  const valid = !!parsed.base && (!cases.bear || !!parsed.bear) && (!cases.bull || !!parsed.bull) && !!adjustments && price !== null;
  const hasCases = !!cases.bear || !!cases.bull;
  const build = (p: { base: Assumption[]; bear?: Assumption[] | null; bull?: Assumption[] | null }, adj: Adjustment[], pr: Price | undefined): ModelState => ({
    companyId: initial.companyId,
    historyYears,
    assumptions: p.base,
    ...(p.bear || p.bull ? { scenarios: { ...(p.bear ? { bear: p.bear } : {}), ...(p.bull ? { bull: p.bull } : {}) } } : {}),
    ...(ed.ytd ? { ytd: true } : {}),
    ...(adj.length ? { adjustments: adj } : {}),
    ...(pr ? { price: pr } : {}),
  });
  const state = valid ? build({ base: parsed.base!, bear: parsed.bear, bull: parsed.bull }, adjustments!, price ?? undefined) : null;
  const dirty = (state ? JSON.stringify({ name: name.trim(), state: modelState(state) }) : null) !== savedAs;
  const nameError = nameProblem(name, ITEM_NAME_MAX);

  // a case that doesn't read right now keeps its last valid numbers on the chart and in the
  // table (marked), instead of the view falling back to another case (#98); the same for the
  // adjustments and the price
  const lastValid = useRef<{ cases: Partial<Record<Scenario, Assumption[]>>; adjustments: Adjustment[]; price: Price | undefined }>(
    { cases: {}, adjustments: initial.adjustments ?? [], price: initial.price });
  for (const s of SCENARIOS) {
    const p = parsed[s];
    if (p) lastValid.current.cases[s] = p;
    else if (s !== "base" && !cases[s]) delete lastValid.current.cases[s];
  }
  if (adjustments) lastValid.current.adjustments = adjustments;
  if (price !== null) lastValid.current.price = price;
  const shownCase = (s: Scenario) => parsed[s] ?? (s === "base" || cases[s] ? lastValid.current.cases[s] ?? null : null);
  const runState = useMemo(() => {
    const base = shownCase("base");
    if (!base) return null;
    return modelState(build({ base, bear: shownCase("bear"), bull: shownCase("bull") }, lastValid.current.adjustments, lastValid.current.price));
  }, [JSON.stringify([cases, ed.adjustments, ed.price, ed.ytd, historyYears])]);
  const { run, pending, retry } = useModelRun(token, runState);
  const ok = run.state === "ok" ? run.run : null;
  const stale = SCENARIOS.filter((s) => (s === "base" || cases[s]) && !parsed[s]);

  const drafts = cases[sel] ?? cases.base;
  const runCase = ok?.cases[sel];
  const defaults = runCase?.defaults ?? null;
  const base = ok ? runBase(ok) : null;
  const fromQuarters = ok ? builtFromQuarters(ok) : false;
  const cur = ok?.current ?? null;
  const guidance = useMemo(() => guidanceYear(earnings, ok), [earnings, ok]);
  const tableCases: Scenario[] = hasCases ? ["bear", "base", "bull"].filter((s) => s === "base" || cases[s as Scenario]) as Scenario[]
    : sel === "base" ? ["base"] : ["base", sel];

  const save = async () => {
    if (!state || nameError || busy || !dirty) return;
    setBusy(true);
    setError(null);
    try {
      const next = await saved.update(item.item_id, { name, state: modelState(state) });
      setSavedAs(JSON.stringify({ name: next.name, state: modelState(restoreModel(next.state) ?? state) }));
      set({ name: next.name });
      if (versions) setVersions(null);
      onSaved?.();
    } catch (e) {
      setError(refusedByOlderApi(e)
        ? "Couldn't save yet: the server is being updated to take this model's new fields. Try again in a minute; nothing is lost while this page stays open."
        : `Couldn't save: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };
  useEditorKeys(dirty, () => void save(), active);

  const setCases = (f: (c: Cases) => Cases) => setEd((e) => ({ ...e, cases: f(e.cases) }));
  const setDrafts = (f: (d: Draft[]) => Draft[]) => setCases((c) => ({ ...c, [sel]: f(c[sel] ?? c.base) }));
  const setDraft = (i: number, patch: Partial<Draft>) => setDrafts((d) => d.map((x, k) => (k === i ? { ...x, ...patch } : x)));
  // the number of forecast years is the model's: every case grows or shrinks with it
  const resize = (n: number) => setCases((c) => ({
    base: resizeDrafts(c.base, n),
    bear: c.bear && resizeDrafts(c.bear, n),
    bull: c.bull && resizeDrafts(c.bull, n),
  }));
  // from the run's preview of them, so only once the run has the base case as typed
  const addCases = () => {
    if (pending) return;
    const bear = ok?.cases.bear?.years;
    const bull = ok?.cases.bull?.years;
    if (!bear?.length || !bull?.length) return;
    setCases((c) => ({ ...c, bear: c.bear ?? fromPreview(bear), bull: c.bull ?? fromPreview(bull) }));
  };
  const removeCases = () => {
    setCases((c) => ({ ...c, bear: null, bull: null }));
    setSel("base");
  };
  const reset = () => {
    if (!defaults) return;
    setDrafts((d) => d.map((x, k) => {
      const dv = defaults[k] ?? defaults.at(-1)!;
      return { ...x, growth: String(dv.growth), margin: String(dv.margin), nonOperating: "", taxRate: "", shareChange: "", fcfMargin: "" };
    }));
  };
  // a model saved before F2 (year 1 a full-year guess): rebuild year 1 from the reported
  // quarters, each case's remainder the one its full year implies, so no year changes
  const rebuild = () => {
    if (!ok) return;
    const before = ed;
    setEd((e) => {
      const next: Cases = { ...e.cases };
      for (const s of SCENARIOS) {
        const list = e.cases[s];
        const implied = ok.cases[s]?.implied_remainder;
        if (!list?.length || !implied) continue;
        next[s] = list.map((d, k) => (k === 0 ? { ...d, growth: (Math.round(implied.growth * 10) / 10).toString(), margin: (Math.round(implied.margin * 10) / 10).toString() } : d));
      }
      return { ...e, ytd: true, cases: next };
    });
    setNotice({ text: `FY${cur?.fy} is now ${quartersLabel(cur?.quarters ?? 0)} as reported plus ${remainderLabel(cur?.quarters ?? 0)}, set to what your full-year numbers implied. Save to keep it.`, undo: before });
  };

  const showVersions = () => {
    if (versions) return setVersions(null);
    setVersions({ state: "loading" });
    itemVersions(token, item.item_id)
      .then((r) => setVersions({ state: "ok", list: r.versions }))
      .catch((e) => setVersions({ state: "error", message: failureReason(e) }));
  };
  const restoreVersion = async (v: ItemVersion) => {
    try {
      const got = await itemVersion(token, item.item_id, v.version_id);
      const m = restoreModel(got.state);
      if (!m) throw new ApiError("that version isn't a model", 422);
      const before = ed;
      setEd(editingOf(got.name, m));
      setSel("base");
      setVersions(null);
      setNotice({ text: `Restored the version saved ${localDay(v.saved_at, true)} (${m.assumptions.length} forecast years${m.scenarios ? ", bear and bull" : ""}). Save to keep it.`, undo: before });
    } catch (e) {
      setNotice({ text: `Couldn't read that version: ${failureReason(e)}.`, error: true });
    }
  };

  const caseLabel = SCENARIO_LABELS[sel].toLowerCase();
  const hint = (k: number, which: "growth" | "margin" | Rate) => {
    const v = defaults?.[k]?.[which];
    return v === undefined ? (run.state === "loading" ? "history loading" : "no default") : `default ${v.toFixed(1)}%`;
  };
  const rules = ok ? defaultRules(ok, sel) : [];
  const yearOf = (i: number) => (base ? base.fy + 1 + i : null);
  const yearLabel = (i: number) => (yearOf(i) ? `FY${yearOf(i)}E` : `Year ${i + 1}`);
  const cols = 3 + RATES.length + 1;
  const oldModel = !ed.ytd && !!cur?.quarters && !!ok?.cases.base?.implied_remainder;

  return (
    <div className={embedded ? "model-page embedded" : "page model-page"}>
      <div className="row wrap editor-bar">
        {!embedded && (
          <a className="btn sm ghost" href={href({ view: "workspace", workspaceId: item.workspace_id })}>
            ← {item.workspace_name ?? "Workspace"}
          </a>
        )}
        <span className="grow" />
        <span className={dirty ? "dirty small" : "faint small"} role="status">
          {dirty ? "● unsaved changes" : "saved"}
        </span>
        <button className="btn sm" onClick={showVersions} aria-expanded={!!versions} title="Earlier saved versions of this model">
          History
        </button>
        <button className="btn primary" onClick={() => void save()} disabled={!dirty || busy || !valid || !!nameError}
          title={valid ? "Save (Ctrl+S)" : "Fix the highlighted fields first (every case)"}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {error && <p className="err small" role="alert">{error}</p>}
      {notice && (
        <div className={notice.error ? "report-status error" : "report-status"} role={notice.error ? "alert" : "status"}>
          {notice.text}{" "}
          {notice.undo && (
            <button className="btn sm ghost" onClick={() => {
              setEd(notice.undo!);
              setNotice(null);
            }}>
              Undo
            </button>
          )}
          <button className="btn sm ghost" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}
      {versions && (
        <section className="card versions" aria-label="Earlier versions">
          <div className="card-h">
            <h3>Earlier versions</h3>
            <span className="sub">each save keeps the one it replaced, the newest 30</span>
            <span className="grow" />
            <button className="btn sm ghost" onClick={() => setVersions(null)}>Close</button>
          </div>
          {versions.state === "loading" ? <p className="faint small">Loading…</p>
            : versions.state === "error" ? <p className="err small" role="alert">Couldn't list them: {versions.message}.</p>
            : !versions.list.length ? <p className="empty">No earlier versions yet: each save from now keeps the one it replaces.</p>
            : (
              <ol className="version-list">
                {versions.list.map((v) => (
                  <li key={v.version_id}>
                    <span className="grow">
                      Saved {localDay(v.saved_at, true)}
                      <span className="faint"> · {v.years ?? "?"} forecast year{v.years === 1 ? "" : "s"}{v.cases ? ", bear and bull" : ""} · {v.name}</span>
                    </span>
                    <button className="btn sm" onClick={() => void restoreVersion(v)} title="Put this version in the editor; nothing is saved until you Save">
                      Restore
                    </button>
                  </li>
                ))}
              </ol>
            )}
        </section>
      )}
      <div className="editor-title">
        <label className="visually-hidden" htmlFor={`${id}-name`}>Model name</label>
        <input id={`${id}-name`} className="field title-input" value={name} maxLength={ITEM_NAME_MAX}
          aria-invalid={!!nameError} onChange={(e) => set({ name: e.target.value })} />
        <p className="muted small">
          Model of <a href={href({ view: "company", companyId: initial.companyId })}>{company}</a>: fiscal-year history and the
          current year's reported quarters from its filings, then your assumptions per forecast year: revenue growth and
          operating margin, and below operating income the interest, tax, share count and free cash flow rates (blank:
          Tributary's default from history).
          {nameError && <span className="err"> {nameError}</span>}
        </p>
      </div>

      {oldModel && (
        <div className="report-status model-upgrade" role="status">
          FY{cur!.fy} has {quartersLabel(cur!.quarters)} reported, but this model was saved before year-to-date actuals, so
          FY{cur!.fy}E is still a full-year guess.{" "}
          <button className="btn sm" onClick={rebuild}>Build FY{cur!.fy}E from {quartersLabel(cur!.quarters)} reported</button>
        </div>
      )}

      <section className="card" aria-labelledby={`${id}-a`}>
        <div className="card-h">
          <h2 id={`${id}-a`}>Assumptions</h2>
          <span className="seg scenario-switch" role="group" aria-label="Scenario">
            {SCENARIOS.map((s) => {
              const missing = s !== "base" && !cases[s];
              const bad = s === "base" ? !parsed.base : !!cases[s] && !parsed[s];
              return (
                <button key={s} className={sel === s ? "on" : undefined} aria-pressed={sel === s}
                  title={missing ? `No ${s} case yet` : bad ? "Some numbers in this case need fixing" : `Edit the ${s} case`}
                  onClick={() => setSel(s)}>
                  {SCENARIO_LABELS[s]}{bad ? " ⚠" : missing ? " +" : ""}
                </button>
              );
            })}
          </span>
          <span className="sub">percent per fiscal year; each next to its default</span>
          <span className="grow" />
          <label className="small" htmlFor={`${id}-years`}>Forecast years</label>
          <select id={`${id}-years`} className="field narrow" value={cases.base.length} onChange={(e) => resize(Number(e.target.value))}>
            {Array.from({ length: MAX_YEARS }, (_, k) => k + 1).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <label className="small" htmlFor={`${id}-hist`}>History years</label>
          <select id={`${id}-hist`} className="field narrow" value={historyYears} onChange={(e) => set({ historyYears: Number(e.target.value) })}>
            {Array.from({ length: MAX_HISTORY - MIN_HISTORY + 1 }, (_, k) => k + MIN_HISTORY).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button className="btn sm" disabled={!defaults || (sel !== "base" && !cases[sel])}
            title={`Set every year of the ${caseLabel} case to the defaults shown beside it`} onClick={reset}>
            Reset to defaults
          </button>
        </div>
        {sel !== "base" && !cases[sel] ? (
          <div className="card-b scenario-empty">
            <p>
              No {caseLabel} case yet. A bear and a bull case sit beside your base case: the same years, your numbers for
              what happens if it goes worse or better. Each starts from the base case {sel === "bear" ? "minus" : "plus"} half
              its growth (at least 5 points) and a fifth of its margin (at least 3), with the base case's other rates: the
              table below shows the {caseLabel} case as it would start. The thesis check reads the base case only.
            </p>
            <button className="btn primary" onClick={addCases} disabled={pending || !ok?.cases.bear?.years.length}
              title={pending || !ok ? "Waiting for the model to run with your latest numbers" : undefined}>
              Add bear and bull cases
            </button>
          </div>
        ) : (
          <>
            {rules.length > 0 && (
              <ul className="small muted model-rules" aria-label="How the defaults are set">
                {rules.map((r) => <li key={r.key}>{r.text}</li>)}
              </ul>
            )}
            {hasCases && (
              <p className="small muted scenario-note">
                Editing the <b>{caseLabel}</b> case. The chart and the table show all three side by side; Check my thesis checks the
                base case.{" "}
                <button className="link" onClick={removeCases}>Remove bear and bull</button>
              </p>
            )}
            <TableScroll label="Assumptions">
              <table className="t assumptions">
                <caption className="visually-hidden">Assumptions per forecast year, {caseLabel} case</caption>
                <thead>
                  <tr>
                    <th className="first" scope="col">Year</th>
                    <th scope="col">Revenue growth</th>
                    <th scope="col">Operating margin</th>
                    {RATES.map((r) => <th key={r} scope="col">{RATE_LABELS[r]}</th>)}
                    <th scope="col">Why (note)</th>
                  </tr>
                </thead>
                <tbody>
                  {drafts.map((d, i) => {
                    const year = yearLabel(i);
                    const remainder = i === 0 && fromQuarters && cur ? remainderLabel(cur.quarters) : null;
                    const which = sel === "base" ? "" : ` ${caseLabel} case`;
                    const what = remainder ? ` ${remainder}` : "";
                    const badGrowth = parsePercent(d.growth, GROWTH_RANGE) === null;
                    const badMargin = parsePercent(d.margin, MARGIN_RANGE) === null;
                    const g = guidance && guidance.fy === yearOf(i) ? guidance : guidance && i === drafts.length - 1 && yearOf(i)! < guidance.fy ? guidance : null;
                    return (
                      <Fragment key={`${sel}-${i}`}>
                        <tr>
                          <th className="first" scope="row">
                            {year}
                            {remainder && <span className="col-sub">{remainder} ({quartersLabel(cur!.quarters)} reported)</span>}
                          </th>
                          <td>
                            <span className="pct-in">
                              <input className="field" inputMode="decimal" value={d.growth} aria-invalid={badGrowth} aria-describedby={`${id}-g${i}`}
                                aria-label={`${year}${which}${what} revenue growth${remainder ? " over the same quarters a year earlier" : ""}, percent`}
                                onChange={(e) => setDraft(i, { growth: e.target.value })} />
                              <span aria-hidden="true">%</span>
                            </span>
                            <span id={`${id}-g${i}`} className={badGrowth ? "err small" : "faint small"}>
                              {badGrowth ? `${GROWTH_RANGE[0]} to ${GROWTH_RANGE[1]}` : hint(i, "growth")}
                            </span>
                          </td>
                          <td>
                            <span className="pct-in">
                              <input className="field" inputMode="decimal" value={d.margin} aria-invalid={badMargin} aria-describedby={`${id}-m${i}`}
                                aria-label={`${year}${which}${what} operating margin, percent`} onChange={(e) => setDraft(i, { margin: e.target.value })} />
                              <span aria-hidden="true">%</span>
                            </span>
                            <span id={`${id}-m${i}`} className={badMargin ? "err small" : "faint small"}>
                              {badMargin ? `up to ${MARGIN_RANGE[1]}` : hint(i, "margin")}
                            </span>
                          </td>
                          {RATES.map((r) => {
                            const bad = parseRate(d[r], RATE_RANGES[r]) === null;
                            const dv = defaults?.[i]?.[r];
                            return (
                              <td key={r}>
                                <span className="pct-in">
                                  <input className="field" inputMode="decimal" value={d[r]} aria-invalid={bad} aria-describedby={`${id}-${r}${i}`}
                                    placeholder={dv === undefined ? "" : dv.toFixed(1)}
                                    aria-label={`${year}${which} ${RATE_LABELS[r].toLowerCase()}, percent (blank: the default)`}
                                    onChange={(e) => setDraft(i, { [r]: e.target.value } as Partial<Draft>)} />
                                  <span aria-hidden="true">%</span>
                                </span>
                                <span id={`${id}-${r}${i}`} className={bad ? "err small" : "faint small"}>
                                  {bad ? `${RATE_RANGES[r][0]} to ${RATE_RANGES[r][1]}` : d[r].trim() ? hint(i, r) : "default (blank)"}
                                </span>
                              </td>
                            );
                          })}
                          <td className="note-cell">
                            <input className="field" value={d.note} maxLength={NOTE_MAX} placeholder="e.g. new product ramps"
                              aria-label={`${year}${which} note`} onChange={(e) => setDraft(i, { note: e.target.value })} />
                          </td>
                        </tr>
                        {g && <GuidanceRow guidance={g} cols={cols} beyond={g.fy !== yearOf(i)} />}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          </>
        )}
      </section>

      <AdjustmentsCard id={id} list={ed.adjustments} onChange={(adjustments) => set({ adjustments })}
        defaultFy={cur?.fy ?? (base ? base.fy + 1 : new Date().getFullYear())} defaultQuarter={Math.max(1, cur?.quarters ?? 1)} />

      <PriceCard id={id} price={ed.price} run={ok} onChange={(p) => set({ price: p })} />

      <section className="card pad" aria-label="History and forecast">
        {pending && run.state === "ok" && <p className="faint small model-pending" role="status">Updating…</p>}
        <RunView run={run} company={company} onRetry={retry} render={(r) => (
          <ModelView run={r} historyYears={historyYears} forecastYears={cases.base.length} company={company} cases={tableCases}
            preview={tableCases.filter((c) => c !== "base" && !cases[c])} stale={stale} selected={sel} />
        )} />
      </section>
    </div>
  );
}

/** The company's own outlook beside the forecast year it covers: quoted, cited, never an input. */
function GuidanceRow({ guidance, cols, beyond }: { guidance: Guidance; cols: number; beyond: boolean }) {
  const q = guidance.quarter;
  const r = q.release!;
  return (
    <tr className="guidance-row">
      <td colSpan={cols}>
        <details>
          <summary>
            The company's outlook {beyond ? `for FY${guidance.fy} (beyond this model's years)` : `for FY${guidance.fy}`}, from its earnings release
            for the quarter ended {day(q.period_end, true)} ({r.form} filed {day(r.filed, true)}): a reference to read, not an input
            ({q.outlook.length} passage{q.outlook.length === 1 ? "" : "s"}, quoted).
          </summary>
          {q.outlook.map((p, k) => (
            <blockquote key={k} className="guidance-quote">
              {p.text.split("\n").map((line, j) => <p key={j}>{line}</p>)}
              <cite>
                <a href={p.citation.document_url ?? p.citation.filing_url ?? r.filing_url ?? "#"} target="_blank" rel="noreferrer">
                  {p.citation.form} {p.citation.section_key ?? ""}, filed {day(p.citation.filed, true)}
                </a>
                {p.truncated && " (cut short)"}
              </cite>
            </blockquote>
          ))}
        </details>
      </td>
    </tr>
  );
}

/** The user's one-offs and acquired revenue: their figures, labelled as theirs. */
function AdjustmentsCard({ id, list, onChange, defaultFy, defaultQuarter }: {
  id: string;
  list: AdjustmentDraft[];
  onChange: (list: AdjustmentDraft[]) => void;
  defaultFy: number;
  defaultQuarter: number;
}) {
  const edit = (i: number, patch: Partial<AdjustmentDraft>) => onChange(list.map((a, k) => (k === i ? { ...a, ...patch } : a)));
  return (
    <section className="card" aria-labelledby={`${id}-adj`}>
      <div className="card-h">
        <h2 id={`${id}-adj`}>Adjustments</h2>
        <span className="sub">your figures, not the filings'</span>
        <span className="grow" />
        <button className="btn sm" disabled={list.length >= MAX_ADJUSTMENTS}
          onClick={() => onChange([...list, { name: "", kind: "one_off", fy: String(defaultFy), quarter: String(defaultQuarter), amount: "" }])}>
          Add an adjustment
        </button>
      </div>
      <p className="small muted card-b model-adj-note">
        A one-off is an amount in a quarter's operating income you take out (positive if it raised it, like a refund;
        negative for a charge): margins, growth, EPS and the defaults read the year without it, and the table shows it
        reported and adjusted. Acquired revenue comes out of organic growth only. Both are what you enter, nothing more.
      </p>
      {list.length > 0 && (
        <TableScroll label="Adjustments">
          <table className="t adjustments">
            <thead>
              <tr>
                <th className="first" scope="col">Name</th>
                <th scope="col">What</th>
                <th scope="col">Fiscal year</th>
                <th scope="col">Quarter</th>
                <th scope="col">Amount, $M</th>
                <th scope="col"><span className="visually-hidden">Remove</span></th>
              </tr>
            </thead>
            <tbody>
              {list.map((a, i) => {
                const problem = adjustmentProblem(a);
                return (
                  <tr key={i}>
                    <td className="first">
                      <input className="field" value={a.name} maxLength={ADJUSTMENT_NAME_MAX} placeholder="e.g. IEEPA tariff refunds"
                        aria-label={`Adjustment ${i + 1} name`} aria-invalid={!a.name.trim()} onChange={(e) => edit(i, { name: e.target.value })} />
                      {problem && <span className="err small">{problem}</span>}
                    </td>
                    <td>
                      <select className="field" value={a.kind} aria-label={`Adjustment ${i + 1}: what it is`}
                        onChange={(e) => edit(i, { kind: e.target.value as AdjustmentDraft["kind"] })}>
                        <option value="one_off">One-off in operating income</option>
                        <option value="acquired">Acquired revenue</option>
                      </select>
                    </td>
                    <td>
                      <input className="field narrow" inputMode="numeric" value={a.fy} aria-label={`Adjustment ${i + 1} fiscal year`}
                        onChange={(e) => edit(i, { fy: e.target.value })} />
                    </td>
                    <td>
                      <select className="field narrow" value={a.quarter} aria-label={`Adjustment ${i + 1} quarter`} onChange={(e) => edit(i, { quarter: e.target.value })}>
                        {["1", "2", "3", "4"].map((q) => <option key={q} value={q}>Q{q}</option>)}
                      </select>
                    </td>
                    <td>
                      <input className="field narrow" inputMode="decimal" value={a.amount} placeholder="46" aria-label={`Adjustment ${i + 1} amount, $ millions`}
                        aria-invalid={!!problem && /amount/.test(problem)} onChange={(e) => edit(i, { amount: e.target.value })} />
                    </td>
                    <td>
                      <button className="btn sm ghost" aria-label={`Remove adjustment ${i + 1}`} onClick={() => onChange(list.filter((_, k) => k !== i))}>×</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      )}
    </section>
  );
}

/** A price the user types and its date: market cap, EV and the multiples in the table. */
function PriceCard({ id, price, run, onChange }: { id: string; price: PriceDraft; run: ModelRun | null; onChange: (p: PriceDraft) => void }) {
  const parsed = parsePriceDraft(price);
  const v = run?.valuation ?? null;
  const shares = v?.shares_outstanding;
  const debt = v?.net_debt;
  return (
    <section className="card" aria-labelledby={`${id}-price`}>
      <div className="card-h">
        <h2 id={`${id}-price`}>Valuation at your price</h2>
        <span className="sub">your price, not market data</span>
      </div>
      <div className="card-b">
        <div className="row wrap price-in">
          <label className="small" htmlFor={`${id}-pv`}>Price per share, $</label>
          <input id={`${id}-pv`} className="field narrow" inputMode="decimal" value={price.value} placeholder="e.g. 150.00"
            aria-invalid={parsed === null && !!price.value.trim()} onChange={(e) => onChange({ ...price, value: e.target.value })} />
          <label className="small" htmlFor={`${id}-pd`}>on</label>
          <input id={`${id}-pd`} className="field narrow" type="date" value={price.date} aria-invalid={parsed === null && !!price.date}
            onChange={(e) => onChange({ ...price, date: e.target.value })} />
          {(price.value || price.date) && <button className="btn sm ghost" onClick={() => onChange({ value: "", date: "" })}>Clear</button>}
          {parsed === null && <span className="err small">a price above $0 and the day it's for</span>}
        </div>
        <p className="small muted">
          The price you type here is yours: Tributary doesn't fetch or keep market prices. It's saved with this model as typed
          and gives market cap, P/E (price over diluted EPS; the TTM's is market cap over TTM net income), EV / Revenue,
          EV / EBIT and FCF yield, on history and on each case, in the table below.
        </p>
        {v && v.market_cap !== null && (
          <p className="small model-valuation">
            Market cap {formatCell(v.market_cap, "USD")}: ${v.price.toFixed(2)} on {day(v.date, true)} × {formatCell(shares?.value ?? null, "shares")}{" "}
            {v.shares_basis === "outstanding" ? "shares outstanding" : "diluted shares (no shares outstanding in the filings)"}
            {shares?.period_end ? ` (${shares.citation?.form ?? "filing"} for ${day(shares.period_end, true)})` : ""}.{" "}
            {v.enterprise_value !== null
              ? <>Net debt {formatCell(debt?.value ?? null, "USD")} ({debt?.citation?.form ?? "filing"} for {day(debt?.period_end ?? "", true)}{debt?.status === "behind" ? ", an earlier quarter" : ""}): EV {formatCell(v.enterprise_value, "USD")}.</>
              : <span className="editor-warn">{evBlank(v)}, so EV / Revenue and EV / EBIT are blank.</span>}
          </p>
        )}
        {v?.note && <p className="small editor-warn">{v.note}.</p>}
      </div>
    </section>
  );
}
