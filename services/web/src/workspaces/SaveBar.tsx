import { useId, useState, type FormEvent } from "react";

import { failureReason } from "../api";
import { MenuButton } from "../components/MenuButton";
import { toast } from "../components/Toasts";
import { ConfirmDelete } from "./Actions";
import {
  canSave,
  companyOf,
  currentFingerprint,
  defaultWorkspace,
  ITEM_NAME_MAX,
  KIND_LABELS,
  nameProblem,
  NEW_WORKSPACE,
  SCREENS_WORKSPACE,
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

const NEW = NEW_WORKSPACE;

function message(e: unknown): string {
  return failureReason(e);
}

/** Save / Save as for the page's chart, comps or screen, with the open item's workspace and an
 * unsaved-changes mark. */
export function SaveBar({ current, saved, label }: Props) {
  const open = saved.open[current.kind];
  const dirty = open ? currentFingerprint(current) !== open.saved : false;
  const [form, setForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Close with unsaved changes asks first (Riley 61): the page keeps them, the saved item doesn't
  const [closing, setClosing] = useState(false);
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
      if (open) toast({ text: `Saved “${open.name}” in ${open.workspaceName}.`, topic: `saved-${current.kind}` });
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
          <span className="faint">{KIND_LABELS[current.kind]} in {open.workspaceName}:</span> <b>{open.name}</b>{" "}
          {dirty ? (
            <span className="dirty" title="Changes since you last saved">Unsaved changes</span>
          ) : (
            <span className="faint">Saved</span>
          )}
        </span>
      )}
      {/* an open item: Save only when there is something to save, Save as and Close in a menu, so the page's bar
          isn't six controls with a greyed Save and a bare "Close" (Riley, test 10, finding 86) */}
      {open && dirty && (
        <button className="btn" onClick={() => void saveOver()} disabled={busy || !ready || offline} title={why}>
          Save
        </button>
      )}
      {open ? (
        <MenuButton label="More" count={false} items={[
          { label: "Save as…", disabled: !ready || offline, title: why, onSelect: () => {
            setForm(true);
            setError(null);
          } },
          { label: `Close “${open.name}”`, title: `Stop editing it; it stays saved as it was`,
            onSelect: () => (dirty ? setClosing(true) : saved.close(current.kind)) },
        ]} />
      ) : (
        <button
          className="btn primary"
          onClick={() => {
            setForm((f) => !f);
            setError(null);
          }}
          disabled={!ready || offline}
          title={why}
          aria-expanded={form}
        >
          Save…
        </button>
      )}
      {closing && open && (
        <ConfirmDelete text={`Close “${open.name}” without saving?`} yes="Close without saving"
          detail={`Your changes stay on this page, but “${open.name}” in ${open.workspaceName} keeps its last saved version.`}
          onYes={() => {
            setClosing(false);
            saved.close(current.kind);
          }}
          onNo={() => setClosing(false)} />
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
          label={label}
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
  defaultWorkspace: openIn,
  label,
  onDone,
}: {
  current: Current;
  saved: Saved;
  initial: string;
  defaultWorkspace?: string;
  label: (companyId: string) => string;
  onDone: () => void;
}) {
  const id = useId();
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const [name, setName] = useState(initial);
  // #119: the open item's workspace, else the company's project or folder, else a new workspace
  // named after it; a screen goes to a folder of screens, else a new "Screens"
  const company = companyOf(current);
  const [workspace, setWorkspace] = useState(() => openIn ?? defaultWorkspace(list, company ? { id: company, names: [label(company)] } : null));
  const [newName, setNewName] = useState(() => (company ? label(company).slice(0, WORKSPACE_NAME_MAX) : current.kind === "screen" ? SCREENS_WORKSPACE : ""));
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
      const item = await saved.saveAs(current, name, target);
      // a workspace the save made says so (Riley, test 10, finding 43: "Screens" appeared in the sidebar unannounced)
      if (item.madeWorkspace) {
        toast({ text: `Saved “${item.name}” in a new workspace, ${item.workspace_name}: it's in the sidebar.`, topic: `saved-${current.kind}` });
      }
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
      {/* the suggestion is selected, so typing replaces it (Riley, test 10, finding 45) */}
      <input id={`${id}-name`} className="field" value={name} maxLength={ITEM_NAME_MAX} onChange={(e) => setName(e.target.value)} autoFocus
        onFocus={(e) => e.currentTarget.select()} />
      <label htmlFor={`${id}-ws`}>Workspace</label>
      <select id={`${id}-ws`} className="field" value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
        {list.map((w) => (
          <option key={w.workspace_id} value={w.workspace_id}>
            {w.name}
          </option>
        ))}
        <option value={NEW}>New workspace…</option>
      </select>
      {workspace === NEW && (
        <>
          <label htmlFor={`${id}-new`}>The new workspace's name</label>
          <input
            id={`${id}-new`}
            className="field"
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
