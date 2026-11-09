import { useEffect, useState } from "react";

import { feedStatus } from "../api";

const REFRESH_MS = 5 * 60_000;

/** When the newest filing reached the database (public, `GET /v1/status`): the feed's own clock.
 * The filings Home lists have a later step behind them (their text is parsed, at once for tracked
 * companies and within about 6 hours for the rest), so neither time stands in for the other. */
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

/** Both clocks explained, wherever they're shown. */
export const FRESHNESS_EXPLAINED =
  "Listed: the filing is in Tributary's index, usually within two minutes of EDGAR accepting it. " +
  "Parsed: its text is in the reader, at once for tracked companies and within about 6 hours for the rest.";
