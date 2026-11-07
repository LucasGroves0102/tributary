import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { failureReason } from "../api";
import type { SavedItem } from "../types";
import { ITEM_NAME_MAX, nameProblem, WORKSPACE_NAME_MAX } from "./model";
import type { Saved } from "./useSaved";

/** A name being edited in place: Enter saves, Escape cancels, a bad name is refused before the API. */
export function NameInput({
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
        className="field"
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

/** "Delete this?" over the whole screen, wherever it was asked from: the safe answer has the focus,
 * Escape or clicking away keeps it, and focus goes back where it was. On document.body so no
 * scroller or layout around the asker can clip it. */
export function ConfirmDelete({ text, onYes, onNo }: { text: string; onYes: () => void; onNo: () => void }) {
  const keep = useRef<HTMLButtonElement>(null);
  const del = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    keep.current?.focus();
    return () => before?.focus?.();
  }, []);
  const onKey = (e: ReactKeyboardEvent) => {
    e.stopPropagation();
    if (e.key === "Escape") onNo();
    else if (e.key === "Tab") {
      // two buttons: Tab goes between them and nowhere else
      e.preventDefault();
      (document.activeElement === keep.current ? del : keep).current?.focus();
    }
  };
  return createPortal(
    <div className="sheet-overlay" onClick={(e) => e.target === e.currentTarget && onNo()} onKeyDown={onKey}>
      <div className="card ws-confirm" role="alertdialog" aria-modal="true" aria-label={text}>
        <p>{text}</p>
        <p className="faint small">This can't be undone.</p>
        <div className="row">
          <span className="grow" />
          <button ref={del} className="btn danger" onClick={onYes}>
            Delete
          </button>
          <button ref={keep} className="btn" onClick={onNo}>
            Keep
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Rename, duplicate and delete on a workspace's own page, in the open, instead of only behind a
 * sidebar row. */
export function WorkspaceActions({
  workspace,
  saved,
  onDeleted,
  what = "workspace",
}: {
  workspace: { workspace_id: string; name: string; items: unknown[] };
  saved: Saved;
  onDeleted: () => void;
  /** "workspace" or "analysis": says what's being renamed or deleted */
  what?: string;
}) {
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = workspace.workspace_id;
  const n = workspace.items.length;
  const run = (doing: string, p: Promise<unknown>, done?: () => void) => {
    setError(null);
    setNote(null);
    p.then(() => {
      setMode(null);
      done?.();
    }).catch((e) => {
      setMode(null);
      setError(`Couldn't ${doing}: ${failureReason(e)}.`);
    });
  };
  return (
    <div className="row wrap ws-actions">
      {mode === "rename" ? (
        <NameInput
          label={`New name for the ${what}`}
          initial={workspace.name}
          max={WORKSPACE_NAME_MAX}
          onCancel={() => setMode(null)}
          onSubmit={(name) => run(`rename the ${what}`, saved.renameWorkspace(id, name))}
        />
      ) : (
        <button className="btn sm ghost" onClick={() => setMode("rename")}>Rename</button>
      )}
      <button className="btn sm ghost"
        onClick={() => run(`duplicate the ${what}`, saved.duplicateWorkspace(id), () => setNote(`Copied: “${workspace.name} (copy)” is in the sidebar.`))}>
        Duplicate
      </button>
      <button className="btn sm ghost" onClick={() => setMode("delete")}>Delete</button>
      {note && <span className="faint small" role="status">{note}</span>}
      {error && <span className="err small" role="alert">{error}</span>}
      {mode === "delete" && (
        <ConfirmDelete
          text={`Delete “${workspace.name}” and its ${n} ${n === 1 ? "item" : "items"}?`}
          onNo={() => setMode(null)}
          onYes={() => run(`delete the ${what}`, saved.deleteWorkspace(id), onDeleted)}
        />
      )}
    </div>
  );
}

/** The same for one saved item, on its workspace's list. */
export function ItemActions({ item, saved }: { item: Pick<SavedItem, "item_id" | "name">; saved: Saved }) {
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = (doing: string, p: Promise<unknown>) => {
    setError(null);
    p.then(() => setMode(null)).catch((e) => {
      setMode(null);
      setError(`Couldn't ${doing}: ${failureReason(e)}.`);
    });
  };
  return (
    <span className="ws-item-acts">
      {mode === "rename" ? (
        <NameInput
          label={`New name for ${item.name}`}
          initial={item.name}
          max={ITEM_NAME_MAX}
          onCancel={() => setMode(null)}
          onSubmit={(name) => run("rename it", saved.renameItem(item.item_id, name))}
        />
      ) : (
        <button className="btn sm ghost" onClick={() => setMode("rename")} aria-label={`Rename ${item.name}`}>Rename</button>
      )}
      <button className="btn sm ghost" onClick={() => run("duplicate it", saved.duplicateItem(item.item_id))} aria-label={`Duplicate ${item.name}`}>
        Duplicate
      </button>
      <button className="btn sm ghost" onClick={() => setMode("delete")} aria-label={`Delete ${item.name}`}>Delete</button>
      {error && <span className="err small" role="alert">{error}</span>}
      {mode === "delete" && (
        <ConfirmDelete
          text={`Delete “${item.name}”?`}
          onNo={() => setMode(null)}
          onYes={() => run("delete it", saved.deleteItem(item.item_id))}
        />
      )}
    </span>
  );
}
