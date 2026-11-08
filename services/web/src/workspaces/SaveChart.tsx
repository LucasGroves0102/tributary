import { useId, useState, type FormEvent } from "react";

import { failureReason } from "../api";
import type { ChartState } from "../chart/model";
import { href } from "../route";
import { companyOf, defaultWorkspace, ITEM_NAME_MAX, nameProblem, NEW_WORKSPACE, stateOf, WORKSPACE_NAME_MAX } from "./model";
import type { Saved, Target } from "./useSaved";

const NEW = NEW_WORKSPACE;

/** "Save to workspace" for a chart shown outside the chart builder (the Revenue view): name
 * and workspace (or a new one), saved as an ordinary chart item through the same Query API
 * route and state cleaner as the chart builder's Save as, so a thesis in that workspace can
 * embed it. It doesn't become the chart builder's open item. */
export function SaveChart({ saved, chart, label }: { saved: Saved; chart: ChartState; label: (companyId: string) => string }) {
  const id = useId();
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const offline = saved.list.state === "unavailable" || saved.list.state === "error";
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(chart.title.slice(0, ITEM_NAME_MAX));
  // #119: the company's project or folder, else a new workspace named after it
  const company = companyOf({ kind: "chart", chart });
  const [workspace, setWorkspace] = useState(() => defaultWorkspace(list, company ? { id: company, names: [label(company)] } : null));
  const [newName, setNewName] = useState(() => (company ? label(company).slice(0, WORKSPACE_NAME_MAX) : ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ workspaceId: string; workspace: string } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const problem = nameProblem(name, ITEM_NAME_MAX) ?? (workspace === NEW ? nameProblem(newName, WORKSPACE_NAME_MAX) : null);
    if (problem) return setError(problem);
    const target: Target = workspace === NEW ? { newWorkspace: newName } : { workspaceId: workspace };
    setBusy(true);
    setError(null);
    try {
      const item = await saved.create("chart", name, stateOf({ kind: "chart", chart }), target);
      setDone({ workspaceId: item.workspace_id, workspace: item.workspace_name ?? "the workspace" });
      setOpen(false);
    } catch (err) {
      setError(`Couldn't save it: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="savebar save-chart">
      <button className="btn sm" disabled={offline} aria-expanded={open}
        title={offline ? "Saving is unavailable right now" : "Save this chart to a workspace; a thesis there can embed it"}
        onClick={() => {
          setOpen((o) => !o);
          setDone(null);
        }}>
        Save to workspace
      </button>
      {done && (
        <span className="small muted" role="status">
          Saved to <a href={href({ view: "workspace", workspaceId: done.workspaceId })}>{done.workspace}</a>; embed it in a thesis there
          with “Embed a saved item”.
        </span>
      )}
      {open && (
        <form className="save-form card" onSubmit={(e) => void submit(e)} aria-label="Save chart to a workspace"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
          <label htmlFor={`${id}-name`}>Name</label>
          <input id={`${id}-name`} value={name} maxLength={ITEM_NAME_MAX} autoFocus onChange={(e) => setName(e.target.value)} />
          <label htmlFor={`${id}-ws`}>Workspace</label>
          <select id={`${id}-ws`} value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
            {list.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id}>{w.name}</option>
            ))}
            <option value={NEW}>New workspace…</option>
          </select>
          {workspace === NEW && (
            <>
              <label htmlFor={`${id}-new`}>New workspace</label>
              <input id={`${id}-new`} value={newName} maxLength={WORKSPACE_NAME_MAX} placeholder="e.g. Semis"
                onChange={(e) => setNewName(e.target.value)} />
            </>
          )}
          {error && <p className="err small" role="alert">{error}</p>}
          <div className="row">
            <span className="grow" />
            <button type="button" className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save chart"}</button>
          </div>
        </form>
      )}
    </span>
  );
}
