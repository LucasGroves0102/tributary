/** Settings → Filing alerts (F6): which forms alert for each followed company, the webhooks the
 * alerts also go to, and their delivery log. Owner-only like everything in Settings; the API
 * takes the user from the token.
 *
 * A webhook URL is typed once, sent in the POST body and cleared from the form; the page shows
 * only what the API returns of it (scheme, host and its last four characters). The signing secret
 * comes back once, with the new or rotated webhook: it stays in this component's state until the
 * user dismisses it or leaves, never in storage, a URL or a log. The API refuses anything but
 * https to a public address and says why (query_api/webhooks.py); the form shows that sentence. */

import { useEffect, useState, type FormEvent } from "react";

import {
  addWebhook,
  alertSettings,
  deleteWebhook,
  failureReason,
  putAlertForms,
  rotateWebhook,
  setWebhookEnabled,
  testWebhook,
  webhookDeliveries,
} from "../api";
import { useDirectory } from "../companies/directory";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { day, localDay } from "../format";
import { href } from "../route";
import type { AlertForm, AlertSettings, Webhook, WebhookDelivery, WebhookFormat } from "../types";
import { deliveryText, FORM_WORDS, FORMS, SOURCE_WORDS } from "./alerts";

type Load = { state: "loading" } | { state: "ok"; data: AlertSettings } | { state: "off" } | { state: "error"; message: string };

const FORMAT_WORDS: Record<WebhookFormat, string> = {
  json: "JSON (signed event)",
  discord: "Discord channel",
  slack: "Slack channel",
};

export function AlertSettingsSection({ token }: { token: string }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    alertSettings(token)
      .then((data) => live && setLoad(data ? { state: "ok", data } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);
  const reload = () => setAttempt((n) => n + 1);

  return (
    <section className="card" aria-labelledby="alerts-h" id="alerts">
      <div className="card-h">
        <h2 id="alerts-h" className="grow">Filing alerts</h2>
        <span className="sub">new 10-K, 10-Q and material 8-K filings of the companies you follow</span>
      </div>
      <div className="card-b">
        {load.state === "loading" ? <Skeleton lines={4} label="Loading alert settings" />
          : load.state === "off" ? <p className="empty">Filing alerts aren't available on this server yet.</p>
          : load.state === "error" ? <ErrorPanel what="your alert settings" detail={load.message} onRetry={reload} />
          : (
            <>
              <p className="small muted">
                Tributary checks {load.data.schedule} for new filings of the companies on your watchlist and the companies you're
                analysing. A 10-K or 10-Q alerts with its key figures and your red lines once the SEC's XBRL data has them; an 8-K when
                it reports a material item (votes and exhibits alone don't). See them under <a href={href({ view: "alerts" })}>Alerts</a>.
                {load.data.last_check ? ` Last checked ${new Date(load.data.last_check.checked_at).toLocaleString()}.` : " Not checked yet."}
              </p>
              <Companies token={token} settings={load.data} />
              <Webhooks token={token} settings={load.data} onChanged={reload} />
              <h3>Email</h3>
              <p className="small muted">
                {load.data.email.available ? "Email alerts are available." : "Email alerts aren't set up on this server, so alerts arrive here and at your webhook."}
              </p>
            </>
          )}
      </div>
    </section>
  );
}

function Companies({ token, settings }: { token: string; settings: AlertSettings }) {
  const directory = useDirectory(token);
  const [forms, setForms] = useState<Record<string, AlertForm[]>>(() =>
    Object.fromEntries(settings.companies.map((c) => [c.company_id, c.forms])));
  const [error, setError] = useState<string | null>(null);
  const toggle = (companyId: string, form: AlertForm) => {
    const before = forms[companyId] ?? FORMS;
    const next = FORMS.filter((f) => (f === form ? !before.includes(f) : before.includes(f)));
    setForms((m) => ({ ...m, [companyId]: next }));
    setError(null);
    putAlertForms(token, companyId, next).catch((e) => {
      setForms((m) => ({ ...m, [companyId]: before }));
      setError(`Couldn't save that: ${failureReason(e)}.`);
    });
  };
  if (!settings.companies.length) {
    return (
      <p className="empty">
        You don't follow any company yet. Add companies to your watchlist on <a href={href({ view: "home" })}>Home</a>, or{" "}
        <a href={href({ view: "analysis" })}>start an analysis</a>: their filings alert here.
      </p>
    );
  }
  return (
    <>
      <TableScroll label="Alerts per company">
        <table className="t alert-forms">
          <thead>
            <tr>
              <th className="first">Company</th>
              <th>Why</th>
              {FORMS.map((f) => <th key={f} className="n" title={FORM_WORDS[f]}>{f}</th>)}
            </tr>
          </thead>
          <tbody>
            {settings.companies.map((c) => (
              <tr key={c.company_id}>
                <td className="first"><a href={href({ view: "company", companyId: c.company_id })}>{directory.label(c.company_id)}</a></td>
                <td className="muted small">{c.sources.map((s) => SOURCE_WORDS[s]).join(" · ")} · since {day(c.since, true)}</td>
                {FORMS.map((f) => (
                  <td key={f} className="n">
                    <input type="checkbox" checked={(forms[c.company_id] ?? FORMS).includes(f)}
                      aria-label={`${f} alerts for ${directory.label(c.company_id)}`} onChange={() => toggle(c.company_id, f)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {error && <p className="err small" role="alert">{error}</p>}
    </>
  );
}

function Webhooks({ token, settings, onChanged }: { token: string; settings: AlertSettings; onChanged: () => void }) {
  const [url, setUrl] = useState("");
  const [format, setFormat] = useState<WebhookFormat | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ webhook: Webhook; secret: string; rotated: boolean } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [log, setLog] = useState<WebhookDelivery[] | null>(null);

  useEffect(() => {
    let live = true;
    webhookDeliveries(token).then((r) => live && setLog(r.deliveries)).catch(() => live && setLog([]));
    return () => {
      live = false;
    };
  }, [token, settings]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const value = url.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await addWebhook(token, value, format || undefined);
      setUrl(""); // the URL carries the channel's token: don't keep it once it's saved
      setSecret({ webhook: r.webhook, secret: r.secret, rotated: false });
      onChanged();
    } catch (err) {
      setError(failureReason(err));
    } finally {
      setBusy(false);
    }
  };
  const act = async (what: () => Promise<unknown>, done?: string) => {
    setError(null);
    setNote(null);
    try {
      await what();
      if (done) setNote(done);
      onChanged();
    } catch (err) {
      setError(failureReason(err));
    }
  };
  const full = settings.webhooks.length >= settings.max_webhooks;

  return (
    <>
      <h3>Webhook</h3>
      <p className="small muted">
        Alerts can also go to a Discord or Slack channel, or any https:// endpoint of yours. Each request is signed:{" "}
        <code>X-Tributary-Signature: v1=</code>HMAC-SHA256 of <code>timestamp.body</code> with your secret. Private and local
        addresses are refused, redirects aren't followed, and a failed delivery is tried again four times over about nine hours.
      </p>
      {secret && (
        <div className="report-status secret-once" role="status">
          <p>
            {secret.rotated ? "New signing secret" : "Webhook added"} for <code>{secret.webhook.url}</code>. Copy the signing secret now: it
            won't be shown again.
          </p>
          <code className="secret" aria-label="Signing secret">{secret.secret}</code>{" "}
          <button className="btn sm" onClick={() => void navigator.clipboard?.writeText(secret.secret)}>Copy</button>{" "}
          <button className="btn sm ghost" onClick={() => setSecret(null)}>I've saved it</button>
        </div>
      )}
      {settings.webhooks.length > 0 && (
        <ul className="webhook-list">
          {settings.webhooks.map((w) => (
            <li key={w.id} className={w.enabled ? undefined : "off"}>
              <span className="grow">
                <code>{w.url}</code> <span className="faint small">· {FORMAT_WORDS[w.format]} · added {localDay(w.created_at, true)}</span>
                {w.last_delivery && (
                  <span className="faint small"> · last: {w.last_delivery.status}{w.last_delivery.last_status ? ` (${w.last_delivery.last_status})` : ""}</span>
                )}
              </span>
              <label className="chk small">
                <input type="checkbox" checked={w.enabled} onChange={() => void act(() => setWebhookEnabled(token, w.id, !w.enabled))} />
                On
              </label>
              <button className="btn sm" onClick={() => void act(async () => {
                const r = await testWebhook(token, w.id);
                setNote(r.ok ? "Test sent: it arrived." : `Test sent, not accepted: ${r.delivery ? deliveryText(r.delivery).replace(/^Failed: /, "") : "no answer"}.`);
              })}>Send a test</button>
              <button className="btn sm ghost" onClick={() => void act(async () => {
                const r = await rotateWebhook(token, w.id);
                setSecret({ webhook: r.webhook, secret: r.secret, rotated: true });
              })}>New secret</button>
              <button className="btn sm ghost" aria-label={`Remove the webhook ${w.url}`} onClick={() => void act(() => deleteWebhook(token, w.id), "Webhook removed.")}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {!full ? (
        <form className="row wrap webhook-form" onSubmit={(e) => void add(e)}>
          <label className="visually-hidden" htmlFor="webhook-url">Webhook URL</label>
          <input id="webhook-url" className="field grow" type="url" inputMode="url" autoComplete="off" spellCheck={false} maxLength={2048}
            placeholder="https://discord.com/api/webhooks/…" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy}
            aria-invalid={!!error} aria-describedby={error ? "webhook-error" : undefined} />
          <label className="visually-hidden" htmlFor="webhook-format">Format</label>
          <select id="webhook-format" className="field narrow" value={format} onChange={(e) => setFormat(e.target.value as WebhookFormat | "")} disabled={busy}>
            <option value="">Format: from the URL</option>
            {settings.formats.map((f) => <option key={f} value={f}>{FORMAT_WORDS[f]}</option>)}
          </select>
          <button className="btn primary" type="submit" disabled={busy || !url.trim()}>{busy ? "Checking…" : "Add webhook"}</button>
        </form>
      ) : (
        <p className="faint small">At most {settings.max_webhooks} webhooks; remove one to add another.</p>
      )}
      {error && <p className="err small" id="webhook-error" role="alert">Refused: {error}.</p>}
      {note && <p className="small" role="status">{note}</p>}
      {log && log.length > 0 && (
        <>
          <h4>Deliveries</h4>
          <TableScroll label="Webhook deliveries">
            <table className="t deliveries">
              <thead>
                <tr><th className="first">When</th><th>What</th><th>Result</th></tr>
              </thead>
              <tbody>
                {log.slice(0, 20).map((d) => (
                  <tr key={d.id} className={d.status}>
                    <td className="first muted">{new Date(d.last_attempt_at ?? d.created_at).toLocaleString()}</td>
                    <td>{d.test ? "Test" : "Filing alert"}</td>
                    <td>{deliveryText(d)}{d.next_attempt_at && <span className="faint small"> · next try {new Date(d.next_attempt_at).toLocaleTimeString()}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </>
  );
}
