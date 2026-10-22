# web

Chat and explore UI (React 19, TypeScript, Vite), served by nginx. It talks to the
[Query API](../query-api) and [agent-api](../agent) through same-origin paths, so it needs no
CORS:

| Path | Proxied to |
|---|---|
| `/api/query/*` | Query API (`query-api:8000`) |
| `/api/agent/*` | agent-api (`agent-api:8001`), unbuffered for server-sent events |

The shell follows prototype v3: a sidebar (screens with `g`-then-letter shortcuts, watchlist
with revenue sparklines, next expected filings, freshness), a top bar (breadcrumb, Ctrl/Cmd-K
command palette, theme, demo user) and the assistant drawer on every page (Ctrl/Cmd-J toggles,
`/` focuses). Dark is the default theme; the choice is kept per user in localStorage. Design
tokens (colours for both themes, type scale, spacing) are in `src/tokens.css`.

Views (hash routes): `#/` Home (status strip, watchlist table, new filings since the last
visit, expected filings); `#/companies?<filters>` the screener (`/v1/screener`: filters kept in
the URL, sortable, ungranted companies as metadata only, CSV export through the API);
`#/company/<company_id>` 12-quarter chart and table, every value linked to its filing;
`#/filing/<company_id>/<accession>[/<section>]` parsed filing reader; `#/chart` the chart
builder (each layer a `/v1/formulas/evaluate` request; plain SVG; CSV, SVG and PNG export);
`#/ask` the assistant full width, with streamed answers, tool trace and verified citation links;
`#/compare` describes what's coming.

Sign-in (`src/session.ts`): the front door is "Try the demo" (`pages/Welcome.tsx`). It calls
`POST /v1/demo/sandbox`, which creates a temporary real user (plan pro, 20 companies, a seeded
watchlist; see the Query API README) and returns its JWT; the page then polls `/v1/me` until all
20 companies are readable, showing how many have arrived (access travels app DB → Debezium →
Kafka → authz-sync → OpenFGA; the BRD target is under 30 s; after that it offers "Continue
anyway"), and lands on Home. The session is kept in localStorage until the sandbox expires;
a banner shows "Demo sandbox: 20 companies · resets in Xh", and an expired or rejected token
returns to the front door. The named demo users (alice, bob, carol, dave: `/v1/demo/users`,
`/v1/demo/token`) are behind a dev flag: build with `VITE_DEMO_PICKER=1`, or run
`localStorage.setItem("tributary.picker", "1")` in the browser and reload. Real sign-in (OAuth)
will be another way to produce the same `Session`.

The assistant sends the screen as `context` with each question (`assistant/actions.ts`) and
applies the agent's `action` events (chart layers and options, screener filters, watchlist) with
an Undo per action (`assistant/useUiActions.ts`): undo restores the chart and page from before
it, reverses watchlist calls, and also undoes every later action.

## Run

```bash
make web        # npm install + Vite dev server on http://localhost:5173 (needs query-api, agent-api)
make up-all     # nginx build on http://localhost:3000
```

The dev server proxies to `QUERY_API_URL` (default `http://localhost:8000`) and `AGENT_API_URL`
(default `http://localhost:8001`).

```bash
cd services/web
npm run typecheck
npm test          # vitest
npm run build     # tsc + vite build into dist/
```

## Production

`Dockerfile` builds the bundle and serves it from `nginx-unprivileged` on port 8080
(`deploy/nginx.conf`). nginx returns 404 for the APIs' `/docs`, `/redoc`, `/openapi.json` and
`/metrics`, forwards a request ID, and passes Cloudflare's `CF-Connecting-IP` to agent-api as
`X-Client-IP` for its per-IP limit. `deploy/headers.conf` sets CSP, HSTS, nosniff,
Referrer-Policy and Permissions-Policy on every response. On k3s only the `cloudflared`
namespace can reach it.
