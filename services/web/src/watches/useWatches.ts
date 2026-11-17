import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { deleteScreenWatch, putScreenWatch, screenWatches, type WatchBody } from "../api";
import type { ScreenWatch, ScreenWatches } from "../types";

export interface Watching {
  /** undefined while loading; null when the server has no watches (an older API, no app database) */
  list: ScreenWatches | null | undefined;
  /** the watch on a saved screen, if any */
  of: (itemId: string) => ScreenWatch | null;
  /** saved screens that are watched */
  watched: ReadonlySet<string>;
  save: (itemId: string, body: WatchBody) => Promise<ScreenWatch>;
  stop: (itemId: string) => Promise<void>;
  reload: () => void;
}

/** The user's watched screens (Query API /v1/watches), read once per token and after each change.
 * Everything goes through the API as the token's user; a watch grants nothing. */
export function useWatches(token: string | null): Watching {
  const [list, setList] = useState<ScreenWatches | null | undefined>(undefined);
  const [epoch, setEpoch] = useState(0);
  const tok = useRef(token);
  tok.current = token;

  useEffect(() => {
    setList(undefined);
    if (!token) return;
    let live = true;
    screenWatches(token)
      .then((l) => live && setList(l))
      .catch(() => live && setList(null)); // 503 (no app database): watching isn't offered
    return () => {
      live = false;
    };
  }, [token, epoch]);

  const byItem = useMemo(() => new Map((list?.watches ?? []).map((w) => [w.item_id, w])), [list]);
  const of = useCallback((itemId: string) => byItem.get(itemId) ?? null, [byItem]);
  const watched = useMemo(() => new Set(byItem.keys()), [byItem]);

  const save = useCallback(async (itemId: string, body: WatchBody) => {
    const { watch } = await putScreenWatch(tok.current!, itemId, body);
    setList((l) => l && { ...l, watches: [...l.watches.filter((w) => w.item_id !== itemId), watch] });
    return watch;
  }, []);

  const stop = useCallback(async (itemId: string) => {
    await deleteScreenWatch(tok.current!, itemId);
    setList((l) => l && { ...l, watches: l.watches.filter((w) => w.item_id !== itemId) });
  }, []);

  const reload = useCallback(() => setEpoch((n) => n + 1), []);
  return { list, of, watched, save, stop, reload };
}
