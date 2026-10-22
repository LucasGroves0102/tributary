import { useCallback, useEffect, useRef, useState } from "react";

import type { ChartState } from "../chart/model";
import { go, parse } from "../route";
import type { UiAction } from "../types";
import { canSave, type Current } from "../workspaces/model";
import { applyChartAction, compareAfter, isChartAction, screenAfter } from "./actions";

/** Applied action, with what Undo needs: the chart and page from just before it, the
 * watchlist call to reverse (only if it changed anything), and the item a save created. */
interface Applied {
  id: number;
  label: string;
  before: { chart: ChartState | null; hash: string };
  watch: { companyId: string; added: boolean } | null;
  /** save_to_workspace: the save in flight, and the saved item's id once it lands */
  save: { pending: Promise<void>; itemId: string | null } | null;
  undone: boolean;
}

export interface UiActions {
  /** Applies an action from the agent; returns its id and label for the message. */
  apply: (a: UiAction) => { id: number; label: string };
  /** Undoes the action and every later one (newest first), and goes back to its page. */
  undo: (id: number) => Promise<void>;
  undone: ReadonlySet<number>;
}

interface Deps {
  setChart: (f: (s: ChartState) => ChartState) => void;
  /** the chart as last rendered */
  chart: () => ChartState;
  isWatched: (companyId: string) => boolean;
  watch: (companyId: string) => Promise<void>;
  unwatch: (companyId: string) => Promise<void>;
  /** saves what's on screen into the named workspace (found by name, or created); the item id */
  saveTo: (current: Current, name: string, workspace: string) => Promise<string>;
  deleteSaved: (itemId: string) => Promise<void>;
  onError: (message: string) => void;
}

const WHAT = { chart: "chart", comps: "comps table", screen: "screen" } as const;

/** The assistant's changes to the screen, each snapshotted so it can be undone (like the
 * prototype's aiSend/aiUndo). Watchlist changes go through the watchlist API, and are undone
 * by the inverse call; a save goes through the saved-work API and is undone by deleting the
 * item. Starts over when the user changes. */
export function useUiActions(user: string | null, deps: Deps): UiActions {
  const log = useRef<Applied[]>([]);
  const next = useRef(1);
  const [undone, setUndone] = useState<ReadonlySet<number>>(new Set());
  const d = useRef(deps);
  d.current = deps;
  // the chart after this answer's actions so far, before React renders it (a save reads it)
  const projected = useRef<{ from: ChartState; to: ChartState } | null>(null);

  useEffect(() => {
    log.current = [];
    projected.current = null;
    setUndone(new Set());
  }, [user]);

  const apply = useCallback((a: UiAction) => {
    const entry: Applied = {
      id: next.current++,
      label: a.label,
      before: { chart: null, hash: window.location.hash || "#/" },
      watch: null,
      save: null,
      undone: false,
    };
    log.current.push(entry);
    const { setChart, isWatched, watch, unwatch, saveTo, onError } = d.current;
    const rendered = d.current.chart();
    const p = projected.current;
    const chartNow = p && p.from === rendered ? p.to : rendered;
    if (isChartAction(a)) projected.current = { from: rendered, to: applyChartAction(chartNow, a) };
    // the chart as it is when this action applies, after any earlier ones in the same answer
    setChart((s) => {
      entry.before.chart ??= s;
      return isChartAction(a) ? applyChartAction(s, a) : s;
    });
    if (isChartAction(a)) {
      go({ view: "chart" });
    } else if (a.action === "set_screen_filters") {
      const now = parse(window.location.hash);
      const current = now.view === "companies" ? (now.screen ?? {}) : {};
      go({ view: "companies", screen: screenAfter(current, a.filters, a.reset) });
    } else if (a.action === "set_compare") {
      const now = parse(window.location.hash);
      go({ view: "compare", compare: compareAfter(now.view === "compare" ? (now.compare ?? {}) : {}, a) });
    } else if (a.action === "save_to_workspace") {
      const now = parse(window.location.hash);
      const current: Current | null =
        a.item === "chart"
          ? { kind: "chart", chart: chartNow }
          : a.item === "comps" && now.view === "compare"
            ? { kind: "comps", compare: now.compare ?? {} }
            : a.item === "screen" && now.view === "companies"
              ? { kind: "screen", screen: now.screen ?? {} }
              : null;
      if (!current || !canSave(current)) {
        onError(`Nothing to save as “${a.name}”: open the ${WHAT[a.item]} first.`);
      } else {
        const save: NonNullable<Applied["save"]> = { pending: Promise.resolve(), itemId: null };
        save.pending = saveTo(current, a.name, a.workspace)
          .then((itemId) => {
            save.itemId = itemId; // Undo waits for this, then deletes it
          })
          .catch(() => onError(`Couldn't save “${a.name}”. Try again with the Save button.`));
        entry.save = save;
      }
    } else {
      const add = a.action === "add_watchlist";
      if (isWatched(a.company_id) !== add) {
        entry.watch = { companyId: a.company_id, added: add };
        (add ? watch : unwatch)(a.company_id).catch(() => {
          entry.watch = null;
          onError(`Couldn't update the watchlist for ${a.company_label}. Try again.`);
        });
      }
    }
    return { id: entry.id, label: entry.label };
  }, []);

  const undo = useCallback(async (id: number) => {
    const target = log.current.find((x) => x.id === id);
    if (!target || target.undone) return;
    const { setChart, watch, unwatch, deleteSaved, onError } = d.current;
    const later = log.current.filter((x) => x.id >= id && !x.undone).reverse();
    for (const x of later) {
      x.undone = true;
      if (x.save) {
        await x.save.pending;
        if (x.save.itemId) {
          await deleteSaved(x.save.itemId).catch(() =>
            onError("Couldn't delete the saved item. Delete it from the sidebar."),
          );
        }
      }
      if (x.watch) {
        await (x.watch.added ? unwatch : watch)(x.watch.companyId).catch(() =>
          onError("Couldn't undo the watchlist change. Try again from Home."),
        );
      }
    }
    projected.current = null;
    if (target.before.chart) setChart(() => target.before.chart!);
    if ((window.location.hash || "#/") !== target.before.hash) window.location.hash = target.before.hash;
    setUndone(new Set(log.current.filter((x) => x.undone).map((x) => x.id)));
  }, []);

  return { apply, undo, undone };
}
