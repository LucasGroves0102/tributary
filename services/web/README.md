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
`localStorage.setItem("tributary.picker", "1")` in the browser and reload.

"Sign in with GitHub" sits next to "Try the demo" when `GET /v1/auth/providers` says
`{"github": true}`. It is a plain link to `/api/query/v1/auth/github/start` (a full-page
navigation, not a fetch; the Query API does the OAuth flow, see its README). GitHub's callback
lands on `#/signed-in?token=…&expires_in=…` (or `?error=<code>`, shown as a plain sentence:
`session.ts` `signInError`). The app reads the token from the fragment (`readSignedIn`; the
token still comes only from the API), drops the fragment from the address bar and history
(`history.replaceState`), reads `/v1/me` (its `github` block: login and the demo's company
ids), stores a `kind: "github"` `Session` the same way as a sandbox's, shows the same
propagation progress for a new account, and lands on Home. The top bar shows "Signed in as
<login>" with "Sign out", which forgets the token (the JWT is stateless and lasts 7 days; an
expired one returns to the front door with "Your sign-in expired"). A GitHub account is not a
sandbox: no "resets in" banner, but it has the same 20 companies, so the scope below applies.

A sandbox (or a GitHub sign-in) lists only its own 20 companies (`src/scope.ts`, a React context set from the
session; display only, the API's OpenFGA check and RLS stay the boundary). Companies screens
with `access=financials`, without the "Only companies I can read" box, and counts "N companies
in your demo"; the palette (and so the watchlist's "+ Add"), Compare's target/peer pickers and the palette's `compare`/`chart`
commands search with `/v1/companies/search?access=financials`; Compare's suggestions and the
company Peers tab ask `/v1/companies/{id}/peers?access=financials` (no readable peers: a short
note pointing to Compare); a Compare link with other companies leaves them out, with a note;
a company or filing URL outside the demo shows one page, "<Name> isn't one of the 20 companies
in this demo.", instead of locked tabs, and the assistant suggests nothing about it. The Ask
page's deliberate permission tests are labelled "Try to break it:". Already scoped by the API
for everyone: the chart builder's companies (`access=financials`), Home's feed and expected
filings (`/v1/filings/latest`, `scope=readable`/`watchlist`), the Search page's company filter
(`/v1/me`). Named demo users and future signed-in users keep the full public index, with
ungranted companies as metadata only.

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

Reports (`src/reports/`, agent-api `/v1/reports`): a workspace's page (`#/workspace/{id}`, ▤ beside
the folder in the sidebar) lists its items and reports and has "Write a report". The assistant
writes a research note from the saved items and saves it as an item of kind `report`; progress
streams while it works, and the app stays usable (the run lives with the app, `useReportWriter`).
The report page (`#/report/{id}`) renders its Markdown through `markdown.ts` (DOMPurify; accession
numbers link to EDGAR only when a tool returned them) and draws each embedded item from the state
saved with the report, reading the data through the Query API as the viewer: charts with
LayerChart, comps as small multiples (an ungranted peer says "no access"), screens as their
filters with a link. Figures the verification couldn't trace are highlighted, and listed in the
note itself. Download Markdown (`reportMarkdown`: embeds become a line naming the item,
unverified figures are marked `[unverified]`, sources listed) and Print / PDF (`@media print` in
`styles.css`: the page alone, in the light deck palette whatever the theme; `tokens.test.ts`
keeps it equal to the light tokens). "Write again" regenerates it over the same item. A report
counts as 3 assistant questions; the API's refusal (hourly or demo cap) is shown as is.

Customizable views (`src/views/`, Query API `/v1/views`): Home's "Customize" shows, hides and
reorders its cards; the watchlist and the screener have "Columns" to choose and order their value
columns from what `/v1/screener/columns` offers (up to 8 and 12), with Reset. Each change is saved
to the user's account at once (optimistic; a failed save restores the last saved state and says
so). Saved preferences are cleaned like any untrusted input (`views/model.ts`: unknown cards and
columns dropped, missing ones default) and grant nothing: values are still read through the
permission-checked routes, masked for ungranted companies. Without the app database the defaults
show and the controls are hidden.

Chart builder (`src/chart/`): a "Chart a company" box next to the title lists the companies the
chart can read (by name or ticker) and adds the picked one's revenue (TTM) as a new line layer.
Each layer card starts with a labelled Company row (full width) and its header shows the
ticker. Layers can be renamed (legend), recoloured (colours stay with a
layer when it moves), moved up or down, hidden; types line, bar, area, stacked bar and stacked
area (layers of the same stacked type and axis stack, negatives downward); transforms quarterly,
TTM, YoY, QoQ, indexed and per share (`ttm(f) / weighted_shares_diluted` in the quarter view,
offered only when the catalog has the share count); ranges 1y/3y/5y/10y/max or a custom first and last period end; quarterly or annual frequency
(annual = each year's fourth quarter: flows as TTM there, i.e. the fiscal- or calendar-year total,
balances as the year-end value, ratios full-year; QoQ becomes YoY). Formulas autocomplete metric
names (the catalog), your companies' tickers (`TICKER.`), `ttm()` / `abs()`, operators and lags
(`chart/autocomplete.ts`; Tab completes, arrows then Enter pick), and the API still checks them
inline. Deck output draws the chart in the light palette with a subtitle (periods and units,
editable), axis unit captions and a source footnote ("Source: SEC filings via Tributary; forms
for companies; latest filing; as of"), which SVG and PNG exports keep. Evaluate answers are kept
in memory per (token, request) for the session (`chart/cache.ts`), so coming back to a chart
asks the API nothing. Clicking a point
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

Compare's "Side by side" draws the selected columns as small multiples (`compare/SmallMultiples.tsx`):
one compact LayerChart per column, every readable company a line in the same colour on each
(at most 8), calendar quarters over 3 years, each line one `/v1/formulas/evaluate` request built
by the chart builder's rules and shared with its session cache; nothing is asked until the
charts are shown.

States and accessibility: every data panel loads with a skeleton (`components/States.tsx`),
errors offer a retry, empty states say what to do next. The screener, watchlist and comps rows
are a roving tab stop (arrows, Page Up/Down, Home/End, Ctrl/Cmd + Up/Down the first and last
row, Right or Ctrl/Cmd + Left/Right into the cells, then the first and last column; Enter opens:
`components/tableNav.ts`). `?` opens the keyboard shortcut sheet (`components/ShortcutSheet.tsx`,
also from the sidebar footer).
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
reopening it, the assistant drawer with a streamed answer, the narrow-screen menu, formula
autocomplete with annual frequency and deck output, Compare's side-by-side charts, the shortcut
sheet and Ctrl+arrow table jumps, the limits page, data notes on Home and the tearsheet, writing
a report from a workspace with a mocked SSE stream (embedded charts, the flagged figure, Markdown
download, print styles), and customizing Home and the watchlist and screener columns) run
against `vite preview` with every `/api/*` call answered by `e2e/mockApi.ts` (route
interception), so no backend runs. One worker and one headless chromium
(`npx playwright install chromium` once). CI runs them in the `e2e` job.

## Performance

Production build (`npm run build`, Vite 6, 2026-10-21), per route: what a first visit to that
screen downloads, minified (gzip). Every screen but Home is its own chunk (`src/pages/lazy.ts`,
React.lazy); once signed in and idle the app prefetches them, so switching screens doesn't wait.

| Route | JS | CSS |
|---|---|---|
| Shell + Home (`#/`), first load | 342.3 kB (109.0 kB) | 37.2 kB (8.1 kB) |
| + Chart builder (`#/chart`) | + ChartBuilder 23.7 kB (8.9 kB), LayerChart 11.4 kB (4.8 kB), cache 0.2 kB | |
| + Company tearsheet (`#/company/…`) | + Company 12.5 kB (4.0 kB), Filings 6.5 kB (2.3 kB), LayerChart | |
| + Compare (`#/compare`) | + Compare 13.6 kB (4.9 kB), LayerChart, cache | |
| + Companies (`#/companies`) | + 11.1 kB (4.1 kB) | |
| + Filing reader (`#/filing/…`) | + FilingReader 10.8 kB (3.7 kB), text 2.1 kB (1.1 kB) | |
| + Search, About | + 5.2 kB (1.9 kB), 3.6 kB (1.4 kB) | |
| + first assistant answer (marked, DOMPurify) | + AssistantMessage 68.1 kB (23.2 kB) | |
| + Workspace (`#/workspace/…`), Report (`#/report/…`) | + 8.3 kB (3.4 kB) each, Report + markdown, LayerChart, SmallMultiples 2.4 kB | |

Before splitting, everything was one 493.6 kB (156.3 kB gzip) script; the first screen now
loads a third less. Most of what remains is React DOM. Chart data is cached in memory per
(token, request) for the session (`src/chart/cache.ts`). Lighthouse CI isn't run: `@lhci/cli`
plus a Chrome download didn't fit the CI and laptop budget; these sizes are the tracked number.

## Production

`Dockerfile` builds the bundle and serves it from `nginx-unprivileged` on port 8080
(`deploy/nginx.conf`). nginx returns 404 for the APIs' `/docs`, `/redoc`, `/openapi.json` and
`/metrics`, forwards a request ID, and passes Cloudflare's `CF-Connecting-IP` to agent-api as
`X-Client-IP` for its per-IP limit. `deploy/headers.conf` sets CSP, HSTS, nosniff,
Referrer-Policy and Permissions-Policy on every response. On k3s only the `cloudflared`
namespace can reach it.
