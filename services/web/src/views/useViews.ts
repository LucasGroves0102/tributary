import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, resetViewPref, saveViewPref, viewPrefs, type ViewName } from "../api";
import type { ColumnInfo, ViewPrefs } from "../types";
import {
  columnList,
  DEFAULT_SCREENER_COLUMNS,
  DEFAULT_WATCHLIST_COLUMNS,
  homeLayout,
  MAX_SCREENER_COLUMNS,
  MAX_WATCHLIST_COLUMNS,
  type HomeCard,
} from "./model";

export interface Views {
  /** false while loading, and when preferences are unavailable (the defaults apply) */
  ready: boolean;
  editable: boolean;
  home: HomeCard[];
  screenerColumns: string[];
  watchlistColumns: string[];
  /** every column the API offers (null until known) */
  options: ColumnInfo[] | null;
  max: { screener: number; watchlist: number };
  setHome: (cards: HomeCard[]) => void;
  setColumns: (view: "screener" | "watchlist", columns: string[]) => void;
  reset: (view: ViewName) => void;
  error: string | null;
}

type Saved = ViewPrefs["views"];
const EMPTY: Saved = { home: null, screener: null, watchlist: null };

/** The signed-in user's view preferences: read once, changed optimistically and saved at once
 * (a failed save puts the last saved state back and says so). */
export function useViews(token: string | null, user: string | null): Views {
  const [saved, setSaved] = useState<Saved>(EMPTY);
  const [options, setOptions] = useState<ViewPrefs["options"] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "unavailable">("loading");
  const [error, setError] = useState<string | null>(null);
  const confirmed = useRef<Saved>(EMPTY);

  useEffect(() => {
    setSaved(EMPTY);
    confirmed.current = EMPTY;
    setOptions(null);
    setState("loading");
    setError(null);
    if (!token) return;
    let live = true;
    viewPrefs(token)
      .then((p) => {
        if (!live) return;
        confirmed.current = p.views;
        setSaved(p.views);
        setOptions(p.options);
        setState("ok");
      })
      .catch(() => live && setState("unavailable"));
    return () => {
      live = false;
    };
  }, [token, user]);

  const write = useCallback(
    (view: ViewName, value: Saved[ViewName], request: () => Promise<unknown>) => {
      setSaved((s) => ({ ...s, [view]: value }));
      setError(null);
      request()
        .then(() => {
          confirmed.current = { ...confirmed.current, [view]: value };
        })
        .catch((e) => {
          setSaved((s) => ({ ...s, [view]: confirmed.current[view] }));
          setError(`Couldn't save your ${view} layout: ${e instanceof ApiError ? e.message : "the Query API didn't answer"}.`);
        });
    },
    [],
  );

  const setHome = useCallback(
    (cards: HomeCard[]) => {
      if (!token) return;
      const value = { cards: cards.map((c) => ({ id: c.id, hidden: c.hidden })) };
      write("home", value, () => saveViewPref(token, "home", value));
    },
    [token, write],
  );

  const setColumns = useCallback(
    (view: "screener" | "watchlist", columns: string[]) => {
      if (!token || !columns.length) return;
      const value = { columns };
      write(view, value, () => saveViewPref(token, view, value));
    },
    [token, write],
  );

  const reset = useCallback(
    (view: ViewName) => {
      if (!token) return;
      write(view, null, () => resetViewPref(token, view));
    },
    [token, write],
  );

  const max = options?.max_columns ?? { screener: MAX_SCREENER_COLUMNS, watchlist: MAX_WATCHLIST_COLUMNS };
  const columns = options?.columns ?? null;
  const home = useMemo(() => homeLayout(saved.home), [saved.home]);
  const screenerColumns = useMemo(
    () => columnList(saved.screener, columns, DEFAULT_SCREENER_COLUMNS, max.screener),
    [saved.screener, columns, max.screener],
  );
  const watchlistColumns = useMemo(
    () => columnList(saved.watchlist, columns, DEFAULT_WATCHLIST_COLUMNS, max.watchlist),
    [saved.watchlist, columns, max.watchlist],
  );

  return {
    ready: state === "ok",
    editable: state === "ok",
    home,
    screenerColumns,
    watchlistColumns,
    options: columns,
    max,
    setHome,
    setColumns,
    reset,
    error,
  };
}
