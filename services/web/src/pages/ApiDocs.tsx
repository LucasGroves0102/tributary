import { useCallback, useEffect, useState } from "react";

import { keyRoutes } from "../api";
import { apiBase, keyRouteList, LIMITS_TEXT, type KeyRoute } from "../apikeys/apiKeys";
import { Folio } from "../components/Folio";
import { Seal } from "../components/Seal";
import { TableScroll } from "../components/TableScroll";
import { href } from "../route";
import type { KeyRoutesDoc } from "../types";

const CLIENT = "https://github.com/LucasGroves0102/tributary/blob/main/clients/python/tributary_client.py";
const NVDA = "0001045810";

type State = { kind: "loading" } | { kind: "ok"; doc: KeyRoutesDoc } | { kind: "none" } | { kind: "error" };

const SCOPE_WORDS: Record<KeyRoute["scope"], string> = { read: "Read", write: "Write", public: "No key" };

/** "Using the API": how to make a key and use it, with curl and Python, the routes a key can use
 * and the limits. Public (shown signed out too, like Data status). The route list and the limits
 * come from the Query API's own generated OpenAPI of the key routes (GET /v1/openapi-keys.json);
 * nothing about a route is written here by hand. */
export function ApiDocs({ load = keyRoutes, signedOut = false, origin }: {
  load?: () => Promise<KeyRoutesDoc | null>;
  signedOut?: boolean;
  /** the site's origin (tests); the page's own by default */
  origin?: string;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const fetchDoc = useCallback(() => {
    let alive = true;
    setState({ kind: "loading" });
    load()
      .then((doc) => alive && setState(doc ? { kind: "ok", doc } : { kind: "none" }))
      .catch(() => alive && setState({ kind: "error" }));
    return () => {
      alive = false;
    };
  }, [load]);
  useEffect(fetchDoc, [fetchDoc]);
  const base = apiBase(origin);
  const limits = state.kind === "ok" ? state.doc.info["x-tributary-limits"] : undefined;

  return (
    <div className={signedOut ? "page api-docs status-page signed-out" : "page api-docs status-page"}>
      {signedOut && (
        <a className="status-home" href={href({ view: "home" })} aria-label="Tributary, the front door">
          <Seal size="logo" />
          <span className="wordmark">Tributary</span>
        </a>
      )}
      <div className="page-header">
        <h1>Using the API</h1>
        <p className="muted">
          What you read in Tributary, from your own scripts: the same figures and filing text, each with the filing it came from,
          through the same permission checks as this site. A key reads only what you can read here.
        </p>
      </div>

      <Folio name="Your key" line="Made in Settings, shown once" lead>
        <ol className="api-steps">
          <li>
            {signedOut ? <><a href={href({ view: "home" })}>Sign in</a> with GitHub or your account, then open Settings, API keys.</>
              : <>Open <a href={href({ view: "settings" })}>Settings</a>, API keys.</>}{" "}
            Demo sandboxes can't make keys.
          </li>
          <li>
            Name the key, choose <b>Read only</b> or <b>Read and write</b>, and when it expires: in 30, 90 or 365 days.
          </li>
          <li>
            Copy the key (it starts <code>trb_</code>) when it appears: it is shown once. Tributary keeps only a fingerprint of it, so a
            lost key can't be shown again; revoke it and make another.
          </li>
        </ol>
        <p>
          Keep it out of your code and notebooks: put it in an environment variable, <code>TRIBUTARY_API_KEY</code>, which the Python client
          reads. You can have five active keys; Settings lists each with when it was last used and how many requests it made today, and
          revokes one at once.
        </p>
      </Folio>

      <Folio name="First request" line="curl and Python">
        <p>
          Every route is under <code className="api-base">{base}</code>. Send the key as a bearer token. Find a company, then read its
          revenue for the last eight quarters, each with its citation:
        </p>
        <pre className="api-code" tabIndex={0} aria-label="curl example"><code>{[
          `export TRIBUTARY_API_KEY=trb_...   # your key`,
          ``,
          `curl -H "Authorization: Bearer $TRIBUTARY_API_KEY" \\`,
          `  "${base}/v1/companies/search?q=NVDA"`,
          ``,
          `curl -H "Authorization: Bearer $TRIBUTARY_API_KEY" \\`,
          `  "${base}/v1/companies/${NVDA}/financials?metrics=revenue&quarters=8"`,
        ].join("\n")}</code></pre>
        <p>
          With the <a href={CLIENT} target="_blank" rel="noreferrer">Python client</a>, one file with no dependencies (save it beside your
          script). It waits out a rate limit and never sends the key anywhere but this API:
        </p>
        <pre className="api-code" tabIndex={0} aria-label="Python example"><code>{[
          `from tributary_client import Tributary`,
          ``,
          `t = Tributary(base_url="${base}")  # the key from TRIBUTARY_API_KEY`,
          `nvda = t.search("NVDA")[0]["company_id"]`,
          `for row in t.financials(nvda, metrics=["revenue"], quarters=8)["rows"]:`,
          `    print(row["period_end"], row["value"], row["citation"]["accession_no"])`,
          ``,
          `# any route the key may use`,
          `risks = t.get(f"/v1/companies/{nvda}/sections/1A", form="10-K")`,
        ].join("\n")}</code></pre>
        <p>A write key can also change your saved work, for example add a company to your watchlist:</p>
        <pre className="api-code" tabIndex={0} aria-label="Write example"><code>{[
          `curl -X PUT -H "Authorization: Bearer $TRIBUTARY_API_KEY" \\`,
          `  "${base}/v1/watchlist/${NVDA}"`,
        ].join("\n")}</code></pre>
      </Folio>

      <Folio name="What a key can do" line="Read, or read and write">
        <dl className="api-scopes">
          <dt>Read only</dt>
          <dd>Every route below marked Read: figures, segments, filings and their text, What changed, search, the screener, peers, events,
            formulas and models (calculated, nothing saved), and your own watchlist and saved work.</dd>
          <dt>Read and write</dt>
          <dd>Also creates and edits your workspaces and the charts, comps, screens, theses and models in them, and adds to or removes from
            your watchlist. Deleting a workspace or an item still needs you signed in: a script can't undo one.</dd>
          <dt>Never</dt>
          <dd>No key makes or revokes keys, changes your alerts, webhooks or other settings, or runs the assistant or a thesis check: those
            need you signed in, and answer a key with 403.</dd>
        </dl>
      </Folio>

      <Folio name="Routes" line="From the API's own description"
        aside={<a href={`${base}/v1/openapi-keys.json`} target="_blank" rel="noreferrer">OpenAPI</a>}>
        {state.kind === "loading" && <p className="muted">Loading the routes…</p>}
        {state.kind === "error" && (
          <p className="error" role="alert">
            Couldn't load the routes.{" "}
            <button className="btn sm" onClick={fetchDoc}>Try again</button>
          </p>
        )}
        {state.kind === "none" && <p className="empty">This server doesn't take API keys yet.</p>}
        {state.kind === "ok" && <Routes routes={keyRouteList(state.doc)} />}
      </Folio>

      <Folio name="Limits and errors" line="Per key">
        <p>
          Each key makes up to {limits ? LIMITS_TEXT(limits) : "a set number of requests a minute and a day"}: a minute on the clock, a day
          from midnight UTC. Every answer says what's left in <code>X-RateLimit-Remaining-Minute</code> and{" "}
          <code>X-RateLimit-Remaining-Day</code>. Errors are JSON with a <code>detail</code>:
        </p>
        <TableScroll label="Errors">
          <table className="t api-errors">
            <thead>
              <tr><th className="first">Status</th><th>Means</th></tr>
            </thead>
            <tbody>
              <tr><td className="first">401</td><td>The key is unknown, expired or revoked: one answer for all three. Make a new key.</td></tr>
              <tr><td className="first">403</td><td>The route needs you signed in, the key's access doesn't reach it (a read key writing), or you can't read that company.</td></tr>
              <tr><td className="first">404</td><td>No such item of yours.</td></tr>
              <tr><td className="first">422</td><td>A parameter or body the route doesn't take; the detail says which.</td></tr>
              <tr><td className="first">429</td><td>Over a limit. <code>Retry-After</code> says how many seconds to wait.</td></tr>
              <tr><td className="first">503</td><td>A part of Tributary is briefly unavailable, or a query asked for too much at once; try again, or narrow it.</td></tr>
            </tbody>
          </table>
        </TableScroll>
      </Folio>
    </div>
  );
}

/** The API's words with their `code` spans as code (text only: nothing is parsed as HTML). */
function withCode(text: string) {
  return text.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));
}

function Routes({ routes }: { routes: KeyRoute[] }) {
  return (
    <TableScroll label="Routes an API key can use">
      <table className="t api-routes">
        <thead>
          <tr>
            <th className="first">Route</th>
            <th>Access</th>
            <th>What it does</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((r) => (
            <tr key={`${r.method} ${r.path}`}>
              <td className="first">
                <span className="api-method">{r.method}</span> <code>{r.path}</code>
              </td>
              <td>{SCOPE_WORDS[r.scope]}</td>
              <td>
                <span className="api-summary">{r.summary}</span>
                {r.description && <span className="api-about">{withCode(r.description)}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
