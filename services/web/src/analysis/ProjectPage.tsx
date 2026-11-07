import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Readable } from "../access";
import { ApiError, expectedFilingsOf, failureReason, getItem, screener, updateProject, workspace as fetchWorkspace } from "../api";
import type { ChartState } from "../chart/model";
import { header, HEADER_COLUMNS } from "../company/data";
import { leftText, useAllowanceContext } from "../assistant/useAllowance";
import { ErrorPanel, Skeleton } from "../components/States";
import { day } from "../format";
import { defaultAssumptions, DEFAULT_HISTORY, DEFAULT_YEARS, historyStats, modelState } from "../models/model";
import { ModelPage } from "../models/ModelPage";
import { loadHistory } from "../models/useHistory";
import { go, href } from "../route";
import { textHash } from "../thesis/check";
import { CheckPanel } from "../thesis/CheckPanel";
import { thesisState } from "../thesis/model";
import { ThesisPage } from "../thesis/ThesisPage";
import { useNewerFiling } from "../thesis/useRevisit";
import { useThesisCheck } from "../thesis/useThesisCheck";
import { embedMarker } from "../thesis/model";
import type { ProjectState, ProjectStep, SavedWorkspaceFull, ScreenerRow } from "../types";
import { WorkspaceActions } from "../workspaces/Actions";
import type { Saved } from "../workspaces/useSaved";
import type { ThesisHandle, ThesisStatus } from "./bridge";
import { PinButton, PinContext, type Pinning } from "./Pin";
import {
  appendToSection,
  catalystLines,
  coreWritten,
  firstOpenStep,
  groupOf,
  GROUPS,
  hasOwnText,
  nextStep,
  restoreProject,
  sectionOf,
  stepDone,
  stepInfo,
  stepNumber,
  STEPS,
  thesisTemplate,
  toggleDone,
  autoDone,
  type Facts,
  type Group,
  type SectionKey,
} from "./project";
import { Business, Numbers, PeersStep, WhatChanged } from "./Steps";

interface Props {
  token: string;
  user: string | null;
  projectId: string;
  /** the step in the route (none: the first one not done) */
  step?: ProjectStep;
  /** a model or thesis of the project named in the route */
  itemId?: string;
  saved: Saved;
  /** companies whose financials the viewer can read (comps embeds) */
  readable: Readable;
  label: (companyId: string) => string;
  onOpenChart: (s: ChartState) => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm" | "q" | "yoy", title: string) => void;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; ws: SavedWorkspaceFull; project: ProjectState }
  | { state: "error"; message: string; notFound: boolean };

/** An analysis project: the company's header with tabs (Company · Charts · Model · Thesis ·
 * Monitor) and the guided steps in a rail, each step a pane that stays mounted once visited, so
 * switching is instant and nothing typed is lost (the thesis also keeps a browser draft). The
 * project is a workspace (app DB 012): its charts, models and thesis are ordinary items. */
export function ProjectPage(props: Props) {
  const { token, projectId } = props;
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [version, setVersion] = useState(0);
  const [retry, setRetry] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let live = true;
    fetchWorkspace(token, projectId)
      .then((ws) => {
        if (!live) return;
        const project = restoreProject(ws.project);
        setLoaded(project
          ? { state: "ok", ws, project }
          : { state: "error", message: "This workspace isn't an analysis project.", notFound: false });
      })
      .catch((e) => live && setLoaded((l) => l.state === "ok" ? l : {
        state: "error",
        message: failureReason(e),
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, projectId, version, retry]);

  if (loaded.state === "loading") return <div className="page"><Skeleton lines={8} label="Loading the analysis" /></div>;
  if (loaded.state === "error") {
    return (
      <div className="page">
        {loaded.notFound ? (
          <p className="empty">This analysis doesn't exist, or isn't yours. Projects are visible only to their owner. <a href={href({ view: "analysis" })}>Your analyses</a></p>
        ) : (
          <ErrorPanel what="the analysis" detail={loaded.message} onRetry={() => setRetry((r) => r + 1)} />
        )}
      </div>
    );
  }
  return <Project {...props} ws={loaded.ws} initial={loaded.project} reload={reload} />;
}

/** The company's identity for the header (public metadata, as on the company page). */
function useCompany(token: string, companyId: string): ScreenerRow | null {
  const [row, setRow] = useState<ScreenerRow | null>(null);
  useEffect(() => {
    let live = true;
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1 })
      .then((r) => live && setRow(r.rows.find((x) => x.company_id === companyId) ?? null))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token, companyId]);
  return row;
}

type Toast = { text: string; error?: boolean; thesis?: boolean } | null;

function Project({ token, user, projectId, step: asked, itemId, saved, readable, label, onOpenChart, onChartMany, ws, initial, reload }: Props & {
  ws: SavedWorkspaceFull;
  initial: ProjectState;
  reload: () => void;
}) {
  const [project, setProject] = useState(initial);
  const [notesStatus, setNotesStatus] = useState<"saved" | "saving" | "error" | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  const companyId = project.companyId;
  const row = useCompany(token, companyId);
  const h = row ? header(row) : null;
  const name = h?.name ?? label(companyId);
  const ticker = h?.tickers[0] ?? name;
  const access = row?.access ?? [];

  const charts = ws.items.filter((i) => i.kind === "chart");
  const models = ws.items.filter((i) => i.kind === "model");
  const thesis = ws.items.find((i) => i.kind === "thesis") ?? null;
  const thesisId = thesis?.item_id ?? null;
  const checks = useThesisCheck(token, thesisId);
  const [status, setStatus] = useState<ThesisStatus | null>(null);
  const thesisStatus = status && status.itemId === thesisId ? status : null;
  const bridge = useRef<ThesisHandle | null>(null);
  const [itemsVersion, setItemsVersion] = useState(0);
  const [thesisEpoch, setThesisEpoch] = useState(0);
  const [toast, setToast] = useState<Toast>(null);

  const facts: Facts = {
    charts: charts.length,
    models: models.length,
    thesisWritten: thesisStatus ? coreWritten(thesisStatus.saved) : false,
    checks: checks.history.length,
  };
  // without a step in the route: where the project stood when it was opened (not a moving target)
  const [opened] = useState(() => firstOpenStep(initial, facts));
  const step = asked ?? opened;
  const [visited, setVisited] = useState<Set<ProjectStep>>(() => new Set([step]));
  const [lastIn, setLastIn] = useState<Partial<Record<Group, ProjectStep>>>({});
  useEffect(() => {
    setVisited((v) => (v.has(step) ? v : new Set([...v, step])));
    setLastIn((l) => ({ ...l, [groupOf(step)]: step }));
  }, [step]);
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 8000);
    return () => window.clearTimeout(t);
  }, [toast]);

  /** Saves the project's state over (optimistic; a failure says so and keeps what's on screen). */
  const saveProject = useCallback(async (next: ProjectState, what: "notes" | "other" = "other") => {
    setProject(next);
    if (what === "notes") setNotesStatus("saving");
    try {
      const w = await updateProject(token, projectId, next);
      const back = restoreProject(w.project);
      if (back && what !== "notes") setProject(back);
      if (what === "notes") setNotesStatus("saved");
      setProjectError(null);
      saved.reload();
    } catch (e) {
      if (what === "notes") setNotesStatus("error");
      else setProjectError(`Couldn't save the project: ${failureReason(e)}.`);
    }
  }, [token, projectId, saved]);

  const itemsChanged = useCallback(() => {
    setItemsVersion((v) => v + 1);
    reload();
  }, [reload]);

  /** The project's thesis, from the template (Catalysts prefilled with expected filings), with
   * `first` already pinned into it when the thesis is created for a pin. */
  const createThesis = useCallback(async (first?: { section: SectionKey; block: string }) => {
    const expected = await expectedFilingsOf(token, companyId, 4).then((r) => r.expected).catch(() => []);
    let markdown = thesisTemplate({ company: ticker !== name ? `${name} (${ticker})` : name, catalysts: catalystLines(expected) });
    if (first) markdown = appendToSection(markdown, first.section, first.block);
    const item = await saved.create("thesis", `${ticker} thesis`.slice(0, 120), thesisState({ companyId, markdown }), { workspaceId: projectId });
    reload();
    return item;
  }, [token, companyId, ticker, name, saved, projectId, reload]);

  const pinning: Pinning = useMemo(() => ({
    pin: async (section, make, what) => {
      const where = sectionOf(section).heading;
      try {
        const block = await make();
        if (!thesisId) {
          await createThesis({ section, block });
          setToast({ text: `Started your thesis from the template and pinned the ${what} under ${where}.`, thesis: true });
          return;
        }
        const handle = bridge.current;
        if (handle && handle.itemId === thesisId) {
          const r = await handle.pin(section, block);
          setToast({
            text: r === "saved" ? `Pinned the ${what} under ${where} in your thesis.`
              : `Added the ${what} under ${where} to your unsaved thesis; save it on the Thesis tab.`,
            thesis: true,
          });
        } else {
          // the editor isn't loaded yet: write the saved text, then load the editor again
          const item = await getItem(token, thesisId);
          const md = typeof item.state.markdown === "string" ? item.state.markdown : "";
          await saved.update(thesisId, { state: { ...item.state, markdown: appendToSection(md, section, block) } });
          setThesisEpoch((n) => n + 1);
          setToast({ text: `Pinned the ${what} under ${where} in your thesis.`, thesis: true });
        }
        itemsChanged();
      } catch (e) {
        setToast({ text: `Couldn't pin it: ${e instanceof ApiError || e instanceof Error ? e.message : "try again"}.`, error: true });
      }
    },
  }), [thesisId, createThesis, token, saved, itemsChanged]);

  const goStep = (s: ProjectStep) => go({ view: "analysis", projectId, step: s });
  const doneCount = STEPS.filter((s) => stepDone(s.key, project, facts)).length;
  const modelId = (itemId && models.some((m) => m.item_id === itemId) ? itemId : models[0]?.item_id) ?? null;
  // the header row says what the viewer reads; until it arrives, ask (the API decides: 403 shows as no access)
  const canFilings = row ? access.includes("filings") : true;
  const common = { token, companyId, name, ticker, label };

  const pane = (key: ProjectStep, body: ReactNode) => {
    const info = stepInfo(key);
    const done = stepDone(key, project, facts);
    const auto = autoDone(key, project, facts);
    const next = nextStep(key);
    if (!visited.has(key) && !(key === "thesis" && thesisId)) return null;
    return (
      <section key={key} className="step-pane" hidden={step !== key} aria-labelledby={`step-${key}`}>
        <div className="step-head">
          <div className="grow">
            <span className="step-n">Step {stepNumber(key)} of {STEPS.length}</span>
            <h2 id={`step-${key}`}>{info.label}</h2>
            <p className="muted small">{info.why}</p>
          </div>
          <label className="chk step-done" title={auto ? "Done: its work is in the project" : "Mark this step done when you're happy with it"}>
            <input type="checkbox" checked={done} disabled={auto && !project.done.includes(key)}
              onChange={() => void saveProject(toggleDone(project, key))} />
            {done ? "Done" : "Mark done"}
          </label>
        </div>
        {body}
        {next && (
          <div className="step-next">
            <a className="btn" href={href({ view: "analysis", projectId, step: next })}>Next: {stepInfo(next).label} →</a>
          </div>
        )}
      </section>
    );
  };

  return (
    <PinContext.Provider value={pinning}>
      <div className="page analysis-project">
        <section className="card project-head">
          <div className="card-b co-head">
            <div className="grow">
              <div className="eyebrow"><a href={href({ view: "analysis" })}>Analysis</a> · {ws.name}</div>
              <div className="row wrap">
                <h1>{name}</h1>
                {h && <span className="mono muted">{[h.tickers.join(", "), h.exchanges.join(", ")].filter(Boolean).join(" · ")}</span>}
              </div>
              {h && (
                <div className="meta">
                  {[h.industry, h.fye && `fiscal year ends ${h.fye}`, project.peers.length ? `${project.peers.length} peer${project.peers.length === 1 ? "" : "s"}` : null]
                    .filter(Boolean).join(" · ")}
                </div>
              )}
            </div>
            <div className="project-progress" aria-label={`${doneCount} of ${STEPS.length} steps done`}>
              <span className="small muted">{doneCount} of {STEPS.length} steps done</span>
              <span className="bar" aria-hidden="true"><span className={`fill f${doneCount}`} /></span>
              <a className="btn sm ghost" href={href({ view: "company", companyId })}>Company page</a>
              <WorkspaceActions workspace={ws} saved={saved} what="analysis" onDeleted={() => go({ view: "analysis" })} />
            </div>
          </div>
          <nav className="tabs" aria-label="Project">
            {GROUPS.map((g) => {
              const target = lastIn[g.key] ?? g.steps[0]!;
              const on = groupOf(step) === g.key;
              return (
                <a key={g.key} href={href({ view: "analysis", projectId, step: target })} aria-current={on ? "page" : undefined}>
                  {g.label}
                  {g.key === "thesis" && thesisStatus?.dirty && <span className="dirty" title="Unsaved changes"> ●</span>}
                </a>
              );
            })}
          </nav>
        </section>
        {projectError && <div className="banner" role="alert">{projectError} <button className="btn sm ghost" onClick={() => setProjectError(null)}>Dismiss</button></div>}
        {toast && (
          <div className={toast.error ? "report-status error pin-toast" : "report-status pin-toast"} role={toast.error ? "alert" : "status"}>
            {toast.text}{" "}
            {toast.thesis && step !== "thesis" && <a href={href({ view: "analysis", projectId, step: "thesis" })}>Open the thesis</a>}{" "}
            <button className="btn sm ghost" onClick={() => setToast(null)}>Dismiss</button>
          </div>
        )}
        <div className="project-body">
          <nav className="step-rail" aria-label="Guided steps">
            <ol>
              {STEPS.map((s, i) => {
                const done = stepDone(s.key, project, facts);
                return (
                  <li key={s.key}>
                    <a href={href({ view: "analysis", projectId, step: s.key })} aria-current={step === s.key ? "step" : undefined}
                      className={done ? "done" : undefined}>
                      <span className="n" aria-hidden="true">{done ? "✓" : i + 1}</span>
                      <span className="grow">{s.label}</span>
                      <span className="visually-hidden">{done ? ", done" : ", not done"}</span>
                    </a>
                  </li>
                );
              })}
            </ol>
          </nav>
          <div className="project-main">
            {pane("business", (
              <Business {...common} project={project} notesStatus={notesStatus} canReadFilings={canFilings}
                onNotes={(notes) => void saveProject({ ...project, notes }, "notes")} />
            ))}
            {pane("numbers", (
              <Numbers {...common} saved={saved} projectId={projectId} charts={charts} onOpenChart={onOpenChart} onSaved={itemsChanged} />
            ))}
            {pane("changes", <WhatChanged {...common} canReadFilings={canFilings} />)}
            {pane("peers", (
              <PeersStep {...common} project={project} saved={saved} projectId={projectId} onSaved={itemsChanged}
                onChart={onChartMany} onPeers={(peers) => void saveProject({ ...project, peers })} />
            ))}
            {pane("model", (
              <ModelStep token={token} companyId={companyId} ticker={ticker} models={models} modelId={modelId} saved={saved}
                projectId={projectId} label={label} active={step === "model"} onChanged={itemsChanged} />
            ))}
            {pane("thesis", thesisId ? (
              <ThesisPage key={`${thesisId}-${thesisEpoch}`} token={token} itemId={thesisId} saved={saved} label={label} readable={readable}
                onOpenChart={onOpenChart} user={user} embedded active={step === "thesis"} bridge={bridge} onStatus={setStatus}
                itemsVersion={itemsVersion} checks={checks} />
            ) : (
              <StartThesis onStart={async () => {
                try {
                  await createThesis();
                } catch (e) {
                  setToast({ text: `Couldn't start the thesis: ${failureReason(e)}.`, error: true });
                }
              }} />
            ))}
            {pane("monitor", (
              <Monitor token={token} companyId={companyId} projectId={projectId} thesisId={thesisId} status={thesisStatus}
                checks={checks} label={label} onQuote={(q) => {
                  goStep("thesis");
                  window.setTimeout(() => bridge.current?.showQuote(q), 0);
                }} />
            ))}
          </div>
        </div>
      </div>
    </PinContext.Provider>
  );
}

function StartThesis({ onStart }: { onStart: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <section className="card start-thesis">
      <div className="card-b">
        <p>
          Your thesis starts from a template: <b>Core thesis</b> (3-5 sentences), <b>Variant perception</b> (what the market
          misses), <b>Pillars</b> with their evidence, <b>Catalysts</b> (the next expected filings are filled in), <b>Risks and
          mitigants</b>, <b>What would prove me wrong</b> and <b>KPIs to watch</b>, each with a line of guidance you can delete.
          It's plain Markdown: change anything.
        </p>
        <p className="muted small">Pinning a chart or a passage from any step starts it too.</p>
        <button className="btn primary" disabled={busy} onClick={() => {
          setBusy(true);
          void onStart().finally(() => setBusy(false));
        }}>
          {busy ? "Starting…" : "Start the thesis"}
        </button>
      </div>
    </section>
  );
}

/** Step 5: the project's models (the first unless the route names one), each the model page
 * without its back link; a new one starts from the company's own history. */
function ModelStep({ token, companyId, ticker, models, modelId, saved, projectId, label, active, onChanged }: {
  token: string;
  companyId: string;
  ticker: string;
  models: { item_id: string; name: string }[];
  modelId: string | null;
  saved: Saved;
  projectId: string;
  label: (companyId: string) => string;
  active: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const years = await loadHistory(token, companyId).then((h) => h.years).catch(() => []);
      const state = modelState({ companyId, historyYears: DEFAULT_HISTORY, assumptions: defaultAssumptions(historyStats(years), DEFAULT_YEARS) });
      const item = await saved.create("model", `${ticker} model`.slice(0, 120), state, { workspaceId: projectId });
      onChanged();
      go({ view: "analysis", projectId, step: "model", itemId: item.item_id });
    } catch (e) {
      setError(`Couldn't create it: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };
  if (!modelId) {
    return (
      <section className="card">
        <div className="card-b">
          <p>
            A revenue-first model: the company's fiscal years of revenue and operating income from its filings, then your
            growth and margin per forecast year, as base, bear and bull cases. It starts at {ticker}'s own 5-year revenue CAGR
            and average operating margin.
          </p>
          <button className="btn primary" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : "Create the model"}</button>
          {error && <p className="err small" role="alert">{error}</p>}
        </div>
      </section>
    );
  }
  return (
    <>
      <div className="row wrap model-picker">
        {models.length > 1 && (
          <span className="seg" role="group" aria-label="Model">
            {models.map((m) => (
              <a key={m.item_id} className={m.item_id === modelId ? "on" : undefined} aria-current={m.item_id === modelId ? "page" : undefined}
                href={href({ view: "analysis", projectId, step: "model", itemId: m.item_id })}>{m.name}</a>
            ))}
          </span>
        )}
        <span className="grow" />
        <PinButton what="model" make={async () => embedMarker(modelId)} />
        <button className="btn sm ghost" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : "New model"}</button>
      </div>
      {error && <p className="err small" role="alert">{error}</p>}
      <ModelPage key={modelId} token={token} itemId={modelId} saved={saved} label={label} embedded active={active} onSaved={onChanged} />
    </>
  );
}

/** Step 7: Check my thesis (the shared check, beside the project's thesis) and Revisit's
 * new-filing notice; KPIs and red lines come next (phase 3). */
function Monitor({ token, companyId, projectId, thesisId, status, checks, label, onQuote }: {
  token: string;
  companyId: string;
  projectId: string;
  thesisId: string | null;
  status: ThesisStatus | null;
  checks: ReturnType<typeof useThesisCheck>;
  label: (companyId: string) => string;
  onQuote: (quote: string) => void;
}) {
  const newer = useNewerFiling(token, checks.history[0], companyId);
  const [hash, setHash] = useState<string | null>(null);
  const savedText = status?.saved ?? null;
  useEffect(() => {
    if (savedText === null) return;
    let live = true;
    textHash(companyId, savedText).then((x) => live && setHash(x)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [companyId, savedText]);
  const { allowance } = useAllowanceContext();
  const checking = checks.run.status === "running";
  const written = savedText !== null && hasOwnText(savedText);
  const why = !status ? "Loading the thesis…" : status.dirty ? "Save the thesis first: the check reads the saved text"
    : !written ? "Write the thesis first: headings and guidance alone have nothing to check"
    : `The assistant checks each factual claim and your models' base cases against the filings (counts as 4 questions${allowance ? `; you have ${leftText(allowance)}` : ""})`;
  if (!thesisId) {
    return (
      <p className="empty card">
        Nothing to monitor yet: <a href={href({ view: "analysis", projectId, step: "thesis" })}>write the thesis</a> first. Each
        new 10-K or 10-Q is then checked against it here.
      </p>
    );
  }
  return (
    <>
      <div className="row wrap monitor-bar">
        <button className="btn primary" onClick={checks.start} disabled={!status || status.dirty || checking || !written} title={why}>
          {checking ? "Checking…" : checks.history.length ? "Check again" : "Check my thesis"}
        </button>
        <span className="faint small">
          {checks.history[0] ? `Last checked ${day((checks.history[0].checked_at ?? checks.history[0].created_at).slice(0, 10), true)}` : "Not checked yet"}
          {status?.dirty && " · the thesis has unsaved changes"}
        </span>
      </div>
      {newer && !checking && (
        <div className="report-status revisit" role="status">
          New {newer.form} filed {day(newer.filed, true)} since your last check — re-check?{" "}
          <button className="btn sm" onClick={checks.start} disabled={!status || status.dirty || !written} title={why}>Re-check</button>
        </div>
      )}
      <CheckPanel checks={checks} stale={!!checks.shown && hash !== null && checks.shown.text_hash !== hash} onQuote={onQuote} label={label} />
      <section className="card coming-next" aria-label="Coming next: KPIs and red lines">
        <div className="card-h">
          <h3>KPIs and red lines</h3>
          <span className="tag e">coming next</span>
        </div>
        <p className="empty">
          Next: the 5-7 KPIs from your thesis as rules with red lines (e.g. “gross margin below 60%”), checked automatically
          after each new filing, so the project says whether the latest 10-Q confirms, is neutral to or challenges the thesis.
          Until then, list them under “KPIs to watch” and re-check when a filing arrives.
        </p>
      </section>
    </>
  );
}
