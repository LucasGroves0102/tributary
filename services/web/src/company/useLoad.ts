import { useEffect, useState } from "react";

import { ApiError } from "../api";

/** A request's state: denied is a 403 (no grant, or the plan's tier doesn't cover it). */
export type Load<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "denied" }
  | { state: "error"; message: string };

/** Runs `fetch` when `key` changes (null: don't run) and keeps only the latest answer. */
export function useLoad<T>(key: string | null, fetch: () => Promise<T>): Load<T> | null {
  const [out, setOut] = useState<Load<T> | null>(null);
  useEffect(() => {
    if (key === null) {
      setOut(null);
      return;
    }
    let live = true;
    setOut({ state: "loading" });
    fetch()
      .then((data) => live && setOut({ state: "ok", data }))
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 403) setOut({ state: "denied" });
        else setOut({ state: "error", message: e instanceof ApiError ? e.message : "the Query API didn't answer" });
      });
    return () => {
      live = false;
    };
    // fetch is a new closure every render; key says when it asks for something else
  }, [key]);
  return out;
}
