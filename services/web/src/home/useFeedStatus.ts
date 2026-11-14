import { useEffect, useState } from "react";

import { dataStatus, feedStatus } from "../api";

const REFRESH_MS = 5 * 60_000;

/** When the newest filing reached the database (public, `GET /v1/status`): the feed's own clock.
 * The filings Home lists have a later step behind them (their text is parsed: about a minute after
 * EDGAR for a 10-K, 10-Q or material 8-K of a company with financials, since doc-fetcher's live fetch
 * of 2026-11-09; other filings at the document crawl's next pass, within about 6 hours), so neither
 * time stands in for the other. */
export function useFeedStatus(on: boolean): string | null {
  const [listed, setListed] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let live = true;
    const read = () =>
      feedStatus()
        .then((s) => live && setListed(s.last_ingested_at))
        .catch(() => undefined);
    void read();
    const id = window.setInterval(() => void read(), REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [on]);
  return listed;
}

/** When the data-quality checks last ran anywhere: *checks last ran*, read from the same place as
 * Data status (`GET /v1/status/data` quality.last_checked_at, public, cached by the API). Home's
 * notes card once showed its companies' newest check under that name ("18 h ago" while Data status
 * said "3 min ago", analyst test 4 #24): a company is checked only when its figures change, so that
 * is *last checked*, a company's, never the run. Null until known, or when it can't be read. */
export function useChecksRan(on: boolean): string | null {
  const [ran, setRan] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let live = true;
    dataStatus()
      .then((s) => live && setRan(s.quality?.last_checked_at ?? null))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [on]);
  return ran;
}

/** Both clocks explained, wherever they're shown. */
export const FRESHNESS_EXPLAINED =
  "Listed: the filing is in Tributary's index, usually within two minutes of EDGAR accepting it. " +
  "Parsed: its text is in the reader, about a minute later for a 10-K, 10-Q or material 8-K of a " +
  "company with financials, and within about 6 hours for other filings.";
