import { useState, type FormEvent } from "react";

import { ApiError } from "../api";
import type { SavedItem, SavedWorkspace } from "../types";
import { ITEM_NAME_MAX, KIND_LABELS, KIND_PAGES, nameProblem, WORKSPACE_NAME_MAX } from "./model";
import type { Saved } from "./useSaved";

type Editing =
  | { type: "new" }
  | { type: "rename-ws"; id: string; name: string }
  | { type: "rename-item"; id: string; name: string }
  | { type: "delete-ws"; id: string }
  | { type: "delete-item"; id: string }
  | null;

interface Props {
  saved: Saved;
  onOpen: (itemId: string) => void;
  onError: (message: string) => void;
}

const why = (e: unknown) => (e instanceof ApiError ? e.message : "the Query API didn't answer");

/** The sidebar's Workspaces: folders of saved charts, comps and screens. Click an item to open
 * it; rename, duplicate and delete from the small buttons beside each row. */
export function WorkspaceTree({ saved, onOpen, onError }: Props) {
  const [editing, setEditing] = useState<Editing>(null);
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set());
  const { list, open } = saved;
  const openIds = new Set(Object.values(open).map((o) => o?.itemId));

  const run = (what: string, p: Promise<unknown>) =>
    p.then(() => setEditing(null)).catch((e) => onError(`Couldn't ${what}: ${why(e)}.`));

  return (
    <>
      <div className="sect">
        <span className="grow">Workspaces{list.state === "ok" ? ` · ${list.workspaces.length}` : ""}</span>
        <button
          onClick={() => setEditing({ type: "new" })}
          title="New workspace"
          aria-label="New workspace"
          disabled={list.state !== "ok"}
        >
          +
        </button>
      </div>
      <div className="ws">
        {editing?.type === "new" && (
          <NameInput
            label="New workspace name"
            initial=""
            max={WORKSPACE_NAME_MAX}
            onCancel={() => setEditing(null)}
            onSubmit={(name) => run("create the workspace", saved.createWorkspace(name))}
          />
        )}
        {list.state === "loading" && <p className="side-note">Loading workspaces…</p>}
        {(list.state === "unavailable" || list.state === "error") && (
          <p className="side-note">Saved work is unavailable right now.</p>
        )}
        {list.state === "ok" && list.workspaces.length === 0 && editing?.type !== "new" && (
          <p className="side-note">Save a chart, comps table or screen and it is listed here.</p>
        )}
        {list.state === "ok" &&
          list.workspaces.map((w) => (
            <Folder
              key={w.workspace_id}
              workspace={w}
              collapsed={closed.has(w.workspace_id)}
              openIds={openIds}
              editing={editing}
              setEditing={setEditing}
              onToggle={() =>
                setClosed((c) => {
                  const next = new Set(c);
                  if (!next.delete(w.workspace_id)) next.add(w.workspace_id);
                  return next;
                })
              }
              onOpen={onOpen}
              run={run}
              saved={saved}
            />
          ))}
      </div>
    </>
  );
}

function Folder({
  workspace: w,
  collapsed,
  openIds,
  editing,
  setEditing,
  onToggle,
  onOpen,
  run,
  saved,
}: {
  workspace: SavedWorkspace;
  collapsed: boolean;
  openIds: Set<string | undefined>;
  editing: Editing;
  setEditing: (e: Editing) => void;
  onToggle: () => void;
  onOpen: (itemId: string) => void;
  run: (what: string, p: Promise<unknown>) => void;
  saved: Saved;
}) {
  const renaming = editing?.type === "rename-ws" && editing.id === w.workspace_id;
  const deleting = editing?.type === "delete-ws" && editing.id === w.workspace_id;
  return (
    <div className="folder">
      {renaming ? (
        <NameInput
          label="Workspace name"
          initial={w.name}
          max={WORKSPACE_NAME_MAX}
          onCancel={() => setEditing(null)}
          onSubmit={(name) => run("rename the workspace", saved.renameWorkspace(w.workspace_id, name))}
        />
      ) : (
        <div className="ws-row fd">
          <button className="ws-open" onClick={onToggle} aria-expanded={!collapsed} title={w.name}>
            <span aria-hidden="true">{collapsed ? "▸" : "▾"}</span> <span className="ws-name">{w.name}</span>{" "}
            <span className="faint">{w.items.length}</span>
          </button>
          <span className="acts">
            <button onClick={() => setEditing({ type: "rename-ws", id: w.workspace_id, name: w.name })} aria-label={`Rename workspace ${w.name}`} title="Rename">
              ✎
            </button>
            <button onClick={() => run("duplicate the workspace", saved.duplicateWorkspace(w.workspace_id))} aria-label={`Duplicate workspace ${w.name}`} title="Duplicate">
              ⧉
            </button>
            <button onClick={() => setEditing({ type: "delete-ws", id: w.workspace_id })} aria-label={`Delete workspace ${w.name}`} title="Delete">
              ×
            </button>
          </span>
        </div>
      )}
      {deleting && (
        <Confirm
          text={`Delete “${w.name}” and its ${w.items.length} ${w.items.length === 1 ? "item" : "items"}?`}
          onYes={() => run("delete the workspace", saved.deleteWorkspace(w.workspace_id))}
          onNo={() => setEditing(null)}
        />
      )}
      {!collapsed && w.items.length === 0 && <p className="side-note">Empty. Use Save on a chart, Compare or Companies.</p>}
      {!collapsed &&
        w.items.map((i) => (
          <Item key={i.item_id} item={i} on={openIds.has(i.item_id)} editing={editing} setEditing={setEditing} onOpen={onOpen} run={run} saved={saved} />
        ))}
    </div>
  );
}

function Item({
  item: i,
  on,
  editing,
  setEditing,
  onOpen,
  run,
  saved,
}: {
  item: SavedItem;
  on: boolean;
  editing: Editing;
  setEditing: (e: Editing) => void;
  onOpen: (itemId: string) => void;
  run: (what: string, p: Promise<unknown>) => void;
  saved: Saved;
}) {
  if (editing?.type === "rename-item" && editing.id === i.item_id) {
    return (
      <NameInput
        label="Item name"
        initial={i.name}
        max={ITEM_NAME_MAX}
        onCancel={() => setEditing(null)}
        onSubmit={(name) => run("rename it", saved.renameItem(i.item_id, name))}
      />
    );
  }
  return (
    <>
      <div className={on ? "ws-row it on" : "ws-row it"}>
        <button className="ws-open" onClick={() => onOpen(i.item_id)} title={`Open in ${KIND_PAGES[i.kind]}: ${i.name}`}>
          <span className={`tag ${i.kind}`}>{KIND_LABELS[i.kind]}</span> <span className="ws-name">{i.name}</span>
        </button>
        <span className="acts">
          <button onClick={() => setEditing({ type: "rename-item", id: i.item_id, name: i.name })} aria-label={`Rename ${i.name}`} title="Rename">
            ✎
          </button>
          <button onClick={() => run("duplicate it", saved.duplicateItem(i.item_id))} aria-label={`Duplicate ${i.name}`} title="Duplicate">
            ⧉
          </button>
          <button onClick={() => setEditing({ type: "delete-item", id: i.item_id })} aria-label={`Delete ${i.name}`} title="Delete">
            ×
          </button>
        </span>
      </div>
      {editing?.type === "delete-item" && editing.id === i.item_id && (
        <Confirm
          text={`Delete “${i.name}”?`}
          onYes={() => run("delete it", saved.deleteItem(i.item_id))}
          onNo={() => setEditing(null)}
        />
      )}
    </>
  );
}

function NameInput({
  label,
  initial,
  max,
  onSubmit,
  onCancel,
}: {
  label: string;
  initial: string;
  max: number;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const p = nameProblem(name, max);
    if (p) return setProblem(p);
    onSubmit(name);
  };
  return (
    <form className="ws-edit" onSubmit={submit}>
      <input
        aria-label={label}
        value={name}
        maxLength={max}
        autoFocus
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel()}
      />
      {problem && (
        <span className="err small" role="alert">
          {problem}
        </span>
      )}
    </form>
  );
}

function Confirm({ text, onYes, onNo }: { text: string; onYes: () => void; onNo: () => void }) {
  return (
    <div className="ws-confirm" role="alertdialog" aria-label={text}>
      <span className="grow">{text}</span>
      <button className="btn sm" onClick={onYes}>
        Delete
      </button>
      <button className="btn sm ghost" onClick={onNo}>
        Keep
      </button>
    </div>
  );
}
