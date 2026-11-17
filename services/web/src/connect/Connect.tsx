/** "Connect an app": the consent screen (#/connect?request=...). An app (Claude, ChatGPT, another MCP
 * client) sent the user here through the Query API's /oauth/authorize; this page shows what the API
 * says about the request (the app's name, who published it, the host it sends the user back to, a
 * loopback warning, what it may do) and passes Approve or Deny on. Both answer with the app's redirect
 * URI, which the browser follows only when `safeRedirect` agrees. A demo sandbox sees why it can't
 * connect and may only go back. Shown without the app's shell, like the front door. */

import { useEffect, useState } from "react";

import { accountName } from "../access";
import { connectRequest, decideConnect, failureReason } from "../api";
import { Seal } from "../components/Seal";
import { Skeleton } from "../components/States";
import { href } from "../route";
import type { ConnectRequest, Me } from "../types";
import { CAN, CANT, clearPending, connectError, publisherWords, READ_ONLY, safeRedirect } from "./connect";

type Load =
  | { state: "loading" }
  | { state: "ok"; data: ConnectRequest }
  | { state: "error"; message: string };

interface Props {
  token: string;
  me: Me | null;
  /** the sealed request from the address, or the code of a problem /oauth/authorize found */
  request?: string;
  error?: string;
  /** how the browser leaves for the app (tests replace it) */
  leave?: (url: string) => void;
}

export function Connect({ token, me, request, error, leave = (url) => window.location.assign(url) }: Props) {
  const [load, setLoad] = useState<Load>(() =>
    request ? { state: "loading" } : { state: "error", message: connectError(error ?? "request") });
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    clearPending(); // signed in and here: nothing left to come back to
    if (!request) return;
    let live = true;
    connectRequest(token, request)
      .then((data) => live && setLoad({ state: "ok", data }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, request]);

  const decide = async (approve: boolean) => {
    if (!request || busy) return;
    setBusy(approve ? "approve" : "deny");
    setProblem(null);
    try {
      const { redirect } = await decideConnect(token, request, approve);
      if (!safeRedirect(redirect)) {
        setProblem("Tributary won't send you to that address. Start connecting again from the app.");
        setBusy(null);
        return;
      }
      setLeaving(new URL(redirect).hostname);
      leave(redirect);
    } catch (e) {
      setProblem(`Couldn't ${approve ? "connect the app" : "tell the app"}: ${failureReason(e)}.`);
      setBusy(null);
    }
  };

  return (
    <div className="connect-sheet" aria-labelledby="connect-title">
      <p className="welcome-brand connect-brand">
        <Seal size="logo" />
        <span className="wordmark">Tributary</span>
      </p>
      {load.state === "loading" ? (
        <>
          <h1 id="connect-title" className="connect-title">Connect an app</h1>
          <Skeleton lines={4} label="Reading the app's request" />
        </>
      ) : load.state === "error" ? (
        <>
          <h1 id="connect-title" className="connect-title">This app can't connect</h1>
          <p className="connect-lead" role="alert">{sentence(load.message)}</p>
          <p className="connect-foot"><a href={href({ view: "home" })}>Go to Tributary</a></p>
        </>
      ) : (
        <Consent data={load.data} me={me} busy={busy} leaving={leaving} problem={problem} decide={(a) => void decide(a)} />
      )}
    </div>
  );
}

function Consent({ data, me, busy, leaving, problem, decide }: {
  data: ConnectRequest;
  me: Me | null;
  busy: "approve" | "deny" | null;
  leaving: string | null;
  problem: string | null;
  decide: (approve: boolean) => void;
}) {
  const c = data.client;
  const host = c.redirect_host ?? "the app";
  return (
    <>
      <h1 id="connect-title" className="connect-title">Connect {c.name} to Tributary</h1>
      <p className="connect-lead">{c.name} asks to read Tributary's data as you, {READ_ONLY}.</p>
      <dl className="connect-facts">
        <div>
          <dt>App</dt>
          <dd>
            <span className="connect-app">{c.name}</span>
            <span className="connect-sub">{publisherWords(c)}</span>
          </dd>
        </div>
        <div>
          <dt>Sends you back to</dt>
          <dd>
            <span className="connect-host">{host}</span>
            {c.loopback && (
              <span className="connect-warn">
                An app on this computer. Approve only if you started connecting it yourself, just now: any program here could
                ask in its name.
              </span>
            )}
          </dd>
        </div>
        {me && (
          <div>
            <dt>Signed in as</dt>
            <dd>{accountName(me)}</dd>
          </div>
        )}
      </dl>
      <div className="connect-scope">
        <section aria-labelledby="connect-can">
          <h2 id="connect-can" className="connect-sect">It can</h2>
          <ul>{CAN.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>
        <section aria-labelledby="connect-cant">
          <h2 id="connect-cant" className="connect-sect">It can't</h2>
          <ul>{CANT.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>
      </div>
      {leaving ? (
        <p className="connect-leaving" role="status">Taking you back to {leaving}…</p>
      ) : data.allowed ? (
        <div className="connect-actions">
          <button className="btn primary" disabled={busy !== null} onClick={() => decide(true)}>
            {busy === "approve" ? "Connecting…" : "Approve"}
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => decide(false)}>Deny</button>
        </div>
      ) : (
        <div className="connect-actions">
          <p className="welcome-notice" role="status">
            Demo sessions can't connect apps. Sign in with GitHub or with an account to connect {c.name}.
          </p>
          <button className="btn" disabled={busy !== null} onClick={() => decide(false)}>Go back to {host}</button>
        </div>
      )}
      {problem && <p className="welcome-error" role="alert">{problem}</p>}
      <p className="connect-foot">You can disconnect it any time in Settings, Connected apps.</p>
    </>
  );
}

function sentence(text: string): string {
  const t = text.trim();
  return /[.!?]$/.test(t) ? t : `${t.charAt(0).toUpperCase()}${t.slice(1)}.`;
}
