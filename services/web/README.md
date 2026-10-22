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
`#/compare` the comps table; `#/about` says what Tributary covers and doesn't (no prices, consensus or
transcripts; estimated dates; banks; EDGAR timing), linked from the sidebar footer and the
assistant's empty state.

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
it, reverses watchlist calls, deletes an item the assistant saved (`save_to_workspace`), and
also undoes every later action.

Saved work (`src/workspaces/`, Query API `/v1/workspaces`): the sidebar's Workspaces are folders
of saved charts, comps sets and screens (rename, duplicate, delete beside each row); clicking an
item opens its page with the saved state (`model.ts` restores it through the same cleaners as a
URL, so a saved item is trusted no more than a link). The Chart builder, Compare and Companies
have Save / Save as… with the open item's workspace and an "unsaved changes" mark; the open item
per page is remembered per user in localStorage. Home's "Continue where you left off" lists
recently opened items and filings read in the reader, newest first. Saved items grant nothing:
the pages read data through the Query API as usual, so a chart for a company you lost shows the
same "isn't one of your companies" state as anywhere else.

Chart builder (`src/chart/`): layers can be renamed (legend), recoloured (colours stay with a
layer when it moves), moved up or down, hidden; types line, bar, area, stacked bar and stacked
area (layers of the same stacked type and axis stack, negatives downward); transforms quarterly,
TTM, YoY, QoQ, indexed and per share (`ttm(f) / weighted_shares_diluted` in the quarter view,
offered only when the catalog has the share count); ranges 1y/3y/5y/10y/max. Clicking a point
(or Enter on it; arrows move between quarters and layers) opens its newest source filing in the
reader. Filing markers put each company's 10-Ks and 10-Qs (public index) and 8-K results (item
2.02, from parsed filings) on the time axis. "View as table" shows the plotted values with their
sources as a data table.

Data notes (`src/company/notes.ts`, Query API `/v1/companies/{id}/data-notes`, `/v1/data-notes`):
the tearsheet's Data notes tab groups what the automated checks found (freshness, restated
figures, gaps and overlapping periods, values to double-check, derived quarters) in plain
language, each with links to its filings (the reader for 10-K/10-Q text the user can read,
else EDGAR); Home lists the newest notes across the user's companies. They need financials
access, like the values they describe.

States and accessibility: every data panel loads with a skeleton (`components/States.tsx`),
errors offer a retry, empty states say what to do next. The screener, watchlist and comps rows
are a roving tab stop (arrows, Page Up/Down, Home/End, Enter opens: `components/tableNav.ts`).
`src/tokens.test.ts` checks every text, state and series token against WCAG AA in both themes.
Under 900px the sidebar becomes a drawer behind Menu; under 600px the assistant drawer is full
screen. Banks show "n/a (bank)" for operating margin, FCF and net debt (`format.ts`
`notForBanks`), and Compare leaves them out of those columns' peer median.

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
npm run e2e       # Playwright flows in e2e/ against dist/ (build first)
```

The end-to-end flows (`e2e/app.spec.ts`: try the demo → Home, screener filter/sort/watch, a
two-layer chart with a formula exported as CSV, comps, saving a chart to a workspace and
reopening it, the assistant drawer with a streamed answer, the narrow-screen menu) run against
`vite preview` with every `/api/*` call answered by `e2e/mockApi.ts` (route interception), so
no backend runs. One worker and one headless chromium (`npx playwright install chromium` once).
CI runs them in the `e2e` job.

## Production

`Dockerfile` builds the bundle and serves it from `nginx-unprivileged` on port 8080
(`deploy/nginx.conf`). nginx returns 404 for the APIs' `/docs`, `/redoc`, `/openapi.json` and
`/metrics`, forwards a request ID, and passes Cloudflare's `CF-Connecting-IP` to agent-api as
`X-Client-IP` for its per-IP limit. `deploy/headers.conf` sets CSP, HSTS, nosniff,
Referrer-Policy and Permissions-Policy on every response. On k3s only the `cloudflared`
namespace can reach it.
