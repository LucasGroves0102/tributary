import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { addItem, ApiError, createWorkspace, deleteItem as apiDeleteItem, deleteWorkspace, duplicateItem as apiDuplicateItem, duplicateWorkspace, failureReason, openItem, readFiling, recentWork, renameWorkspace, updateItem, workspaces as fetchWorkspaces } from "../api";
import { load, save as store } from "../storage";
import type { RecentWork, SavedItemFull, SavedKind, SavedWorkspace, WrittenKind } from "../types";
import {
  findWorkspace,
  fingerprint,
  isPageKind,
  restoreOpen,
  stateOf,
  type Current,
  type OpenItem,
  type OpenItems,
} from "./model";

/** unavailable: the Query API has no app database (503). */
export type SavedList =
  | { state: "loading" }
  | { state: "ok"; workspaces: SavedWorkspace[] }
  | { state: "unavailable" | "error"; message: string };

/** Where Save as puts the item: an existing workspace, or a new one by name. */
export type Target = { workspaceId: string } | { newWorkspace: string };

/** How long a deleted workspace or item can be brought back (#158). The delete is sent when this
 * runs out, or at once when the page is closed or the user changes, so nothing is ever deleted
 * that the user could still undo, and nothing confirmed is kept by closing the tab. */
export const UNDO_MS = 10_000;

/** A confirmed delete waiting out UNDO_MS: hidden from the lists, not yet sent. */
export interface PendingDelete {
  kind: "workspace" | "item";
  id: string;
  /** what goes, in words: “Semis” and its 3 items */
  what: string;
}

export interface Saved {
  list: SavedList;
  recent: RecentWork | null;
  /** the saved item each page has open (the chart builder, Compare, Companies) */
  open: OpenItems;
  createWorkspace: (name: string) => Promise<SavedWorkspace>;
  renameWorkspace: (id: string, name: string) => Promise<void>;
  deleteWorkspace: (id: string) => Promise<void>;
  duplicateWorkspace: (id: string) => Promise<void>;
  renameItem: (id: string, name: string) => Promise<void>;
  duplicateItem: (id: string) => Promise<void>;
  deleteItem: (id: string) => Promise<void>;
  /** A delete the user confirmed: hidden at once, sent after UNDO_MS unless undone (#158).
   * `deleteWorkspace`/`deleteItem` stay immediate for the assistant's own Undo of a save. */
  deleteWithUndo: (kind: PendingDelete["kind"], id: string) => void;
  undoDelete: (id: string) => void;
  pendingDeletes: PendingDelete[];
  /** a delete that failed once its undo window ran out (the thing is back in the list) */
  deleteFailure: string | null;
  dismissDeleteFailure: () => void;
  /** saves what's on screen as a new item, which becomes the page's open item */
  saveAs: (current: Current, name: string, target: Target) => Promise<SavedItemFull>;
  /** saves over the page's open item */
  save: (current: Current) => Promise<void>;
  /** a new thesis or model (its own page edits it), or a chart or comps set saved from
   * elsewhere than their page (the Revenue view, an analysis project): it doesn't become that
   * page's open item */
  create: (kind: WrittenKind | "chart" | "comps", name: string, state: object, target: Target) => Promise<SavedItemFull>;
  /** saves a thesis or model over (state and/or name) */
  update: (id: string, patch: { name?: string; state?: object }) => Promise<SavedItemFull>;
  /** fetches an item's state (recorded as opened) and makes it the page's open item */
  openItem: (id: string) => Promise<SavedItemFull>;
  /** the page no longer edits a saved item (a new chart, other comps) */
  close: (kind: SavedKind) => void;
  recordFiling: (companyId: string, accessionNo: string, sectionKey: string | null) => void;
  /** reads the list and recent work again (a report was saved elsewhere) */
  reload: () => void;
  /** bumps whenever the list is read again */
  version: number;
}

const key = (user: string) => `tributary.open.${user}`;

function storedOpen(user: string | null): OpenItems {
  if (!user) return {};
  try {
    return restoreOpen(JSON.parse(load(key(user)) ?? "null"));
  } catch {
    return {};
  }
}

const RECENT = 8;

/** The user's workspaces (sidebar), recent work (Home) and the item each page has open.
 * Everything goes through the Query API as the token's user; nothing here grants access. */
export function useSaved(token: string | null, user: string | null): Saved {
  const [list, setList] = useState<SavedList>({ state: "loading" });
  const [recent, setRecent] = useState<RecentWork | null>(null);
  const [open, setOpen] = useState<{ user: string | null; items: OpenItems }>(() => ({
    user,
    items: storedOpen(user),
  }));
  const [version, setVersion] = useState(0);
  const [recentVersion, setRecentVersion] = useState(0);
  const tok = useRef(token);
  tok.current = token;
  const listRef = useRef<SavedWorkspace[]>([]);

  useEffect(() => {
    setList({ state: "loading" });
    setRecent(null);
    setOpen({ user, items: storedOpen(user) });
  }, [token, user]);

  useEffect(() => {
    if (!token) return;
    let live = true;
    fetchWorkspaces(token)
      .then((w) => {
        if (!live) return;
        listRef.current = w;
        setList({ state: "ok", workspaces: w });
      })
      .catch(
        (e) =>
          live &&
          setList(
            e instanceof ApiError && e.status === 503
              ? { state: "unavailable", message: e.message }
              : { state: "error", message: failureReason(e) },
          ),
      );
    return () => {
      live = false;
    };
  }, [token, version]);

  useEffect(() => {
    if (!token) return;
    let live = true;
    recentWork(token, RECENT)
      .then((r) => live && setRecent(r))
      .catch(() => live && setRecent({ items: [], filings: [] }));
    return () => {
      live = false;
    };
  }, [token, version, recentVersion]);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  const setOpenItems = useCallback(
    (f: (o: OpenItems) => OpenItems) =>
      setOpen((cur) => {
        const items = f(cur.user === user ? cur.items : storedOpen(user));
        if (user) store(key(user), JSON.stringify(items));
        return { user, items };
      }),
    [user],
  );

  const need = () => {
    if (!tok.current) throw new ApiError("not signed in", 401);
    return tok.current;
  };

  /** Drops open items matching `drop` (deleted elsewhere). */
  const forget = useCallback(
    (drop: (o: OpenItem) => boolean) =>
      setOpenItems((o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v && !drop(v))) as OpenItems),
    [setOpenItems],
  );

  const create = useCallback(async (name: string) => {
    const w = await createWorkspace(need(), name.trim());
    refresh();
    return w;
  }, [refresh]);

  const target = useCallback(async (t: Target): Promise<{ id: string; name: string }> => {
    if ("workspaceId" in t) {
      const w = listRef.current.find((x) => x.workspace_id === t.workspaceId);
      return { id: t.workspaceId, name: w?.name ?? "" };
    }
    // the latest list, so two saves by name don't create the workspace twice
    const fresh = await fetchWorkspaces(need());
    listRef.current = fresh;
    const found = findWorkspace(fresh, t.newWorkspace);
    if (found) return { id: found.workspace_id, name: found.name };
    const w = await createWorkspace(need(), t.newWorkspace.trim());
    listRef.current = [...fresh, w];
    return { id: w.workspace_id, name: w.name };
  }, []);

  const saveAs = useCallback(
    async (current: Current, name: string, t: Target) => {
      const ws = await target(t);
      const state = stateOf(current);
      const item = await addItem(need(), ws.id, current.kind, name.trim(), state);
      setOpenItems((o) => ({
        ...o,
        [current.kind]: {
          itemId: item.item_id,
          workspaceId: ws.id,
          workspaceName: ws.name,
          name: item.name,
          saved: fingerprint(current.kind, item.state),
        },
      }));
      refresh();
      return item;
    },
    [target, setOpenItems, refresh],
  );

  const save = useCallback(
    async (current: Current) => {
      const o = open.user === user ? open.items[current.kind] : undefined;
      if (!o) throw new Error("nothing open to save over");
      try {
        const item = await updateItem(need(), o.itemId, { state: stateOf(current) });
        setOpenItems((x) => ({ ...x, [current.kind]: { ...o, name: item.name, saved: fingerprint(current.kind, item.state) } }));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) forget((x) => x.itemId === o.itemId);
        throw e;
      }
      refresh();
    },
    [open, user, setOpenItems, forget, refresh],
  );

  const openOne = useCallback(
    async (id: string) => {
      let item: SavedItemFull;
      try {
        item = await openItem(need(), id);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          forget((o) => o.itemId === id);
          refresh();
        }
        throw e;
      }
      const kind = item.kind;
      // reports, theses and models have their own pages: nothing to keep open
      if (isPageKind(kind)) {
        setOpenItems((o) => ({
          ...o,
          [kind]: {
            itemId: item.item_id,
            workspaceId: item.workspace_id,
            workspaceName: item.workspace_name ?? "",
            name: item.name,
            saved: fingerprint(kind, item.state),
          },
        }));
      }
      refresh();
      return item;
    },
    [setOpenItems, forget, refresh],
  );

  const createItem = useCallback(
    async (kind: WrittenKind | "chart" | "comps", name: string, state: object, t: Target) => {
      const ws = await target(t);
      const item = await addItem(need(), ws.id, kind, name.trim(), state);
      refresh();
      return { ...item, workspace_name: ws.name };
    },
    [target, refresh],
  );

  const update = useCallback(
    async (id: string, patch: { name?: string; state?: object }) => {
      const item = await updateItem(need(), id, patch.name === undefined ? patch : { ...patch, name: patch.name.trim() });
      refresh();
      return item;
    },
    [refresh],
  );

  const renameWs = useCallback(async (id: string, name: string) => {
    const w = await renameWorkspace(need(), id, name.trim());
    setOpenItems((o) =>
      Object.fromEntries(
        Object.entries(o).map(([k, v]) => [k, v && v.workspaceId === id ? { ...v, workspaceName: w.name } : v]),
      ) as OpenItems,
    );
    refresh();
  }, [setOpenItems, refresh]);

  const deleteWs = useCallback(async (id: string) => {
    await deleteWorkspace(need(), id);
    forget((o) => o.workspaceId === id);
    refresh();
  }, [forget, refresh]);

  const duplicateWs = useCallback(async (id: string) => {
    await duplicateWorkspace(need(), id);
    refresh();
  }, [refresh]);

  const renameItem = useCallback(async (id: string, name: string) => {
    const item = await updateItem(need(), id, { name: name.trim() });
    setOpenItems((o) =>
      Object.fromEntries(
        Object.entries(o).map(([k, v]) => [k, v && v.itemId === id ? { ...v, name: item.name } : v]),
      ) as OpenItems,
    );
    refresh();
  }, [setOpenItems, refresh]);

  const duplicateItem = useCallback(async (id: string) => {
    await apiDuplicateItem(need(), id);
    refresh();
  }, [refresh]);

  const deleteItem = useCallback(async (id: string) => {
    try {
      await apiDeleteItem(need(), id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e; // already gone
    }
    forget((o) => o.itemId === id);
    refresh();
  }, [forget, refresh]);

  // #158: confirmed deletes wait UNDO_MS, hidden; a timer (or a flush) sends them
  const [pendingDeletes, setPendingDeletes] = useState<PendingDelete[]>([]);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);
  const waiting = useRef(new Map<string, { timer: ReturnType<typeof setTimeout>; send: (keepalive: boolean) => void }>());

  const deleteWithUndo = useCallback((kind: PendingDelete["kind"], id: string) => {
    const token = tok.current;
    if (!token || waiting.current.has(id)) return;
    const ws = listRef.current;
    let what = "it";
    if (kind === "workspace") {
      const w = ws.find((x) => x.workspace_id === id);
      const n = w?.items.length ?? 0;
      what = w ? `“${w.name}” and its ${n} ${n === 1 ? "item" : "items"}` : "the workspace";
    } else {
      const it = ws.flatMap((w) => w.items).find((i) => i.item_id === id);
      what = it ? `“${it.name}”` : "the item";
    }
    const p: PendingDelete = { kind, id, what };
    const send = (keepalive: boolean) => {
      const entry = waiting.current.get(id);
      if (!entry) return; // undone, or sent already
      clearTimeout(entry.timer);
      waiting.current.delete(id);
      const call = kind === "workspace" ? deleteWorkspace(token, id, { keepalive }) : apiDeleteItem(token, id, { keepalive });
      call
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) return; // gone already
          setDeleteFailure(`Couldn't delete ${what}: ${failureReason(e)}. It's back in the list.`);
          throw e;
        })
        .then(() => forget((o) => (kind === "workspace" ? o.workspaceId === id : o.itemId === id)))
        .catch(() => undefined)
        .finally(() => {
          setPendingDeletes((ps) => ps.filter((x) => x.id !== id));
          refresh();
        });
    };
    waiting.current.set(id, { timer: setTimeout(() => send(false), UNDO_MS), send });
    setPendingDeletes((ps) => [...ps.filter((x) => x.id !== id), p]);
  }, [forget, refresh]);

  const undoDelete = useCallback((id: string) => {
    const entry = waiting.current.get(id);
    if (!entry) return;
    clearTimeout(entry.timer);
    waiting.current.delete(id);
    setPendingDeletes((ps) => ps.filter((x) => x.id !== id));
  }, []);

  // closing the page, or another user signing in, sends what's waiting: the user confirmed it
  useEffect(() => {
    const flush = () => {
      for (const { send } of [...waiting.current.values()]) send(true);
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [token]);

  const close = useCallback(
    (kind: SavedKind) =>
      setOpenItems((o) => {
        if (!o[kind]) return o;
        const next = { ...o };
        delete next[kind];
        return next;
      }),
    [setOpenItems],
  );

  const recordFiling = useCallback((companyId: string, accessionNo: string, sectionKey: string | null) => {
    if (!tok.current) return;
    readFiling(tok.current, companyId, accessionNo, sectionKey)
      .then(() => setRecentVersion((v) => v + 1))
      .catch(() => undefined);
  }, []);

  const gone = useMemo(() => new Set(pendingDeletes.map((p) => p.id)), [pendingDeletes]);
  const shownList = useMemo<SavedList>(() => {
    if (list.state !== "ok" || !gone.size) return list;
    return {
      state: "ok",
      workspaces: list.workspaces
        .filter((w) => !gone.has(w.workspace_id))
        .map((w) => (w.items.some((i) => gone.has(i.item_id)) ? { ...w, items: w.items.filter((i) => !gone.has(i.item_id)) } : w)),
    };
  }, [list, gone]);
  const shownRecent = useMemo(
    () => (recent && gone.size
      ? { ...recent, items: recent.items.filter((i) => !gone.has(i.item_id) && !gone.has(i.workspace_id)) }
      : recent),
    [recent, gone],
  );

  return {
    list: shownList,
    recent: shownRecent,
    deleteWithUndo,
    undoDelete,
    pendingDeletes,
    deleteFailure,
    dismissDeleteFailure: () => setDeleteFailure(null),
    open: open.user === user ? open.items : storedOpen(user),
    createWorkspace: create,
    renameWorkspace: renameWs,
    deleteWorkspace: deleteWs,
    duplicateWorkspace: duplicateWs,
    renameItem,
    duplicateItem,
    deleteItem,
    saveAs,
    save,
    create: createItem,
    update,
    openItem: openOne,
    close,
    recordFiling,
    reload: refresh,
    version,
  };
}
