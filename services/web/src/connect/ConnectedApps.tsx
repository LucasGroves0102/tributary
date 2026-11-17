/** Settings → Connected apps: the apps (Claude, ChatGPT, another MCP client) connected to this account
 * through "Connect" in the app (Query API oauth.py), each with who published it, the host it sends the
 * user back to, when it was connected and last used; Disconnect, after a confirm, ends every token it
 * holds at once. Hidden for a demo sandbox (`allowed` false) and for an API without connected apps (404:
 * the deploy window, or OAuth off). Nothing secret is shown: the API sends no token, id or URL of one. */

import { useEffect, useState } from "react";

import { connectedApps, disconnectApp, failureReason } from "../api";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { localDay, localTime } from "../format";
import type { ConnectedApp, ConnectedApps } from "../types";
import { ConfirmDelete } from "../workspaces/Actions";
import { READ_ONLY } from "./connect";

type Load = { state: "loading" } | { state: "ok"; data: ConnectedApps } | { state: "off" } | { state: "error"; message: string };

export function ConnectedAppsSection({ token }: { token: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [confirm, setConfirm] = useState<ConnectedApp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    connectedApps(token)
      .then((data) => live && setLoad(data && data.allowed ? { state: "ok", data } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);
  const reload = () => setAttempt((n) => n + 1);

  if (load.state === "off") return null;
  const disconnect = async (app: ConnectedApp) => {
    setConfirm(null);
    setError(null);
    setNote(null);
    try {
      await disconnectApp(token, app.connection_id);
      setNote(`Disconnected ${app.name}: it can't read Tributary any more.`);
      reload();
    } catch (e) {
      setError(`Couldn't disconnect ${app.name}: ${failureReason(e)}.`);
    }
  };
  return (
    <section className="card connected-apps" aria-labelledby="connected-apps-h" id="connected-apps">
      <div className="card-h">
        <h2 id="connected-apps-h" className="grow">Connected apps</h2>
        <span className="sub">Claude, ChatGPT and other AI apps you let in</span>
      </div>
      <div className="card-b">
        {load.state === "loading" ? <Skeleton lines={2} label="Loading your connected apps" />
          : load.state === "error" ? <ErrorPanel what="your connected apps" detail={load.message} onRetry={reload} />
          : (
            <>
              <p className="small muted connected-apps-lead">
                Add Tributary to Claude, ChatGPT or another AI app by its address, {window.location.origin}/mcp: the app sends
                you here to sign in and approve it, with no key to copy. A connected app reads Tributary's data as you,{" "}
                {READ_ONLY}.
              </p>
              {load.data.connections.length === 0 ? (
                <p className="empty">No apps are connected.</p>
              ) : (
                <TableScroll label="Your connected apps">
                  <table className="t connected-app-list">
                    <thead>
                      <tr>
                        <th className="first">App</th>
                        <th>Sends you back to</th>
                        <th>Connected</th>
                        <th>Last used</th>
                        <th><span className="visually-hidden">Disconnect</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {load.data.connections.map((a) => (
                        <tr key={a.connection_id}>
                          <td className="first">
                            {a.name}
                            {a.publisher && <span className="unit">published by {a.publisher}</span>}
                          </td>
                          <td>{a.redirect_hosts.join(", ") || "—"}</td>
                          <td>{localDay(a.connected_at, true)}</td>
                          <td>{a.last_used_at ? localTime(a.last_used_at) : <span className="faint">Not yet</span>}</td>
                          <td className="revoke">
                            <button className="btn sm ghost" aria-label={`Disconnect ${a.name}`} onClick={() => setConfirm(a)}>
                              Disconnect
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
              )}
            </>
          )}
        {confirm && (
          <ConfirmDelete text={`Disconnect ${confirm.name}?`} yes="Disconnect"
            detail="It is refused from its next request. To use it again, connect it again from the app."
            onYes={() => void disconnect(confirm)} onNo={() => setConfirm(null)} />
        )}
        {error && <p className="err small" role="alert">{error}</p>}
        {note && <p className="small" role="status">{note}</p>}
      </div>
    </section>
  );
}
