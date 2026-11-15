/** Settings → API keys: the user's personal access tokens for using Tributary from their own
 * scripts (Query API api_keys.py). Make one (a name, read or read and write, 30, 90 or 365 days),
 * see it once, list them with their prefix, dates and today's usage, revoke one after a confirm.
 *
 * The key comes back once, in the create answer: it stays in this component's state until the
 * user says they've saved it (or leaves), and never goes to storage, a URL, a log or the assistant's
 * context. The list shows only what the API returns of a key (its first 8 characters).
 *
 * Hidden when the API has no key routes (404: a Query API one release older, the deploy window)
 * and for a demo sandbox (`allowed` false: the API's rule). */

import { useEffect, useState, type FormEvent } from "react";

import { apiKeys, createApiKey, failureReason, revokeApiKey } from "../api";
import { Choices } from "../components/Choices";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { localDay, localTime } from "../format";
import { href } from "../route";
import type { ApiKey, ApiKeyCreated, ApiKeys, ApiKeyScope } from "../types";
import { ConfirmDelete } from "../workspaces/Actions";
import { LIMITS_TEXT, SCOPE_HELP, scopeWords, usageText } from "./apiKeys";

type Load = { state: "loading" } | { state: "ok"; data: ApiKeys } | { state: "off" } | { state: "error"; message: string };

const NAME_MAX = 60;

export function ApiKeysSection({ token }: { token: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  // the new key, this once: never stored, gone when dismissed or when Settings closes
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  useEffect(() => {
    let live = true;
    apiKeys(token)
      .then((data) => live && setLoad(data && data.allowed ? { state: "ok", data } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);
  const reload = () => setAttempt((n) => n + 1);

  if (load.state === "off") return null;
  return (
    <section className="card api-keys" aria-labelledby="api-keys-h" id="api-keys">
      <div className="card-h">
        <h2 id="api-keys-h" className="grow">API keys</h2>
        <span className="sub">use Tributary from your own scripts</span>
      </div>
      <div className="card-b">
        {load.state === "loading" ? <Skeleton lines={3} label="Loading your API keys" />
          : load.state === "error" ? <ErrorPanel what="your API keys" detail={load.message} onRetry={reload} />
          : (
            <>
              <p className="small muted api-keys-lead">
                A key lets a script read what you can read here, as you: the same figures with the same citations, through the same
                permission checks. Each key makes up to {LIMITS_TEXT(load.data.limits)}. No key can change your account: making keys, alerts
                and webhooks stay here. <a href={href({ view: "api" })}>How to use a key</a>
              </p>
              {created && <NewKey created={created} onDone={() => setCreated(null)} />}
              <KeyList token={token} keys={load.data.keys} onChanged={reload} />
              <NewKeyForm token={token} data={load.data} onCreated={(c) => {
                setCreated(c);
                reload();
              }} />
            </>
          )}
      </div>
    </section>
  );
}

function NewKey({ created, onDone }: { created: ApiKeyCreated; onDone: () => void }) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const copy = () => {
    const write = navigator.clipboard?.writeText(created.key);
    if (!write) return setCopied("no");
    write.then(() => setCopied("yes"), () => setCopied("no"));
  };
  return (
    <div className="report-status secret-once api-key-once" role="status">
      <p>
        <b>Your new key, {created.api_key.name}.</b> Copy it now and keep it somewhere safe: you won't see it again. Anyone who has it can
        read {created.api_key.scopes.includes("write") ? "and change " : ""}what you can until it expires on{" "}
        {localDay(created.api_key.expires_at, true)} or you revoke it.
      </p>
      <div className="row wrap">
        <code className="secret" aria-label="Your new API key">{created.key}</code>
        <button className="btn sm" onClick={copy}>{copied === "yes" ? "Copied" : "Copy"}</button>
        <button className="btn sm ghost" onClick={onDone}>I've saved it</button>
      </div>
      {copied === "no" && <p className="small muted">This browser didn't let the page copy it: select the key and copy it yourself.</p>}
    </div>
  );
}

function KeyList({ token, keys, onChanged }: { token: string; keys: ApiKey[]; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<ApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (!keys.length) return <p className="empty">You have no API keys yet.</p>;
  const revoke = async (key: ApiKey) => {
    setConfirm(null);
    setError(null);
    setNote(null);
    try {
      await revokeApiKey(token, key.key_id);
      setNote(`Revoked ${key.name}: scripts using it are refused from now on.`);
      onChanged();
    } catch (e) {
      setError(`Couldn't revoke ${key.name}: ${failureReason(e)}.`);
    }
  };
  return (
    <>
      <TableScroll label="Your API keys">
        <table className="t api-key-list">
          <thead>
            <tr>
              <th className="first">Name</th>
              <th>Key</th>
              <th>Access</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Expires</th>
              <th className="n">Today</th>
              <th><span className="visually-hidden">Revoke</span></th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.key_id} className={k.status === "active" ? undefined : "off"}>
                <td className="first">{k.name}</td>
                <td><code className="key-prefix">{k.prefix}…</code></td>
                <td>{scopeWords(k.scopes)}</td>
                <td>{localDay(k.created_at, true)}</td>
                <td>{k.last_used_at ? localTime(k.last_used_at) : <span className="faint">Never</span>}</td>
                <td>
                  {k.status === "revoked" && k.revoked_at ? `Revoked ${localDay(k.revoked_at, true)}`
                    : k.status === "expired" ? `Expired ${localDay(k.expires_at, true)}`
                    : localDay(k.expires_at, true)}
                </td>
                <td className="n">{usageText(k.today)}</td>
                <td className="revoke">
                  {k.status === "active" && (
                    <button className="btn sm ghost" aria-label={`Revoke the key ${k.name}`} onClick={() => setConfirm(k)}>Revoke</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="faint small">Today counts requests since midnight UTC, when each key's daily limit starts again.</p>
      {confirm && (
        <ConfirmDelete text={`Revoke the key ${confirm.name}?`} yes="Revoke"
          detail="Scripts using it are refused from their next request. This can't be undone: make a new key to replace it."
          onYes={() => void revoke(confirm)} onNo={() => setConfirm(null)} />
      )}
      {error && <p className="err small" role="alert">{error}</p>}
      {note && <p className="small" role="status">{note}</p>}
    </>
  );
}

function NewKeyForm({ token, data, onCreated }: { token: string; data: ApiKeys; onCreated: (c: ApiKeyCreated) => void }) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState<ApiKeyScope>("read");
  const [days, setDays] = useState(String(data.default_expiry_days));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = data.keys.filter((k) => k.status === "active").length;
  if (active >= data.max_active) {
    return <p className="faint small">You have {data.max_active} active keys, the most there can be: revoke one to make another.</p>;
  }
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = name.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const made = await createApiKey(token, {
        name: value, scopes: scope === "write" ? ["read", "write"] : ["read"], expires_in_days: Number(days),
      });
      setName("");
      onCreated(made);
    } catch (err) {
      setError(failureReason(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="api-key-form" onSubmit={(e) => void submit(e)} aria-labelledby="api-key-new-h">
      <h3 id="api-key-new-h">New key</h3>
      <div className="api-key-fields">
        <label className="api-key-field name">
          <span>Name</span>
          <input className="field" value={name} maxLength={NAME_MAX} autoComplete="off" spellCheck={false}
            placeholder="What it's for: a notebook, a nightly job" onChange={(e) => setName(e.target.value)} disabled={busy}
            aria-invalid={!!error} aria-describedby={error ? "api-key-error" : undefined} />
        </label>
        <div className="api-key-field">
          <span id="api-key-scope">Access</span>
          <Choices value={scope} label="Access" set={setScope}
            options={[["read", "Read only"], ["write", "Read and write"]]} />
          <span className="faint small">{SCOPE_HELP[scope]}</span>
        </div>
        <div className="api-key-field">
          <span>Expires in</span>
          <Choices value={days} label="Expires in" set={setDays}
            options={data.expiry_days.map((d) => [String(d), `${d} days`] as [string, string])} />
        </div>
      </div>
      <div className="row">
        <button className="btn primary" type="submit" disabled={busy || !name.trim()}>{busy ? "Making the key…" : "Create key"}</button>
        <span className="faint small">{active} of {data.max_active} active</span>
      </div>
      {error && <p className="err small" id="api-key-error" role="alert">Refused: {error}.</p>}
    </form>
  );
}
