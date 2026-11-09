/** Settings → Connections ("Connect your data"): the user adds or removes their own
 * market-data API key. The key is typed into a password field, sent once in the PUT body
 * (the server validates it with one provider call and stores it encrypted) and then cleared:
 * it is never kept in state after a successful save, in storage, or shown again. What the page
 * shows of a saved key is the provider, "…last4" and dates, as the API returns them.
 * Disconnecting deletes the key and every price fetched with it. */

import { useState, type FormEvent } from "react";

import { connectMarketData, disconnectMarketData, failureReason } from "../api";
import { Skeleton } from "../components/States";
import { day } from "../format";
import { refreshMarketData, useMarketData } from "../market/useMarketData";
import type { MarketConnection, MarketProvider } from "../types";

export function Settings({ token }: { token: string }) {
  const market = useMarketData(token);
  return (
    <div className="page settings-page">
      <h1>Settings</h1>
      <section className="card" aria-label="Connections">
        <div className="card-h">
          <h2 className="grow">Connections</h2>
          <span className="sub">bring your own market data</span>
        </div>
        <div className="card-b">
          {market === null ? (
            <Skeleton lines={3} label="Loading connections" />
          ) : !market.enabled ? (
            <p className="empty">No data connections are available on this server.</p>
          ) : (
            <>
              <p className="small muted">
                Share prices can't be shown publicly: data vendors license them for personal use. Connect your own
                account and Tributary fetches end-of-day prices with your key, stores them for you only, and uses them for
                your price charts and valuation multiples. Nobody else sees them. Your key is stored encrypted and is never
                shown again; disconnecting deletes it and every price fetched with it.
              </p>
              {market.providers.map((p) => (
                <Provider key={p.id} token={token} provider={p}
                  connection={market.connections.find((c) => c.provider === p.id) ?? null} />
              ))}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function Provider({ token, provider, connection }: { token: string; provider: MarketProvider; connection: MarketConnection | null }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const value = key.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await connectMarketData(token, provider.id, value);
      setKey("");
      setDone(`Connected to ${provider.name}.`);
      await refreshMarketData(token);
    } catch (err) {
      setError(failureReason(err));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await disconnectMarketData(token, provider.id);
      setDone(`Disconnected: your ${provider.name} key and its prices are deleted.`);
      await refreshMarketData(token);
    } catch (err) {
      setError(failureReason(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="connection" role="group" aria-label={provider.name}>
      <h3>{provider.name}</h3>
      <p className="small">
        {provider.data}.{" "}
        <a href={provider.signup_url} target="_blank" rel="noreferrer">Get your API key</a>
        {" · "}
        <a href={provider.terms_url} target="_blank" rel="noreferrer">Plans and terms</a>
      </p>
      {connection ? (
        <div className="row wrap">
          <span className="grow">
            Connected · key <code>{connection.key_hint}</code> · added {day(connection.created_at.slice(0, 10), true)}
          </span>
          <button className="btn" onClick={() => void disconnect()} disabled={busy}>
            Disconnect
          </button>
        </div>
      ) : null}
      <form onSubmit={(e) => void save(e)}>
        <input className="field" type="password" autoComplete="off" spellCheck={false} maxLength={256}
          aria-label={`${provider.name} API key`} placeholder={connection ? "Replace with a new key" : `Your ${provider.name} API key`}
          value={key} onChange={(e) => setKey(e.target.value)} disabled={busy} />
        <button className="btn primary" type="submit" disabled={busy || !key.trim()}>
          {busy ? "Checking…" : connection ? "Replace key" : "Connect"}
        </button>
      </form>
      {error && <p className="error small" role="alert">{error}</p>}
      {done && <p className="small" role="status">{done}</p>}
    </div>
  );
}
