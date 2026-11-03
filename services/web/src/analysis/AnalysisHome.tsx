import { useEffect, useId, useMemo, useState, type FormEvent } from "react";

import type { Readable } from "../access";
import { ApiError, createWorkspace, peers as fetchPeers, screener, thesisChecks } from "../api";
import { CompanySearch } from "../components/CompanySearch";
import { Skeleton } from "../components/States";
import { ago, day } from "../format";
import { go, href } from "../route";
import { STATUS_LABELS } from "../thesis/check";
import { useNewerFilings } from "../thesis/useRevisit";
import type { SavedWorkspace, ThesisCheckSummary } from "../types";
import { nameProblem, WORKSPACE_NAME_MAX } from "../workspaces/model";
import type { Saved } from "../workspaces/useSaved";
import { MAX_PEERS, restoreProject, STEPS } from "./project";

/** peers suggested in "New analysis", and how many start ticked */
const SUGGESTED = 8;
const PRESELECTED = 3;

interface Props {
  token: string;
  saved: Saved;
  /** companies whose financials the user can read (access.ts): the only ones offered */
  readable: Readable;
  label: (companyId: string) => string;
}

export interface ProjectRow {
  ws: SavedWorkspace;
  companyId: string;
  thesisId: string | null;
  /** the newest change to the project or anything in it */
  edited: string;
}

/** The user's analysis projects, newest edit first (pure). */
export function projectRows(list: SavedWorkspace[]): ProjectRow[] {
  return list.flatMap((ws) => {
    const p = restoreProject(ws.project);
    if (!p) return [];
    const edited = [ws.updated_at, ...ws.items.map((i) => i.updated_at)].filter(Boolean).sort().at(-1) ?? ws.updated_at;
    return [{ ws, companyId: p.companyId, thesisId: ws.items.find((i) => i.kind === "thesis")?.item_id ?? null, edited }];
  }).sort((a, b) => b.edited.localeCompare(a.edited));
}

/** Names and tickers of the projects' companies (public identity, one screener request). */
function useNames(token: string, ids: string[]): Map<string, { name: string; ticker: string | null }> {
  const [out, setOut] = useState(new Map<string, { name: string; ticker: string | null }>());
  const key = [...new Set(ids)].sort().join(",");
  useEffect(() => {
    if (!key) return;
    let live = true;
    const list = key.split(",");
    screener(token, { company_id: list, columns: ["revenue_ttm"], sort: "name", limit: list.length })
      .then((r) => live && setOut(new Map(r.rows.map((x) => [x.company_id, { name: x.name ?? x.company_id, ticker: x.tickers?.[0] ?? null }]))))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token, key]);
  return out;
}

/** The newest check of each thesis (one checks-list request per thesis, the first 20). */
function useLastChecks(token: string, thesisIds: string[]): Record<string, ThesisCheckSummary | null> {
  const [out, setOut] = useState<Record<string, ThesisCheckSummary | null>>({});
  const key = thesisIds.slice(0, 20).join(",");
  useEffect(() => {
    let live = true;
    for (const id of key ? key.split(",") : []) {
      thesisChecks(token, id)
        .then((r) => live && setOut((o) => ({ ...o, [id]: r.checks[0] ?? null })))
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [token, key]);
  return out;
}

/** The Analysis tab's home: your projects and "New analysis". */
export function AnalysisHome({ token, saved, readable, label }: Props) {
  const [creating, setCreating] = useState(false);
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const rows = useMemo(() => projectRows(list), [list]);
  const names = useNames(token, rows.map((r) => r.companyId));
  const theses = rows.flatMap((r) => (r.thesisId ? [r.thesisId] : []));
  const checks = useLastChecks(token, theses);
  const newer = useNewerFilings(token, theses);
  const offline = saved.list.state === "unavailable" || saved.list.state === "error";

  return (
    <div className="page analysis-home">
      <div className="row wrap">
        <h1 className="grow">Analysis</h1>
        <button className="btn primary" onClick={() => setCreating((c) => !c)} aria-expanded={creating} disabled={offline}
          title={offline ? "Saving is unavailable right now" : "Start a guided analysis of one company"}>
          New analysis
        </button>
      </div>
      <p className="muted analysis-intro">
        A guided path from a company's filings to your own thesis: what the business does, ten years of numbers, what changed
        in the latest filings, how it compares with its peers, a model with bear, base and bull cases, the thesis itself, and a
        check of it after every new 10-Q or 10-K. Everything you pin, chart and write stays in the project, visible only to you.
      </p>
      {creating && <NewAnalysis token={token} saved={saved} readable={readable} onDone={() => setCreating(false)} />}
      {saved.list.state === "loading" ? (
        <Skeleton lines={4} label="Loading your analyses" />
      ) : offline ? (
        <p className="empty card">Saved work is unavailable right now, so projects can't be listed or created. Try again shortly.</p>
      ) : rows.length === 0 ? (
        !creating && (
          <section className="card analysis-empty">
            <div className="card-b">
              <h2>No analyses yet</h2>
              <ol className="step-list">
                {STEPS.map((s) => <li key={s.key}><b>{s.label}</b> <span className="muted">{s.why}</span></li>)}
              </ol>
              <button className="btn primary" onClick={() => setCreating(true)}>Start your first analysis</button>
            </div>
          </section>
        )
      ) : (
        <ul className="project-list" aria-label="Your analyses">
          {rows.map((r) => {
            const who = names.get(r.companyId);
            const last = r.thesisId ? checks[r.thesisId] : undefined;
            const fresh = r.thesisId ? newer[r.thesisId] : undefined;
            return (
              <li key={r.ws.workspace_id} className="card">
                <a className="project-row" href={href({ view: "analysis", projectId: r.ws.workspace_id })}>
                  <span className="grow">
                    <b>{who?.name ?? label(r.companyId)}</b>
                    {who?.ticker && <span className="mono faint"> {who.ticker}</span>}
                    <span className="small muted project-name">{r.ws.name}</span>
                  </span>
                  <span className="small muted">edited {ago(r.edited)}</span>
                  <span className="small project-status">
                    {!r.thesisId ? (
                      <span className="faint">no thesis yet</span>
                    ) : last === undefined ? (
                      <span className="faint">…</span>
                    ) : last === null ? (
                      <span className="faint">thesis not checked yet</span>
                    ) : (
                      <span title={`Checked ${day((last.checked_at ?? last.created_at).slice(0, 10), true)}`}>
                        <span className="tag q">{last.counts.supported} {STATUS_LABELS.supported.toLowerCase()}</span>{" "}
                        <span className={last.counts.contradicted ? "tag warn" : "tag"}>{last.counts.contradicted} {STATUS_LABELS.contradicted.toLowerCase()}</span>{" "}
                        <span className="tag">{last.counts.unverifiable} {STATUS_LABELS.unverifiable.toLowerCase()}</span>
                      </span>
                    )}
                    {fresh && <span className="tag e" title="Re-check the thesis against it">new {fresh.form} {day(fresh.filed)}</span>}
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Pick a company (only ones you can read), tick peers (suggested by the peers route), name
 * it: a new project (a workspace with `project` set) opens at its first step. */
function NewAnalysis({ token, saved, readable, onDone }: Omit<Props, "label"> & { onDone: () => void }) {
  const id = useId();
  const [company, setCompany] = useState<{ id: string; name: string } | null>(null);
  const [suggested, setSuggested] = useState<{ id: string; name: string }[] | null>(null);
  const [peers, setPeers] = useState<{ id: string; name: string }[]>([]);
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!company) return;
    let live = true;
    setSuggested(null);
    fetchPeers(token, company.id, ["revenue_ttm"], SUGGESTED, true)
      .then((r) => {
        if (!live) return;
        const list = r.peers
          .filter((p) => readable.has(p.company_id) && p.access.includes("financials"))
          .map((p) => ({ id: p.company_id, name: p.tickers?.[0] ?? p.name ?? p.company_id }));
        setSuggested(list);
        setPeers(list.slice(0, PRESELECTED));
      })
      .catch(() => live && setSuggested([]));
    return () => {
      live = false;
    };
  }, [token, company?.id]);

  const pick = (cid: string, n: string) => {
    setCompany({ id: cid, name: n });
    setPeers([]);
    if (!touched) setName(n.slice(0, WORKSPACE_NAME_MAX));
  };
  const toggle = (p: { id: string; name: string }) =>
    setPeers((cur) => (cur.some((x) => x.id === p.id) ? cur.filter((x) => x.id !== p.id) : cur.length < MAX_PEERS ? [...cur, p] : cur));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!company) return setError("Pick the company to analyse.");
    const problem = nameProblem(name, WORKSPACE_NAME_MAX);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      const ws = await createWorkspace(token, name.trim(), { companyId: company.id, peers: peers.map((p) => p.id), notes: "", done: [] });
      saved.reload();
      onDone();
      go({ view: "analysis", projectId: ws.workspace_id, step: "business" });
    } catch (err) {
      setError(`Couldn't create it: ${err instanceof ApiError ? err.message : "the Query API didn't answer"}.`);
      setBusy(false);
    }
  };

  const extra = peers.filter((p) => !suggested?.some((s) => s.id === p.id));
  return (
    <form className="card new-analysis" aria-label="New analysis" onSubmit={(e) => void submit(e)}
      onKeyDown={(e) => e.key === "Escape" && onDone()}>
      <div className="card-b">
        <h2>New analysis</h2>
        <div className="field-row">
          <span className="small muted">Company</span>
          {company ? (
            <span className="row">
              <b className="grow">{company.name}</b>
              <button type="button" className="btn sm ghost" onClick={() => {
                setCompany(null);
                setSuggested(null);
                setPeers([]);
              }}>Change</button>
            </span>
          ) : (
            <CompanySearch token={token} label="Company to analyse" placeholder="Name or ticker, e.g. NVDA" exclude={[]}
              readable={readable} autoFocus onPick={pick} />
          )}
        </div>
        {company && (
          <div className="field-row">
            <span className="small muted">Peers <span className="faint">(optional; change them later on the Peers step)</span></span>
            <div className="row wrap" role="group" aria-label="Suggested peers">
              {suggested === null && <span className="faint small">Finding peers…</span>}
              {suggested?.length === 0 && <span className="faint small">No suggested peers with the same SIC you can read.</span>}
              {suggested?.map((p) => {
                const on = peers.some((x) => x.id === p.id);
                return (
                  <button key={p.id} type="button" className={on ? "chip on" : "chip"} aria-pressed={on} onClick={() => toggle(p)}>
                    {on ? "✓ " : "+ "}{p.name}
                  </button>
                );
              })}
              {extra.map((p) => (
                <button key={p.id} type="button" className="chip on" aria-pressed onClick={() => toggle(p)}>✓ {p.name}</button>
              ))}
            </div>
            {peers.length < MAX_PEERS && (
              <CompanySearch token={token} label="Add another peer" placeholder="+ Add a peer" readable={readable}
                exclude={[company.id, ...peers.map((p) => p.id)]} onPick={(pid, n) => toggle({ id: pid, name: n })} />
            )}
          </div>
        )}
        <label htmlFor={`${id}-name`} className="small muted">Name</label>
        <input id={`${id}-name`} className="field" value={name} maxLength={WORKSPACE_NAME_MAX} placeholder="e.g. Nvidia"
          onChange={(e) => {
            setName(e.target.value);
            setTouched(true);
          }} />
        <p className="faint small">A project is a workspace: it also shows under Workspaces in the sidebar, with its charts, model and thesis.</p>
        {error && <p className="err small" role="alert">{error}</p>}
        <div className="row">
          <span className="grow" />
          <button type="button" className="btn ghost" onClick={onDone}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !company}>{busy ? "Creating…" : "Start the analysis"}</button>
        </div>
      </div>
    </form>
  );
}
