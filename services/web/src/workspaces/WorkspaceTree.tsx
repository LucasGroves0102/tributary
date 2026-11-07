import { useState } from "react";

import { failureReason } from "../api";
import { Skeleton } from "../components/States";
import { href } from "../route";
import type { SavedItem, SavedWorkspace } from "../types";
import { ConfirmDelete, NameInput } from "./Actions";
import { ITEM_NAME_MAX, KIND_LABELS, KIND_PAGES, WORKSPACE_NAME_MAX } from "./model";
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

const why = (e: unknown) => failureReason(e);

/** The sidebar's Workspaces: folders of saved charts, comps, screens and reports. The arrow folds a
 * folder; its name opens the workspace page (or the analysis), where rename, duplicate and delete
 * have a proper place (workspaces/Actions.tsx). An item's name opens the item. "⋯" on a row is the
 * shortcut to the same actions: a strip under the row, so the name never gives up room for them. */
export function WorkspaceTree({ saved, onOpen, onError }: Props) {
  const [editing, setEditing] = useState<Editing>(null);
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set());
  const [menu, setMenu] = useState<string | null>(null);
  const { list, open } = saved;
  const openIds = new Set(Object.values(open).map((o) => o?.itemId));

  const run = (what: string, p: Promise<unknown>) => {
    setMenu(null);
    return p.then(() => setEditing(null)).catch((e) => {
      setEditing(null);
      onError(`Couldn't ${what}: ${why(e)}.`);
    });
  };
  const edit = (e: Editing) => {
    setMenu(null);
    setEditing(e);
  };

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
            onSubmit={(name) => void run("create the workspace", saved.createWorkspace(name))}
          />
        )}
        {list.state === "loading" && <Skeleton lines={2} label="Loading workspaces" />}
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
              edit={edit}
              menu={menu}
              setMenu={setMenu}
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

/** An action strip opened at the bottom of the list isn't left below the fold. */
const inView = (el: HTMLElement | null) => el?.scrollIntoView?.({ block: "nearest" });

/** "⋯": on a row's edge over its own background, shown when the row is hovered or focused (always on
 * touch screens), and drawn, because system fonts don't all have these glyphs. */
function Dots({ open, label, onClick }: { open: boolean; label: string; onClick: () => void }) {
  return (
    <button className="ws-dots" aria-label={label} aria-expanded={open} onClick={onClick} title="Rename, duplicate, delete">
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <circle cx="2.5" cy="7" r="1.3" fill="currentColor" />
        <circle cx="7" cy="7" r="1.3" fill="currentColor" />
        <circle cx="11.5" cy="7" r="1.3" fill="currentColor" />
      </svg>
    </button>
  );
}

function Folder({
  workspace: w,
  collapsed,
  openIds,
  editing,
  edit,
  menu,
  setMenu,
  onToggle,
  onOpen,
  run,
  saved,
}: {
  workspace: SavedWorkspace;
  collapsed: boolean;
  openIds: Set<string | undefined>;
  editing: Editing;
  edit: (e: Editing) => void;
  menu: string | null;
  setMenu: (m: string | null) => void;
  onToggle: () => void;
  onOpen: (itemId: string) => void;
  run: (what: string, p: Promise<unknown>) => void;
  saved: Saved;
}) {
  const renaming = editing?.type === "rename-ws" && editing.id === w.workspace_id;
  const deleting = editing?.type === "delete-ws" && editing.id === w.workspace_id;
  const key = `ws:${w.workspace_id}`;
  const target = w.project
    ? href({ view: "analysis", projectId: w.workspace_id })
    : href({ view: "workspace", workspaceId: w.workspace_id });
  return (
    <div className="folder">
      {renaming ? (
        <NameInput
          label="Workspace name"
          initial={w.name}
          max={WORKSPACE_NAME_MAX}
          onCancel={() => edit(null)}
          onSubmit={(name) => run("rename the workspace", saved.renameWorkspace(w.workspace_id, name))}
        />
      ) : (
        <div className="ws-row fd">
          <button className="ws-fold" onClick={onToggle} aria-expanded={!collapsed}
            aria-label={`${collapsed ? "Show" : "Hide"} the items of ${w.name}`}>
            <span aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
          </button>
          <a className="ws-open" href={target} title={w.project ? `Open the analysis: ${w.name}` : `Open ${w.name}: items and reports`}>
            <span className="ws-name">{w.name}</span>{" "}
            {w.project && <span className="tag analysis" title="An analysis project">A</span>}{" "}
            <span className="faint">{w.items.length}</span>
          </a>
          <Dots open={menu === key} label={`Actions for workspace ${w.name}`} onClick={() => setMenu(menu === key ? null : key)} />
        </div>
      )}
      {menu === key && (
        <div className="ws-menu" ref={inView}>
          <button onClick={() => edit({ type: "rename-ws", id: w.workspace_id, name: w.name })} aria-label={`Rename workspace ${w.name}`}>
            Rename
          </button>
          <button onClick={() => run("duplicate the workspace", saved.duplicateWorkspace(w.workspace_id))} aria-label={`Duplicate workspace ${w.name}`}>
            Duplicate
          </button>
          <button onClick={() => edit({ type: "delete-ws", id: w.workspace_id })} aria-label={`Delete workspace ${w.name}`}>
            Delete
          </button>
        </div>
      )}
      {deleting && (
        <ConfirmDelete
          text={`Delete “${w.name}” and its ${w.items.length} ${w.items.length === 1 ? "item" : "items"}?`}
          onYes={() => run("delete the workspace", saved.deleteWorkspace(w.workspace_id))}
          onNo={() => edit(null)}
        />
      )}
      {!collapsed && w.items.length === 0 && <p className="side-note">Empty. Use Save on a chart, Compare or Companies.</p>}
      {!collapsed &&
        w.items.map((i) => (
          <Item key={i.item_id} item={i} on={openIds.has(i.item_id)} editing={editing} edit={edit} menu={menu} setMenu={setMenu} onOpen={onOpen} run={run} saved={saved} />
        ))}
    </div>
  );
}

function Item({
  item: i,
  on,
  editing,
  edit,
  menu,
  setMenu,
  onOpen,
  run,
  saved,
}: {
  item: SavedItem;
  on: boolean;
  editing: Editing;
  edit: (e: Editing) => void;
  menu: string | null;
  setMenu: (m: string | null) => void;
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
        onCancel={() => edit(null)}
        onSubmit={(name) => run("rename it", saved.renameItem(i.item_id, name))}
      />
    );
  }
  const key = `item:${i.item_id}`;
  return (
    <>
      <div className={on ? "ws-row it on" : "ws-row it"}>
        <button className="ws-open" onClick={() => onOpen(i.item_id)} title={`Open in ${KIND_PAGES[i.kind]}: ${i.name}`}>
          <span className={`tag ${i.kind}`}>{KIND_LABELS[i.kind]}</span> <span className="ws-name">{i.name}</span>
        </button>
        <Dots open={menu === key} label={`Actions for ${i.name}`} onClick={() => setMenu(menu === key ? null : key)} />
      </div>
      {menu === key && (
        <div className="ws-menu it" ref={inView}>
          <button onClick={() => edit({ type: "rename-item", id: i.item_id, name: i.name })} aria-label={`Rename ${i.name}`}>
            Rename
          </button>
          <button onClick={() => run("duplicate it", saved.duplicateItem(i.item_id))} aria-label={`Duplicate ${i.name}`}>
            Duplicate
          </button>
          <button onClick={() => edit({ type: "delete-item", id: i.item_id })} aria-label={`Delete ${i.name}`}>
            Delete
          </button>
        </div>
      )}
      {editing?.type === "delete-item" && editing.id === i.item_id && (
        <ConfirmDelete
          text={`Delete “${i.name}”?`}
          onYes={() => run("delete it", saved.deleteItem(i.item_id))}
          onNo={() => edit(null)}
        />
      )}
    </>
  );
}
