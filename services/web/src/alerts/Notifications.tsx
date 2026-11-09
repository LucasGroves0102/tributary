import { useCallback, useEffect, useState, type ReactNode } from "react";

import { alertNotifications, failureReason, markAlertsRead } from "../api";
import { useDirectory } from "../companies/directory";
import { ErrorPanel, Skeleton } from "../components/States";
import { localDay } from "../format";
import { href } from "../route";
import type { AlertNotification, AlertNotifications } from "../types";
import { alertLinks, figureChange, figuresNote, figureValue, headline, inDisplay, RED_LINE_WORDS, redLineState, ruleText, SOURCE_WORDS } from "./alerts";
import { refreshAlerts } from "./useAlerts";

type Load = { state: "loading" } | { state: "ok"; data: AlertNotifications } | { state: "off" } | { state: "error"; message: string };

/** Notifications, newest first: the whole list (Alerts page) or one company's (the Monitor step).
 * Each says the form, the date and period, the 8-K's items or the 10-K/10-Q's key figures with
 * their change on a year earlier, the user's red lines evaluated on it, and links to What changed,
 * Earnings, the reader and EDGAR. Opening the list marks what it shows as read. */
export function NotificationList({ token, companyId, empty, limit = 50, onLoaded }: {
  token: string;
  /** one company's alerts (the Monitor step); the company name is then left out */
  companyId?: string;
  /** what an empty list says */
  empty: ReactNode;
  limit?: number;
  onLoaded?: (data: AlertNotifications | null) => void;
}) {
  const directory = useDirectory(token);
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const markRead = useCallback((list: AlertNotification[]) => {
    const unread = list.filter((n) => !n.read_at).map((n) => n.id);
    if (!unread.length) return;
    markAlertsRead(token, { ids: unread })
      .then(() => refreshAlerts(token))
      .catch(() => undefined); // still unread; the bell says so
  }, [token]);
  useEffect(() => {
    let live = true;
    alertNotifications(token, { companyId, limit })
      .then((data) => {
        if (!live) return;
        onLoaded?.(data);
        if (!data) return setLoad({ state: "off" });
        setLoad({ state: "ok", data });
        markRead(data.notifications);
      })
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, companyId, limit, attempt]);

  if (load.state === "loading") return <Skeleton lines={4} label="Loading alerts" />;
  if (load.state === "off") return <p className="empty">Filing alerts aren't available on this server yet.</p>;
  if (load.state === "error") return <ErrorPanel what="your alerts" detail={load.message} onRetry={() => setAttempt((n) => n + 1)} />;
  const list = load.data.notifications;
  if (!list.length) return <div className="empty">{empty}</div>;
  return (
    <ol className="alert-list">
      {list.map((n) => (
        <li key={n.id} className={n.read_at ? "alert-item" : "alert-item unread"}>
          <div className="alert-head">
            <span className={`tag ${n.form === "8-K" ? "k" : "q"}`}>{n.form}</span>
            {!companyId && (
              <a className="alert-co" href={href({ view: "company", companyId: n.company_id })}>{directory.label(n.company_id)}</a>
            )}
            <span className="alert-what">{headline(n)}</span>
            {!n.read_at && <span className="tag new" title="New since you last looked">new</span>}
            {n.crossed > 0 && <span className="tag crossed">{n.crossed} red line{n.crossed === 1 ? "" : "s"} crossed</span>}
            <span className="grow" />
            <span className="faint small" title={`Noticed ${new Date(n.created_at).toLocaleString()}`}>
              {n.sources.map((s) => SOURCE_WORDS[s]).join(" · ")}
            </span>
          </div>
          {n.form === "8-K" && n.items.length > 0 && (
            <ul className="alert-items">
              {n.items.map((i) => <li key={i.item}><span className="mono">{i.item}</span> {i.title}</li>)}
            </ul>
          )}
          {n.figures && n.figures.length > 0 && (
            <dl className="alert-figures">
              {n.figures.map((f) => (
                <div key={f.metric} title={`${f.span === "ttm" ? "Fiscal year" : "Quarter"} ended ${localDay(f.period_end, true)}${f.year_ago ? `; a year earlier ${figureValue({ value: f.year_ago, unit: f.unit })}` : ""}`}>
                  <dt>{f.label}{f.span === "ttm" ? " (year)" : ""}</dt>
                  <dd>
                    {f.not_meaningful ? "n/m" : figureValue(f)}
                    {figureChange(f) && <span className={Number(f.change ?? 0) >= 0 ? "pos small" : "neg small"}> {figureChange(f)}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {n.red_lines.length > 0 && (
            <ul className="alert-redlines" aria-label="Your red lines on this filing">
              {n.red_lines.map((r) => {
                const state = redLineState(r);
                return (
                  <li key={r.rule_id} className={`redline ${state}`}>
                    <b>{RED_LINE_WORDS[state]}</b> {r.label} <span className="faint">({ruleText(r)}; {inDisplay(r.value, r.display, r.unit)})</span>
                  </li>
                );
              })}
            </ul>
          )}
          {figuresNote(n, load.data.pending_days) && <p className="faint small">{figuresNote(n, load.data.pending_days)}</p>}
          <div className="row wrap alert-links">
            {alertLinks(n).map((l) => (
              <a key={l.label} className="btn sm ghost" href={l.href} {...(l.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{l.label}</a>
            ))}
          </div>
        </li>
      ))}
    </ol>
  );
}
