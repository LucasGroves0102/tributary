import { Fragment, useCallback, useEffect, useState } from "react";

import { failureReason, markAlertsRead, screenAlerts } from "../api";
import { refreshAlerts } from "../alerts/useAlerts";
import { useDirectory } from "../companies/directory";
import { ErrorPanel, Skeleton } from "../components/States";
import { localTime } from "../format";
import { href } from "../route";
import type { ScreenAlert, ScreenAlerts } from "../types";
import { alertHeadline, crossingFigure, moreWords } from "./watches";

type Load = { state: "loading" } | { state: "ok"; data: ScreenAlerts } | { state: "off" } | { state: "error"; message: string };

/** The Alerts page's watched screens: each alert the alerts job made after a night's figures,
 * newest first: the companies that crossed a line (with the value), entered or left, each a link,
 * and a link back to the screen. Only companies the user still reads are listed (`hidden` counts
 * the rest). Opening the list marks what it shows as read. Nothing from an older API. */
export function ScreenAlertList({ token, onLoaded }: {
  token: string;
  onLoaded?: (data: ScreenAlerts | null) => void;
}) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const markRead = useCallback((list: ScreenAlert[]) => {
    const unread = list.filter((a) => !a.read_at).map((a) => a.id);
    if (!unread.length) return;
    markAlertsRead(token, { ids: unread })
      .then(() => refreshAlerts(token))
      .catch(() => undefined); // still unread; the bell says so
  }, [token]);
  useEffect(() => {
    let live = true;
    screenAlerts(token, { limit: 50 })
      .then((data) => {
        if (!live) return;
        onLoaded?.(data);
        if (!data) return setLoad({ state: "off" });
        setLoad({ state: "ok", data });
        markRead(data.alerts);
      })
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);

  if (load.state === "off") return null;
  return (
    <>
      <h2 className="alerts-h">Your watched screens</h2>
      <section className="card">
        {load.state === "loading" ? <Skeleton lines={2} label="Loading your screens' alerts" />
          : load.state === "error" ? <ErrorPanel what="your screens' alerts" detail={load.message} onRetry={() => setAttempt((n) => n + 1)} />
            : <Alerts token={token} list={load.data.alerts} />}
      </section>
    </>
  );
}

function Alerts({ token, list }: { token: string; list: ScreenAlert[] }) {
  const directory = useDirectory(token);
  if (!list.length) {
    return (
      <p className="empty">
        No screen alerts yet. Open a saved screen under <a href={href({ view: "companies" })}>Companies</a> and choose
        Watch to hear when companies enter or leave it, or cross a line you set.
      </p>
    );
  }
  const co = (id: string) => <a href={href({ view: "company", companyId: id })}>{directory.label(id)}</a>;
  const names = (ids: string[], more: number) => (
    <>
      {ids.map((id, i) => <Fragment key={id}>{i > 0 && ", "}{co(id)}</Fragment>)}
      {more > 0 && `${ids.length ? " " : ""}${moreWords(more)}`}
    </>
  );
  return (
    <ol className="alert-list" aria-label="Alerts from your watched screens">
      {list.map((a) => (
        <li key={a.id} className={a.read_at ? "alert-item" : "alert-item unread"}>
          <div className="alert-head">
            <a className="alert-co" href={href({ view: "screen", itemId: a.item_id })}>{a.name}</a>
            <span className="alert-what">{alertHeadline(a)}</span>
            {!a.read_at && <span className="tag new" title="New since you last looked">new</span>}
            <span className="grow" />
            <span className="faint small">{localTime(a.created_at, true)}</span>
          </div>
          {(a.crossed ?? []).map((x) => (
            <p key={x.line} className="screen-alert-line">
              <b>{x.label} {x.direction === "above" ? "rose above" : "fell below"} {crossingFigure(x, x.threshold)}:</b>{" "}
              {x.companies.map((c, i) => (
                <Fragment key={c.company_id}>
                  {i > 0 && "; "}
                  {co(c.company_id)} <span className="figure">{crossingFigure(x, c.value)}</span>
                </Fragment>
              ))}
              {x.more > 0 && ` ${moreWords(x.more)}`}
            </p>
          ))}
          {a.entered && <p className="screen-alert-line"><b>Entered:</b> {names(a.entered.companies, a.entered.more)}</p>}
          {a.left && <p className="screen-alert-line"><b>Left:</b> {names(a.left.companies, a.left.more)}</p>}
          {a.hidden > 0 && (
            <p className="faint small">
              {a.hidden} more {a.hidden === 1 ? "company" : "companies"} you can no longer read {a.hidden === 1 ? "isn't" : "aren't"} listed.
            </p>
          )}
          <div className="row wrap alert-links">
            <a className="btn sm ghost" href={href({ view: "screen", itemId: a.item_id })}>Open the screen</a>
          </div>
        </li>
      ))}
    </ol>
  );
}
