import { useId, useState, type FormEvent } from "react";

import { failureReason } from "../api";
import { CompanySearch } from "../components/CompanySearch";
import { DEFAULT_HISTORY, DEFAULT_YEARS, modelState } from "../models/model";
import { newModelAssumptions } from "../models/useHistory";
import { go } from "../route";
import { thesisState } from "../thesis/model";
import type { WrittenKind } from "../types";
import { defaultWorkspace, ITEM_NAME_MAX, nameProblem, NEW_WORKSPACE, WORKSPACE_NAME_MAX } from "./model";
import type { Saved, Target } from "./useSaved";

const NEW = NEW_WORKSPACE;
const LABEL: Record<WrittenKind, string> = { thesis: "thesis", model: "model" };

interface Fixed {
  token: string;
  saved: Saved;
  /** from a company page: the thesis or model is about this company */
  company?: { id: string; name: string };
  /** from a workspace page: it goes in this workspace */
  workspaceId?: string;
}

/** "New thesis" and "New model" buttons, each opening a small form: name, company (a model
 * needs one; a thesis may name one) and workspace, unless the page fixes them. */
export function NewWork(props: Fixed) {
  const [open, setOpen] = useState<WrittenKind | null>(null);
  const offline = props.saved.list.state === "unavailable" || props.saved.list.state === "error";
  return (
    <span className="savebar new-work">
      {(["thesis", "model"] as const).map((k) => (
        <button key={k} className="btn" disabled={offline} aria-expanded={open === k}
          title={offline ? "Saving is unavailable right now" : k === "thesis" ? "Write a thesis in Markdown, with your charts and models embedded" : "Forecast revenue and operating income from history and your assumptions"}
          onClick={() => setOpen((o) => (o === k ? null : k))}>
          New {LABEL[k]}
        </button>
      ))}
      {open && <NewWorkForm key={open} kind={open} {...props} onDone={() => setOpen(null)} />}
    </span>
  );
}

function NewWorkForm({ kind, token, saved, company: fixed, workspaceId, onDone }: Fixed & { kind: WrittenKind; onDone: () => void }) {
  const id = useId();
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const [company, setCompany] = useState<{ id: string; name: string } | null>(fixed ?? null);
  const suggested = (c: { name: string } | null) => (c ? `${c.name} ${LABEL[kind]}` : `Untitled ${LABEL[kind]}`).slice(0, ITEM_NAME_MAX);
  const [name, setName] = useState(() => suggested(fixed ?? null));
  const [touched, setTouched] = useState(false);
  // #119: the company's project or folder, else a new workspace named after it; never just the first
  const startOn = (c: { id: string; name: string } | null) => defaultWorkspace(list, c ? { id: c.id, names: [c.name] } : null);
  const [workspace, setWorkspace] = useState(() => workspaceId ?? startOn(fixed ?? null));
  const [wsTouched, setWsTouched] = useState(false);
  const [newName, setNewName] = useState(() => (fixed ? fixed.name.slice(0, WORKSPACE_NAME_MAX) : ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (kind === "model" && !company) return setError("Pick the company to model.");
    const problem = nameProblem(name, ITEM_NAME_MAX) ?? (workspace === NEW ? nameProblem(newName, WORKSPACE_NAME_MAX) : null);
    if (problem) return setError(problem);
    const target: Target = workspace === NEW ? { newWorkspace: newName } : { workspaceId: workspace };
    setBusy(true);
    setError(null);
    try {
      let state: object;
      if (kind === "model") {
        // start from the company's own history: its revenue CAGR and average margin
        state = modelState({ companyId: company!.id, historyYears: DEFAULT_HISTORY, assumptions: await newModelAssumptions(token, company!.id, DEFAULT_YEARS) });
      } else {
        state = thesisState({ companyId: company?.id ?? null, markdown: "" });
      }
      const item = await saved.create(kind, name, state, target);
      onDone();
      go({ view: kind, itemId: item.item_id });
    } catch (err) {
      setError(`Couldn't create it: ${failureReason(err)}.`);
      setBusy(false);
    }
  };

  return (
    <form className="save-form card" onSubmit={(e) => void submit(e)} aria-label={`New ${LABEL[kind]}`}
      onKeyDown={(e) => e.key === "Escape" && onDone()}>
      {!fixed && (
        <>
          <span className="small muted">Company{kind === "thesis" ? " (optional)" : ""}</span>
          {company ? (
            <span className="row">
              <b className="grow">{company.name}</b>
              <button type="button" className="btn sm ghost" onClick={() => setCompany(null)}>Change</button>
            </span>
          ) : (
            <CompanySearch token={token} label={kind === "model" ? "Company to model" : "Company this thesis is about"}
              placeholder="Name or ticker" exclude={[]}
              onPick={(cid, n) => {
                setCompany({ id: cid, name: n });
                if (!touched) setName(suggested({ name: n }));
                if (!wsTouched && !workspaceId) {
                  setWorkspace(startOn({ id: cid, name: n }));
                  setNewName((v) => v || n.slice(0, WORKSPACE_NAME_MAX));
                }
              }} />
          )}
        </>
      )}
      <label htmlFor={`${id}-name`}>Name</label>
      <input id={`${id}-name`} value={name} maxLength={ITEM_NAME_MAX} autoFocus={!!fixed}
        onChange={(e) => {
          setName(e.target.value);
          setTouched(true);
        }} />
      {!workspaceId && (
        <>
          <label htmlFor={`${id}-ws`}>Workspace</label>
          <select id={`${id}-ws`} value={workspace} onChange={(e) => {
            setWorkspace(e.target.value);
            setWsTouched(true);
          }}>
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
        </>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
      <div className="row">
        <span className="grow" />
        <button type="button" className="btn ghost" onClick={onDone}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Creating…" : `Create ${LABEL[kind]}`}
        </button>
      </div>
    </form>
  );
}
