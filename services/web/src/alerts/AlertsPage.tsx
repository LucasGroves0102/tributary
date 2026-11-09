import { useState } from "react";

import { markAlertsRead } from "../api";
import { href } from "../route";
import type { AlertNotifications } from "../types";
import { NotificationList } from "./Notifications";
import { refreshAlerts, useAlertsUnread } from "./useAlerts";

/** Alerts (F6, #154): new 10-K, 10-Q and material 8-K filings of the companies the user watches
 * or analyses, newest first, from the Query API's alerts job. Opening the page marks what it shows
 * as read. Settings has the forms per company and the webhook. */
export function AlertsPage({ token }: { token: string }) {
  const unread = useAlertsUnread(token);
  const [epoch, setEpoch] = useState(0);
  const [loaded, setLoaded] = useState<AlertNotifications | null>(null);
  const check = unread?.last_check ?? null;
  return (
    <div className="page alerts-page">
      <div className="row page-bar">
        <h1 className="grow">Alerts</h1>
        <a className="btn ghost" href={`${href({ view: "settings" })}`}>Alert settings</a>
        <button className="btn" disabled={!loaded?.notifications.some((n) => !n.read_at) && !unread?.unread}
          onClick={() => void markAlertsRead(token, { all: true }).then(() => refreshAlerts(token)).then(() => setEpoch((n) => n + 1)).catch(() => undefined)}>
          Mark all read
        </button>
      </div>
      <p className="muted small">
        New 10-K, 10-Q and material 8-K filings of the companies on your watchlist and in your analyses, checked every 10 minutes
        {check ? `; last checked ${new Date(check.checked_at).toLocaleString()}` : ""}. A 10-K or 10-Q shows its key figures and your red
        lines once the SEC's XBRL data has them.
      </p>
      <section className="card">
        <NotificationList key={epoch} token={token} onLoaded={setLoaded} empty={
          <>
            No alerts yet. Filings of companies on your watchlist, or that you <a href={href({ view: "analysis" })}>analyse</a>, show here
            from the day you follow them. Choose which forms alert in <a href={href({ view: "settings" })}>Settings</a>.
          </>
        } />
      </section>
    </div>
  );
}
