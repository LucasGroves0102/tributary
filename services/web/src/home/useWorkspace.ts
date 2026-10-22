import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  expectedFilings,
  filingIndexOf,
  financials,
  latestFilings,
  unwatch as apiUnwatch,
  watch as apiWatch,
  watchlist as fetchWatchlist,
} from "../api";
import type { CalendarEntry, FinancialRow, IndexedFiling, LatestFiling, Watchlist } from "../types";
import {
  addDays,
  expectedItems,
  recentFilings,
  watchRows,
  type ExpectedItem,
  type FilingItem,
  type WatchRow,
} from "./data";
import { lastVisit } from "./lastVisit";

export const EXPECTED_DAYS = 45;
export const RECENT_DAYS = 90;
const SPARK_QUARTERS = 12;
const FEED_LIMIT = 50;
/** Forms the Home feed follows from the public index (insider and other forms stay on each
 * company's Filings tab). */
export const FEED_FORMS = ["10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT", "8-K", "8-K/A"];
/** The filing index route takes at most this many companies. */
const INDEX_COMPANIES = 50;

/** unavailable: the Query API has no app database for watchlists (503). */
export type WatchlistState =
  | { state: "loading" }
  | { state: "ok"; data: Watchlist }
  | { state: "unavailable" | "error"; message: string };

export interface Workspace {
  watchlist: WatchlistState;
  rows: WatchRow[];
  filings: FilingItem[] | null;
  expected: ExpectedItem[] | null;
  latest: LatestFiling[] | null;
  since: string;
  firstVisit: boolean;
  watch: (companyId: string) => Promise<void>;
  unwatch: (companyId: string) => Promise<void>;
  /** how many companies the user can read (Home's filing feed covers them all) */
  readableCount: number;
}

const today = () => new Date().toISOString().slice(0, 10);

/** The signed-in user's watchlist, recent filings and expected filings: Home and the sidebar
 * share it, so the watchlist is fetched once. companyIds: the companies the user can read
 * (/v1/me); Home follows their filing index along with the watchlist's. */
export function useWorkspace(token: string | null, user: string | null, companyIds: string[] = []): Workspace {
  const [list, setList] = useState<WatchlistState>({ state: "loading" });
  const [latest, setLatest] = useState<LatestFiling[] | null>(null);
  const [index, setIndex] = useState<IndexedFiling[] | null>(null);
  const [calendars, setCalendars] = useState<{
    watched: CalendarEntry[];
    readable: CalendarEntry[];
  } | null>(null);
  const [revenue, setRevenue] = useState<Record<string, FinancialRow[]>>({});
  const [version, setVersion] = useState(0);
  const visit = useMemo(() => (user ? lastVisit(user) : null), [user]);

  useEffect(() => {
    setList({ state: "loading" });
    setLatest(null);
    setIndex(null);
    setCalendars(null);
    setRevenue({});
  }, [token]);

  useEffect(() => {
    if (!token) return;
    let live = true;
    fetchWatchlist(token)
      .then((data) => live && setList({ state: "ok", data }))
      .catch(
        (e) =>
          live &&
          setList(
            e instanceof ApiError && e.status === 503
              ? { state: "unavailable", message: e.message }
              : {
                  state: "error",
                  message: (e instanceof ApiError && e.message) || "the Query API didn't answer",
                },
          ),
      );
    latestFilings(token, FEED_LIMIT)
      .then((r) => live && setLatest(r.filings))
      .catch(() => live && setLatest([]));
    const none = { filings: [] as CalendarEntry[] };
    Promise.all([
      expectedFilings(token, EXPECTED_DAYS, "watchlist").catch(() => none),
      expectedFilings(token, EXPECTED_DAYS, "readable").catch(() => none),
    ]).then(([w, r]) => {
      if (live) setCalendars({ watched: w.filings, readable: r.filings });
    });
    return () => {
      live = false;
    };
  }, [token, version]);

  // sparklines: quarterly revenue, only for watched companies the user can read
  const readableIds = list.state === "ok"
    ? list.data.companies.filter((c) => c.access.includes("financials")).map((c) => c.company_id)
    : [];
  const readableKey = readableIds.join(",");
  useEffect(() => {
    if (!token || !readableKey) return;
    let live = true;
    for (const id of readableKey.split(",")) {
      financials(token, id, SPARK_QUARTERS, ["revenue"])
        .then((r) => live && setRevenue((s) => ({ ...s, [id]: r.rows })))
        .catch(() => {});
    }
    return () => {
      live = false;
    };
  }, [token, readableKey]);

  const entries = useMemo(() => (list.state === "ok" ? list.data.companies : []), [list]);
  const since = visit?.since ?? new Date().toISOString();

  // the public filing index of watched companies first, then readable ones (the route's cap)
  const listLoaded = list.state !== "loading";
  const indexKey = [...new Set([...entries.map((e) => e.company_id), ...companyIds])]
    .slice(0, INDEX_COMPANIES)
    .join(",");
  useEffect(() => {
    if (!token || !listLoaded) return;
    if (!indexKey) {
      setIndex([]);
      return;
    }
    let live = true;
    filingIndexOf(token, indexKey.split(","), {
      forms: FEED_FORMS,
      from: addDays(today(), -RECENT_DAYS),
      limit: FEED_LIMIT,
    })
      .then((r) => live && setIndex(r.filings))
      .catch(() => live && setIndex([]));
    return () => {
      live = false;
    };
  }, [token, indexKey, listLoaded, version]);

  const filings = useMemo(
    () =>
      latest === null || index === null
        ? null
        : recentFilings(latest, entries, since, today(), RECENT_DAYS, index),
    [latest, index, entries, since],
  );
  const rows = useMemo(() => {
    const fresh = new Set((filings ?? []).filter((f) => f.isNew).map((f) => f.companyId));
    return watchRows(entries, revenue, fresh);
  }, [entries, revenue, filings]);
  const expected = useMemo(
    () =>
      calendars &&
      expectedItems(
        calendars.watched,
        calendars.readable,
        new Set(entries.map((e) => e.company_id)),
      ),
    [calendars, entries],
  );

  const watch = useCallback(
    async (id: string) => {
      if (!token) return;
      await apiWatch(token, id);
      setVersion((v) => v + 1);
    },
    [token],
  );
  const unwatch = useCallback(
    async (id: string) => {
      if (!token) return;
      await apiUnwatch(token, id);
      setVersion((v) => v + 1);
    },
    [token],
  );

  return {
    watchlist: list,
    rows,
    filings,
    expected,
    latest,
    since,
    firstVisit: visit?.first ?? false,
    watch,
    unwatch,
    readableCount: companyIds.length,
  };
}
