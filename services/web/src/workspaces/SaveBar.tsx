import { useId, useState, type FormEvent } from "react";

import { ApiError } from "../api";
import {
  canSave,
  currentFingerprint,
  ITEM_NAME_MAX,
  KIND_LABELS,
  nameProblem,
  suggestName,
  WORKSPACE_NAME_MAX,
  type Current,
} from "./model";
import type { Saved, Target } from "./useSaved";

interface Props {
  current: Current;
  saved: Saved;
  /** a company's ticker (or name), for default names */
  label: (companyId: string) => string;
}

const NEW = "__new__";

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : "the Query API didn't answer";
}

/** Save / Save as for the page's chart, comps or screen, with the open item's workspace and an
 * unsaved-changes mark. */
export function SaveBar({ current, saved, label }: Props) {
  const open = saved.open[current.kind];
  const dirty = open ? currentFingerprint(current) !== open.saved : false;
  const [form, setForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = canSave(current);
  const offline = saved.list.state === "unavailable" || saved.list.state === "error";
  const why = offline
    ? "Saving is unavailable right now"
    : !ready
      ? current.kind === "chart"
        ? "Add a layer to save this chart"
        : "Pick a target company to save these comps"
      : undefined;

  const saveOver = async () => {
    setBusy(true);
    setError(null);
    try {
      await saved.save(current);
    } catch (e) {
      setError(`Couldn't save: ${message(e)}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="savebar">
      {open && (
        <span className="saved-as" title={`Saved in workspace “${open.workspaceName}”`}>
          <span className="faint">{open.workspaceName} /</span> {open.name}
          {dirty ? (
            <span className="dirty" title="Changes since you last saved">
              {" "}● unsaved changes
            </span>
          ) : (
            <span className="faint"> · saved</span>
          )}
        </span>
      )}
      {open && (
        <button className="btn" onClick={() => void saveOver()} disabled={!dirty || busy || !ready || offline} title={why}>
          Save
        </button>
      )}
      <button
        className={open ? "btn" : "btn primary"}
        onClick={() => {
          setForm((f) => !f);
          setError(null);
        }}
        disabled={!ready || offline}
        title={why}
        aria-expanded={form}
      >
        {open ? "Save as…" : "Save…"}
      </button>
      {open && (
        <button className="btn ghost" onClick={() => saved.close(current.kind)} title={`Stop editing “${open.name}”; it stays saved`}>
          Close
        </button>
      )}
      {error && !form && (
        <span className="err small" role="alert">
          {error}
        </span>
      )}
      {form && (
        <SaveForm
          current={current}
          saved={saved}
          initial={open ? `${open.name}` : suggestName(current, label)}
          defaultWorkspace={open?.workspaceId}
          onDone={() => setForm(false)}
        />
      )}
    </div>
  );
}

function SaveForm({
  current,
  saved,
  initial,
  defaultWorkspace,
  onDone,
}: {
  current: Current;
  saved: Saved;
  initial: string;
  defaultWorkspace?: string;
  onDone: () => void;
}) {
  const id = useId();
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const [name, setName] = useState(initial);
  const [workspace, setWorkspace] = useState(defaultWorkspace ?? list[0]?.workspace_id ?? NEW);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const problem =
      nameProblem(name, ITEM_NAME_MAX) ?? (workspace === NEW ? nameProblem(newName, WORKSPACE_NAME_MAX) : null);
    if (problem) return setError(problem);
    const target: Target = workspace === NEW ? { newWorkspace: newName } : { workspaceId: workspace };
    setBusy(true);
    setError(null);
    try {
      await saved.saveAs(current, name, target);
      onDone();
    } catch (err) {
      setError(`Couldn't save: ${message(err)}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="save-form card" onSubmit={(e) => void submit(e)} aria-label={`Save ${KIND_LABELS[current.kind].toLowerCase()}`}>
      <label htmlFor={`${id}-name`}>Name</label>
      <input id={`${id}-name`} value={name} maxLength={ITEM_NAME_MAX} onChange={(e) => setName(e.target.value)} autoFocus />
      <label htmlFor={`${id}-ws`}>Workspace</label>
      <select id={`${id}-ws`} value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
        {list.map((w) => (
          <option key={w.workspace_id} value={w.workspace_id}>
            {w.name}
          </option>
        ))}
        <option value={NEW}>New workspace…</option>
      </select>
      {workspace === NEW && (
        <>
          <label htmlFor={`${id}-new`}>New workspace</label>
          <input
            id={`${id}-new`}
            value={newName}
            maxLength={WORKSPACE_NAME_MAX}
            placeholder="e.g. Beverages"
            onChange={(e) => setNewName(e.target.value)}
          />
        </>
      )}
      {error && (
        <p className="err small" role="alert">
          {error}
        </p>
      )}
      <div className="row">
        <span className="grow" />
        <button type="button" className="btn ghost" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="btn primary" disabled={busy}>
          Save
        </button>
      </div>
    </form>
  );
}
