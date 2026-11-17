import { useState } from "react";

import { markAlertsRead } from "../api";
import { localTime } from "../format";
import { href } from "../route";
import type { AlertNotifications, ScreenAlerts } from "../types";
import { ScreenAlertList } from "../watches/ScreenAlerts";
import { NotificationList } from "./Notifications";
import { refreshAlerts, useAlertsUnread } from "./useAlerts";

/** Alerts (F6, #154): new 10-K, 10-Q and material 8-K filings of the companies the user watches
 * or analyses, newest first, from the Query API's alerts job; above them, what changed in the
 * user's watched screens after a night's figures (watches/ScreenAlerts). Opening the page marks
 * what it shows as read. Settings has the forms per company and the webhook. */
export function AlertsPage({ token }: { token: string }) {
  const unread = useAlertsUnread(token);
  const [epoch, setEpoch] = useState(0);
  const [loaded, setLoaded] = useState<AlertNotifications | null>(null);
  const [screens, setScreens] = useState<ScreenAlerts | null>(null);
  const unreadShown = !!loaded?.notifications.some((n) => !n.read_at) || !!screens?.alerts.some((a) => !a.read_at);
  const check = unread?.last_check ?? null;
  return (
    <div className="page alerts-page">
      <div className="row page-bar">
        <h1 className="grow">Alerts</h1>
        <a className="btn ghost" href={`${href({ view: "settings" })}`}>Alert settings</a>
        {/* once the list is here, it decides: nothing unread in it, nothing to mark (Riley 54) */}
        <button className="btn" disabled={loaded ? !unreadShown : !unread?.unread}
          onClick={() => void markAlertsRead(token, { all: true }).then(() => refreshAlerts(token)).then(() => setEpoch((n) => n + 1)).catch(() => undefined)}>
          Mark all read
        </button>
      </div>
      {(unread?.reviews_due ?? 0) > 0 && (
        <p className="review-due-note" role="status">
          {unread!.reviews_due === 1 ? "A holding has" : `${unread!.reviews_due} holdings have`} a review due: a new 10-K or 10-Q
          landed since your last review. <a href={href({ view: "portfolio" })}>Review in your portfolio</a>
        </p>
      )}
      <p className="muted small">
        New 10-K, 10-Q and material 8-K filings of the companies on your watchlist, in your analyses and in your portfolio, checked every 10 minutes
        {check ? `; last checked ${localTime(check.checked_at, true)}` : ""}. A 10-K or 10-Q shows its key figures and your red
        lines once the SEC's XBRL data has them.{screens !== null && " Your watched screens are checked once a day, after the night's figures land."}
      </p>
      <ScreenAlertList key={`s${epoch}`} token={token} onLoaded={setScreens} />
      {screens !== null && <h2 className="alerts-h">Filings</h2>}
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
