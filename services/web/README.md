# web

The research workspace UI (React 19, TypeScript, Vite), served by nginx. It talks to the
[Query API](../query-api) and [agent-api](../agent) through same-origin paths, so it needs no
CORS:

| Path | Proxied to |
|---|---|
| `/api/query/*` | Query API (`query-api:8000`) |
| `/api/agent/*` | agent-api (`agent-api:8001`), unbuffered for server-sent events |
| `/mcp` (exactly) | the MCP server (`mcp-server:8002`) for users' own AI models; the app never calls it |
| `/.well-known/oauth-protected-resource[/mcp]` | the MCP server: its resource metadata, where an AI app learns to sign in |
| `/.well-known/oauth-authorization-server`, `/oauth/*` | Query API: the authorization server for connected apps (also in the dev proxy); any other `/.well-known/` path is a 404 |

Every figure, reading and access decision comes from the APIs: the app words them and decides none.
Each screen's detail, the rules behind it and the measurements are in [DESIGN.md](DESIGN.md).

## Look

Noor's build spec, [`.interface-design/system.md`](../../.interface-design/system.md): paper, ink and pencil;
Archivo for the interface and every figure, Source Serif 4 only for the filing's own words; one seal red for
the tick (a figure that agrees to its filing) and the logo; green and rust for a change's direction only. The
first batch rebuilt the foundations, the shell and the company page's header, Overview and Statements;
batches 2 and 3 every other page (sections instead of cards, every table a ledger, the screener's filter
chips, the reader's What changed mode as a blackline, Home's lead and side columns, the front door).

- Tokens (both palettes, type, spacing, radius, the 32px control, motion) are in `src/tokens.css`; the spec's
  shared components in `src/ui.css`, the shell in `src/shell.css`, the company page in `src/company.css`, what
  every other page shares and each page's own in `src/pages.css` (loaded last); the older rules those build on
  in `src/styles.css` and `src/seal.css`.
- Every figure opens its source card (`components/SourceCard`): the figure as Tributary holds it, its mark
  (✓ agrees to the filing, Σ added up, *d* derived; from the API's sources only), its filing, the reader and
  EDGAR. Ledgers are one tab stop each, arrows inside (`components/ledgerGrid`).
- Motion (`src/motion.ts`): page changes are view transitions (the shell still, content fading and sliding by
  depth, a company's name travelling from its row into the title); anything from the keyboard is instant;
  reduced motion fades only.
- Fonts: Source Serif 4 and Archivo (with its width axis), self-hosted from pinned `@fontsource-variable`
  packages (the CSP allows fonts only from this origin).
- Controls with behaviour use Radix Primitives (pinned exact), styled only with the tokens.
- The theme follows the system; Settings picks Light, Dark or System (`theme.ts`).

More: [DESIGN.md, Look](DESIGN.md#look), [Shell and layout](DESIGN.md#shell-and-layout) and
[Motion](DESIGN.md#motion).

## Shell

A 48px top bar over a 232px rail, both still on every page change. The top bar: the logo, search (Ctrl/Cmd-K
opens the command palette), the feed's freshness (a status point that opens what it means and Data status),
the filing alerts with their count, the Assistant (Ctrl/Cmd-J; `/` focuses it) and the account chip (a
sandbox's reset and End demo, an account's Sign out). The rail: the screens (with `g`-then-letter shortcuts,
g p Portfolio and g n Analysis), Watching (code, name, "new"), Workspaces, and at its foot Data status, API,
Settings, Shortcuts and What Tributary covers. The palette finds companies, the nav's screens, Alerts, Settings,
Using the API, Data status, saved screens and, where the API allows them, API keys and Connected apps
(`PalettePlace`, built in App). Every screen sets the tab's title (`src/title.ts`). A page that fails to render
shows "This page didn't load" and keeps the shell (`components/ErrorBoundary.tsx`). Pages lay out by the room
they have (`@container main`): the 400px assistant panel pushes the page at 1200px and up, overlays it on a
tablet and fills a phone; under 1200px the rail is a drawer behind Menu. `?` opens the keyboard shortcuts.

## Screens

Hash routes (`src/route.ts`; an address naming no page shows "No page here", a malformed one under a known page goes Home, `#/explore` to Companies):

| Route | Screen | Detail |
|---|---|---|
| `#/` | Home: Start an analysis, the watchlist, new filings, data notes; Customize for more cards | [Home](DESIGN.md#home) |
| `#/companies?<filters>` | the screener (`/v1/screener`): filters in the URL, sectors, columns, CSV through the API | [Screens](DESIGN.md#screens-hash-routes) |
| `#/company/<id>[/<tab>][?asof=<day>]` | a company: Overview, Earnings, Statements, Revenue, Segments, Ratios, Filings, Data notes, Peers, Price (only with Connect your data); `asof`: its figures as they stood at the end of a past day | [Company page](DESIGN.md#company-page), [As of](DESIGN.md#as-of-a-past-day) |
| `#/filing/<id>/<accession>[/<section>][?view=changes]` | the filing reader: whole sections as paragraphs and tables, search, side by side; `view=changes` its What changed mode (a 10-K or 10-Q's changes as a blackline) | [Filing reader](DESIGN.md#filing-reader) |
| `#/search` | full-text search across filings | [Search](DESIGN.md#search) |
| `#/chart` | the chart builder (formula layers, CSV, SVG and PNG export) | [Chart builder](DESIGN.md#chart-builder) |
| `#/compare` | comps table, peer medians, side-by-side charts | [Compare](DESIGN.md#compare) |
| `#/ask` | the assistant full width, with verified citations | [Assistant](DESIGN.md#assistant) |
| `#/analysis[/<id>[/<step>[/<item>]]]`, `#/analysis?start=<id>` | guided research projects in 8 steps | [Analysis](DESIGN.md#analysis) |
| `#/workspace/<id>`, `#/report/<id>` | saved work and research notes the assistant writes | [Saved work and reports](DESIGN.md#saved-work-and-reports) |
| `#/thesis/<id>`, `#/model/<id>` | a thesis (Write view, Check my thesis, Revisit) and a model | [Theses and models](DESIGN.md#theses-and-models) |
| `#/alerts` | filing alerts and your watched screens' alerts | [Filing alerts](DESIGN.md#filing-alerts), [Watched screens](DESIGN.md#watched-screens) |
| `#/screen/<id>` | a saved screen's link (alerts point at it): opens it as Companies' open screen, in place of the link | [Watched screens](DESIGN.md#watched-screens) |
| `#/portfolio[/<holding>]`, `#/portfolio/import` | your holdings on your own prices, a holding's buys, sales and journal, the Fidelity import | [Portfolio](DESIGN.md#portfolio) |
| `#/data` | Your data: your datasets (CSV upload, checked before storing), your metrics (saved formulas, screener columns) | [Your data](DESIGN.md#your-data) |
| `#/settings` | filing alerts, Connect your data (when on), Connected apps, API keys | [Price](DESIGN.md#price-connect-your-data), [Connected apps](DESIGN.md#connected-apps), [API keys](DESIGN.md#api-keys-and-using-the-api) |
| `#/connect?request=…` | the consent screen when an AI app (Claude, ChatGPT) asks to read Tributary as you: the app, who published it, where it sends you back, Approve or Deny; without the shell | [Connected apps](DESIGN.md#connected-apps) |
| `#/about` | what Tributary covers and doesn't | [Screens](DESIGN.md#screens-hash-routes) |
| `#/status` | Data status: freshness, completeness, quality checks by what they mean for a reader, filing-section checks, recorded suites; public | [Screens](DESIGN.md#screens-hash-routes) |
| `#/api` | Using the API: keys, examples, in your spreadsheet (Excel, Google Sheets), the routes a key can use, connecting an AI model; public | [API keys](DESIGN.md#api-keys-and-using-the-api), [Spreadsheet add-ins](#spreadsheet-add-ins) |
| `#/signed-in?token=…` | where GitHub sign-in and an owner-issued link land | [Sign-in](DESIGN.md#sign-in-and-access) |

`#/status` and `#/api` render signed out too, without the shell. `#/connect` signed out shows the
front door, keeps the request in the tab while you sign in, and opens again after.

## Rules every screen follows

- **Names, never ids.** Anything holding a company id reads its name through
  `companies/directory.ts`; SEC names are made readable for display only (`displayNames.ts`), and CSV
  exports keep the SEC's ([Company names](DESIGN.md#company-names)).
- **One blanks vocabulary** (`src/blanks.ts`): — not reported, · no data in this period, n/a, n/m, $0 a
  real zero. Whether a figure is n/m, n/a, old or stale is the API's reading (migrations 035, 069)
  ([Blanks and readings](DESIGN.md#blanks-readings-currencies-and-per-share-figures)).
- **Money** has four significant figures at most, thousands grouped, negatives in parentheses on every
  table and the lists under them (display only: CSV and hovers keep the sign), one unit per row
  (`format.ts` `moneyRow`: the next smaller unit when a figure would read under one, so $119M ... $3,955M,
  not $0.12B), and another currency shown in it, never converted. A column's decimal points line up
  (`components/TableScroll` `decimalPads`: unseen digits, a point or a hanging parenthesis after a figure).
  A negative is written with the true minus (U+2212), and a change's fall is rust (`.neg` = `.loss`), a
  level uncoloured; a figure never parts from its currency code or scale word at a line's end (no-break
  spaces), and a " · " never starts a wrapped line.
- **One sign convention**: outflows (capex, dividends, buybacks) are negative on statements, their CSV
  and the Numbers charts; the chart builder and screener keep the API's metric, the amount paid.
- **Times** are the viewer's local date and clock with the zone (`format.ts` `localTime`).
- **Freshness words**: *listed* (in the filing index), *parsed* (text in the reader), *checks last
  ran*, *last checked* (one company) ([Freshness and times](DESIGN.md#freshness-and-times)).
- **A tick** (✓, seal red) marks only a figure the filing states as shown; Σ a sum of marked figures; *d* a
  derived one (italic); a ratio or difference has no mark. The marks come from the API's sources
  (`data.ts` `statMark`, a row's `value_source`, `Tables.tsx` `cellMark`). The Overview's figures and every
  Statements cell open a source card; a statement's column names its filing. A computed figure elsewhere
  (the screener, Compare, Portfolio) has no mark and says how it is worked out, on hover or a tap.
- **Confidence** (migration 074) is the API's level and reasons, worded by `companies/rows.ts`
  `confidenceWords`: quiet text with a dotted underline whose reasons open on a tap as well as a hover
  (`components/Why`), in the company header always and in the screener only when medium or low; never a
  colour or a traffic light.
- **Buttons**: primary (the ink filled, one per view), secondary (outline), quiet (text, underlined on
  hover), destructive (the error ink; after a rule in a menu). An open saved item's bar shows Save only
  with changes, Save as and Close in More.
- **Feedback**: what just happened is a toast (`components/Toasts`): a save, a new workspace made by a
  save, an export, an alert setting, a red line, End demo. Empty states are one line and one action;
  how a screen works folds behind `details.how-to-read`.
- **A phone**: tables of the user's own records read as labelled cards under 560px (`.t.cards-narrow`,
  labels from `data-label`); the company header keeps the minor facts (`.co-minor`) for wider pages and one
  action row (the primary and `···`).
- **Model output** renders only through `src/markdown.ts` (marked + DOMPurify); an accession number
  becomes an EDGAR link only if a tool returned it. A user's thesis renders the same way, without the
  citation check.
- **Access is the API's.** What a page shows as readable comes from `/v1/me` (`src/access.ts`);
  OpenFGA and RLS stay the boundary. Saved items grant nothing.
- **An older Query API** (the minute of a rolling deploy) degrades, never breaks ([Production](#production)).

## Sign-in

- **Try the demo**: `POST /v1/demo/sandbox` creates a temporary real user and returns its token; the
  session lasts until the sandbox expires, with a banner and End demo.
- **Sign in with GitHub** (when `GET /v1/auth/providers` says so): a plain link to
  `/api/query/v1/auth/github/start`; GitHub's callback lands on `#/signed-in?token=…`.
- **Password accounts** (owner-created; no signup): "Sign in" on the front door posts once to
  `/api/query/v1/auth/password`. The password is never stored.
- **An owner-issued link**: `query-api-token <user_id> --hours N` inside the query-api pod, sent as
  `https://<site>/#/signed-in?token=<token>&expires_in=<s>`.
- **Named demo users** (alice, bob, carol, dave) are test personas, off in production. Locally: build
  with `VITE_DEMO_PICKER=1`, or `localStorage.setItem("tributary.picker", "1")` and reload.

The token always comes from the API. Detail, open access and the demo's scope while the catalog is
closed: [Sign-in and access](DESIGN.md#sign-in-and-access).

## Run

```bash
make web        # npm install + Vite dev server on http://localhost:5173 (needs query-api, agent-api)
make up-all     # nginx build on http://localhost:3000
```

| Variable | Where | Default |
|---|---|---|
| `QUERY_API_URL` | dev server proxy for `/api/query` | `http://localhost:8000` |
| `AGENT_API_URL` | dev server proxy for `/api/agent` | `http://localhost:8001` |
| `VITE_DEMO_PICKER` | build time: `1` shows the named demo users on the front door | off |

```bash
cd services/web
npm run typecheck
npm test          # vitest (src/**/*.test.{ts,tsx}, jsdom)
npm run build     # tsc + vite build into dist/
npm run e2e       # Playwright flows in e2e/ against dist/ (build first)
```

The root `make test` and ruff don't cover this folder; `make typecheck` runs its typecheck. Pin exact
versions in `package.json` and update `package-lock.json` with them.

The 24 end-to-end flows (`e2e/app.spec.ts`; the list is in [DESIGN.md](DESIGN.md#end-to-end-flows))
run against `vite preview` with every `/api/*` call answered by `e2e/mockApi.ts`, so no backend runs.
One worker and one headless chromium (`npx playwright install chromium` once). CI runs them in the
`e2e` job. One flow fails on any console error or 4xx/5xx on the main screens (#68), so a route the
app calls on load needs a mock in `e2e/mockApi.ts`.

### Writing tests

- `src/test-setup.ts` (vite.config `setupFiles`) empties the toast store after each test; toasts are
  found on `document` (`.undo-stack .undo-note`).
- Never `onClick={propThatTestsSpyOn}`: a failing `toHaveBeenCalledWith` on the React event it receives
  prints the fiber tree and kills the vitest worker out of memory, with no assertion message.
- Menus (the company page's More, every `components/MenuButton`): fire a keydown ArrowDown on the
  button (pointerdown is order-sensitive), wait a few ticks before and after (a menu closed a moment
  ago returns focus on a timer and closes the new one), then click the `[role=menuitem]`
  (`[role=menuitemcheckbox]` for checkbox items). See Company.test `moreItem`, App.test, Thesis.test.
- Toggle groups (`components/Choices`) are role radio: read `[aria-checked="true"]`, not aria-pressed.
- A test that picks a company mocks `/v1/companies/search` and waits out the 150 ms debounce.
- The assistant panel is closed unless `localStorage` `tributary.assistant` is `"open"`; a test that
  reads a whole thesis's text sets `tributary.thesis.mode=markdown`.
- Settings shows filing alerts first: a test that queries Settings' first input or `[role=alert]`
  scopes to its section. App and Workspaces tests that need Home's status strip, recent or expected
  cards mock `/v1/views` with them on.
- `newestPeriodic` caches per token: call `clearNewest()` between tests.
- A delete with undo is sent on `window.dispatchEvent(new Event("pagehide"))` or when a pass-through
  `vi.spyOn(globalThis, "setTimeout")` catches the UNDO_MS timer (fake timers hang `settle()`).
- jsdom has no ResizeObserver, so LayerChart draws its standard size in tests. `lazy.test.ts` lists
  the screens in order (Settings and ApiDocs after Status).
- A figure's source card (`components/SourceCard`) is portalled to the body: click the figure (or its
  ledger cell) and read `document.querySelector(".source-card")`; click again to close it. The company
  header's `···` menu is `button[aria-label="More actions"]`; Statements' range, basis and per-share basis
  are its view menu's `menuitemcheckbox`es (`.view-menu`).
- A cell's text is its figure: tests compare `td.textContent` (a negative statement cell reads
  "($0.24B)"), read a figure's how from its card, find ledger rows by `data-key` and header
  facts by `.co-listing`. A seal's hidden `.seal-source` counts in a parent's text, so `.story` tests
  use `toContain`. A component rendered alone has no `SealSprite` (harmless).
- Fixtures that are real API answers are regenerated, never hand-edited: `models/fixtures`
  (`services/query-api/tests/fixtures/model/make_web_fixtures.py`, after any change to the model's
  maths), `company/fixtures` (production's answers and the branch routes over production's data),
  `reader/fixtures/nvda-10k-2026-item15.txt` (production's stored text).
- Locally, Playwright may want a headless shell build the machine lacks: run with a throwaway config
  setting `launchOptions.executablePath`. To check the CSP locally, a throwaway vite config can merge
  `preview.headers` with the CSP from `deploy/headers.conf`. Never commit either.

## Performance

Every screen but Home is its own chunk (`src/pages/lazy.ts`, `React.lazy`); once signed in and idle
the app prefetches them. At the last measurement (2026-11-09) the shell and Home were 471.9 kB
(154.4 kB gzip) of JS on a first visit. Evaluate answers are cached in memory per (token, request) for
the session (`src/chart/cache.ts`). Per-route sizes: [DESIGN.md, Performance](DESIGN.md#performance).

## Spreadsheet add-ins

Static files in `public/addins/` (copied to the image as they are; no build step), both calling the
Query API's `POST /v1/sheets/values` with the user's own API key, the key only in `Authorization`:

- **Excel** (`addins/excel/`): `manifest.xml` (XML add-in manifest, shared runtime, id fixed, every
  URL `https://tributary.lucasgroves.dev/addins/excel/...`), `functions.json` + `functions.js`
  (`=TRIBUTARY.VALUE`, `.SERIES`, `.SOURCE`: cells batched for 50 ms into requests of 200 asks and 25
  companies, one at a time, answers kept 10 minutes, the API's reasons as `#N/A`, bad arguments as
  `#VALUE!`), `taskpane.html/.js/.css` (save or remove the key in `OfficeRuntime.storage`, Refresh
  figures, Check the key) and the seal at 16/32/64/80 px. Sideload the manifest (Using the API says
  how); AppSource listing is the owner's step later. Schema validation needs Microsoft's online
  validator (`npx office-addin-manifest validate manifest.xml`): run it before listing; the tests
  check the XML offline (well-formed, resources defined, files present, functions match).
- **Google Sheets** (`addins/sheets/Tributary.gs`, served as text): pasted into Extensions, Apps
  Script. `=TRIBUTARY`, `=TRIBUTARY_SERIES`, `=TRIBUTARY_SOURCE`; a range is one request; the key in
  user properties (the menu), answers in the script cache six hours; Refresh sets every TRIBUTARY
  formula again. Google runs formulas with the owner's properties: editors of a sheet can use its
  owner's key (said on the page).
- `location /addins/` sends `deploy/addin-headers.conf`: headers.conf's headers but a CSP that lets
  Office's web hosts frame the pages (`frame-ancestors`) and loads Office.js from
  `appsforoffice.microsoft.com`; no inline script or style, `connect-src 'self'`. Nothing under
  `/addins/` falls back to `index.html`.
- Cloudflare: Apps Script's `UrlFetchApp` sends `Mozilla/5.0 (compatible; Google-Apps-Script; ...)`
  from Google's addresses. That user agent passes the zone's checks (2026-11-17, from outside
  Google; Python's default `Python-urllib` gets 1010). Google's addresses weren't testable from
  here: if Sheets users see "a proxy or firewall page", add the skip rule in
  [deploy/terraform](../../deploy/terraform/README.md#spreadsheets-and-the-cloudflare-zone).
- Tests: `src/addins/addins.test.ts` runs both files as served (`node:vm`, fakes for Office and
  Apps Script services), checks the manifest and that addin-headers.conf differs from headers.conf
  only in its CSP.

## Production

`Dockerfile` builds the bundle and serves it from `nginx-unprivileged` on port 8080
(`deploy/nginx.conf`). nginx returns 404 for the APIs' `/docs`, `/redoc`, `/openapi.json` and
`/metrics`, and for the Query API routes only agent-api may call (the demo's question quota, writing
a report, storing a thesis check: browsers read those through the item routes); it forwards a request
ID, and passes Cloudflare's `CF-Connecting-IP` to both APIs as `X-Client-IP` (agent-api's per-IP
limit, the Query API's sandbox creation and password attempt limits). `location = /mcp` proxies to
the MCP server with the Authorization header untouched, no buffering, a 300 s read timeout,
`Cache-Control: no-store` and the security headers; nothing else of that service is routed. A
dataset upload (`POST /api/query/v1/datasets` and `.../{id}/upload`, exact paths) is the one Query
API body allowed past nginx's 1 MB default: 21 MB and a 120 s read. `deploy/headers.conf` sets CSP, HSTS, nosniff,
Referrer-Policy and Permissions-Policy on every response. On k3s only the `cloudflared`
namespace can reach it. The page itself goes out with `Cache-Control: no-transform`, so Cloudflare
serves it as built: its Web Analytics "automatic setup" injected a beacon script from
`static.cloudflareinsights.com`, which the CSP blocked on every load (a console error, and nothing
measured). The CSP stays as it is; if the beacon still shows up, turn the zone's Web Analytics
automatic setup off in the Cloudflare dashboard.

A location with its own `add_header` drops the server-level headers, so each includes
`/etc/nginx/headers.conf` (`/addins/` includes `addin-headers.conf` instead: see
[Spreadsheet add-ins](#spreadsheet-add-ins)). nginx resolves `mcp-server` when it starts, so that Service must exist
wherever this image runs (Helm creates it even at `replicas: 0`; compose has the service).

One Helm upgrade rolls every service at once, so for a minute this page can talk to a Query API one
release older. What it asks of the newer API degrades rather than breaks (`api.ts`, tested in
`api.test.ts`): a new route answering 404 reads "not available here yet", a request refused for a new
parameter is asked again without it, and a save refused for a field the older API doesn't know is
saved again without it, unless the field is the user's own work. The full list:
[DESIGN.md, Deploy window](DESIGN.md#deploy-window).
