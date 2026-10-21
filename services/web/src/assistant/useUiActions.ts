import { useCallback, useEffect, useRef, useState } from "react";

import type { ChartState } from "../chart/model";
import { go, parse } from "../route";
import type { UiAction } from "../types";
import { applyChartAction, isChartAction, screenAfter } from "./actions";

/** Applied action, with what Undo needs: the chart and page from just before it, and the
 * watchlist call to reverse (only if it changed anything). */
interface Applied {
  id: number;
  label: string;
  before: { chart: ChartState | null; hash: string };
  watch: { companyId: string; added: boolean } | null;
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
  isWatched: (companyId: string) => boolean;
  watch: (companyId: string) => Promise<void>;
  unwatch: (companyId: string) => Promise<void>;
  onError: (message: string) => void;
}

/** The assistant's changes to the screen, each snapshotted so it can be undone (like the
 * prototype's aiSend/aiUndo). Watchlist changes go through the watchlist API, and are undone
 * by the inverse call. Starts over when the user changes. */
export function useUiActions(user: string | null, deps: Deps): UiActions {
  const log = useRef<Applied[]>([]);
  const next = useRef(1);
  const [undone, setUndone] = useState<ReadonlySet<number>>(new Set());
  const d = useRef(deps);
  d.current = deps;

  useEffect(() => {
    log.current = [];
    setUndone(new Set());
  }, [user]);

  const apply = useCallback((a: UiAction) => {
    const entry: Applied = {
      id: next.current++,
      label: a.label,
      before: { chart: null, hash: window.location.hash || "#/" },
      watch: null,
      undone: false,
    };
    log.current.push(entry);
    const { setChart, isWatched, watch, unwatch, onError } = d.current;
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
    const { setChart, watch, unwatch, onError } = d.current;
    const later = log.current.filter((x) => x.id >= id && !x.undone).reverse();
    for (const x of later) {
      x.undone = true;
      if (x.watch) {
        await (x.watch.added ? unwatch : watch)(x.watch.companyId).catch(() =>
          onError("Couldn't undo the watchlist change. Try again from Home."),
        );
      }
    }
    if (target.before.chart) setChart(() => target.before.chart!);
    if ((window.location.hash || "#/") !== target.before.hash) window.location.hash = target.before.hash;
    setUndone(new Set(log.current.filter((x) => x.undone).map((x) => x.id)));
  }, []);

  return { apply, undo, undone };
}
