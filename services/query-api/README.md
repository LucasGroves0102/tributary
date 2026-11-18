# query-api

FR8, week 2. HTTP API over the gold tables, read-only except for the caller's own rows in the app
database (watchlist, saved work, preferences, alert settings, API keys, connected apps, a market-data
connection);
the [agent](../agent), the [web](../web) app and the [MCP server](../mcp-server) reach data only
through it. Design notes, measurements and each feature's full rules: [DESIGN.md](DESIGN.md).

Every data route is permission-checked twice:

1. **OpenFGA** (`authorizer.py`): `can_read_financials` / `can_read_filings` on the company,
   before any query. Denied → 403 (same response whether the company exists or not).
   OpenFGA unreachable → 503: fail closed.
2. **Postgres row-level security** (`store.py`): queries run as the `query_api` role with
   `app.user_id` set for the transaction; policies return only rows of companies in
   `authz.readable_companies(tier)` (private grants, plus the public catalog within the user's
   tiers; db/migrations/030). No user id, no rows. This holds even if a route forgot layer 1.

**Open access.** While the app database's `public_catalogs` row `sec` is public (the switch:
[db/app](../../db/app/README.md), "Open access"), every signed-in user reads every company in
the public catalog (every company with gold financials or a stored document) within their
plan's tiers, through the same two layers: OpenFGA resolves `reader from catalog` in the same
Check, and RLS reads `authz.public_access`. Per-company grants keep working for anything outside
the catalog. Nothing in the API changes shape except `/v1/me`'s `catalog`.

The caller is identified by an HS256 JWT (`sub` = app user_id), from a script by one of their
personal API keys (`Authorization: Bearer trb_...`, [API keys](#api-keys)), or through the MCP
server by a connected app's access token (`Bearer tro_...`, [Connected apps](#connected-apps-oauth)):
the user is the key's owner or the app's grant's, read from that row. The user is never read from
request parameters. Dev tokens: `make token USER=alice`.

## Routes

`{id}` is the canonical 10-digit `company_id`. Each route's full description is in
[DESIGN.md, Route details](DESIGN.md#route-details).

| Route | Needs | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok"}` |
| `GET /v1/status` | | `last_accepted_at` and `last_ingested_at` for outside uptime checks (`ops.feed_status()`, migration 026); no filing or company |
| `GET /v1/status/data` | | the public status page: aggregate counts and timestamps only, cached 60 s ([Status page](#status-page)) |
| `GET /v1/demo/users` | `DEMO_USERS` set | demo accounts (404 when disabled) |
| `POST /v1/demo/token` | `DEMO_USERS` set | an 8-hour token for a demo account (never a sandbox, GitHub or password user) |
| `GET /v1/demo/sandbox` | `DEMO_SANDBOX_ENABLED` | what "Try the demo" creates: lifetime, plan, tickers, `catalog_public`, assistant allowance ([Demo sandboxes](#demo-sandboxes)) |
| `POST /v1/demo/sandbox` | `DEMO_SANDBOX_ENABLED` | 201: a new temporary user and its token; 429 at the per-IP or live-sandbox cap. Takes no input |
| `GET /v1/demo/agent-quota` | token | agent-api only (nginx hides it): what POST would still allow today, taking nothing |
| `POST /v1/demo/agent-quota` | token | agent-api only: takes 1-5 assistant questions (a report counts as 3) from the caller's daily cap; 429 with the message to show; `{"demo": false}` for users not capped |
| `POST /v1/demo/agent-quota/refund` | token | agent-api only: gives back 1-5 questions for a run that failed, never more than the caller used today |
| `GET /v1/auth/providers` | | `{"github": bool, "password": bool}`: which sign-in buttons to show |
| `POST /v1/auth/password` | `APP_DATABASE_URL` | `{"username", "password"}` → a 7-day JWT; one 401 for every refusal, 429 per address ([Password accounts](#password-accounts)) |
| `GET /v1/auth/github/start` | `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | 302 to GitHub (no scopes) with a signed `state` and a nonce cookie; 404 when not configured ([GitHub sign-in](#github-sign-in)) |
| `GET /v1/auth/github/callback?code=&state=` | the same | 302 to `/#/signed-in?token=…&expires_in=…` (a 7-day JWT in the fragment) or `/#/signed-in?error=<code>` |
| `GET /v1/api-keys` | token (sign-in only, never a key) | the caller's keys (name, first 8 characters, scopes, dates, today's use; never a key or its hash) and the key settings ([API keys](#api-keys)) |
| `POST /v1/api-keys` | sign-in, not a demo sandbox, app DB | `{"name", "scopes", "expires_in_days"}` → 201 with the key, shown once; 409 with 5 active keys |
| `DELETE /v1/api-keys/{key_id}` | sign-in, app DB | revokes the key at once; another user's is the same 404 |
| `GET /v1/openapi-keys.json` | | the OpenAPI of the routes an API key can use (the web app's "Using the API" page) |
| `GET /.well-known/oauth-authorization-server` | `OAUTH_ISSUER` | RFC 8414 metadata of the authorization server for the MCP server ([Connected apps](#connected-apps-oauth)); 404 when off |
| `POST /oauth/register` | the same | RFC 7591 dynamic client registration (JSON); 201 with a `client_id` (and a secret, shown once, for a confidential client); 400 for an unsafe redirect URI; 429 per address |
| `GET /oauth/authorize` | the same | checks the client (registered, or its metadata document fetched) and the exact redirect URI, then PKCE S256, `resource`, `state`; 302 to the consent page (`/#/connect?request=…`, nonce cookie) or back to the app with `error`, `state`, `iss` |
| `POST /oauth/token` | the same | form: `authorization_code` (code, redirect_uri, code_verifier, client) or `refresh_token` (rotated) → `tro_` access token (1 h) and `trr_` refresh token (30 d); RFC 6749 errors |
| `POST /oauth/revoke` | the same | RFC 7009: the token's grant is revoked; 200 also for an unknown token |
| `POST /v1/oauth/consent` | sign-in only, nonce cookie | `{"request"}` → the app, publisher, redirect host, loopback flag, scopes, `allowed` (false for a demo sandbox) |
| `POST /v1/oauth/consent/approve` / `deny` | the same | a grant and a single-use code, or `access_denied`: `{"redirect"}` to the app; 403 for a demo sandbox; 409 over 25 live connections |
| `GET /v1/oauth/connections` | sign-in only | the apps connected to the account (name, publisher, redirect hosts, connected, last used) |
| `DELETE /v1/oauth/connections/{id}` | sign-in only | disconnects the app: every grant and token at once; another user's is the same 404 |
| `GET /v1/me` | token | `catalog {public, tiers, companies}`; `companies` (private grants only when the catalog is public); `sandbox`, `github`, `account`, `api_key` or `connected_app` by kind of caller |
| `GET /v1/companies/search?q=&access=&limit=` | token | tickers, CIKs and any part of a name, best match first (`silver.search_companies`); `limit` 1-25 (default 5); `access=financials`: only companies you can read |
| `GET /v1/metrics` | | metric names served by `financials` (`silver.metrics` + `gold.derived_metrics`) |
| `GET /v1/companies/{id}/financials?metrics=&quarters=&view=&basis=&format=&readings=&callouts=` | financials | values + citation + freshness; `quarters` 1-120; `format=csv`; `readings=true` ([Screener](#screener)); `callouts=true` ([Statement callouts](#statement-callouts)); `currency` and `cadence: "annual"` only when not USD / not quarterly |
| `GET /v1/companies/{id}/components?metric=&period_end=&view=` | financials | what one figure is made of: inputs, quarters, parts, tags and filings ([Reported vs standardised](#reported-vs-standardised)) |
| `GET /v1/companies/{id}/filings/{accession}/figures` | financials | one filing's own figures, and net debt, cash and investments and FCF from it alone |
| `GET /v1/companies/{id}/insiders?limit=&cursor=&before=` | filings | Forms 3, 4, 5, newest first, with owners, lines and a summary ([Insider filings](#insider-filings)) |
| `GET /v1/companies/{id}/text-kpis?years=` | filings | backlog, orders and order growth as the filings state them in words; `years` 1-15 (default 10) ([Backlog and orders](#backlog-and-orders-from-the-text)) |
| `GET /v1/companies/{id}/segments?axis=&metric=&quarters=&view=&basis=&format=` | financials | values per segment member, exact decimals, a citation per value ([Segments](#segments)) |
| `GET /v1/companies/{id}/filings?limit=&accession_no=` | filings | recent parsed filings (or one) and their section outline, no text |
| `GET /v1/companies/{id}/filing-index?form=&from=&to=&limit=&offset=` | token | every filing in the SEC index (public metadata), newest first; `sections` count only where the caller reads filings |
| `GET /v1/filing-index?company_id=&form=&from=&to=&limit=&offset=` | token | the same across up to 50 companies (default: the caller's readable companies) |
| `GET /v1/filings/latest?limit=` | token | newest parsed filings the caller can read |
| `GET /v1/companies/{id}/sections/{key}?form=&accession_no=&limit=&max_chars=` | filings | section text + citation; `form` 10-K, 10-Q, 8-K, 20-F, 40-F or 6-K; `max_chars` default 8,000, up to 1,000,000 |
| `GET /v1/companies/{id}/filings/{accession}/changes?section=&basis=&kind=&view=&q=&numbers=` | filings | new / removed / rewritten Risk Factors and MD&A paragraphs against the earlier filing; `view` reads them for materiality ([What changed](#what-changed)) |
| `GET /v1/search?q=&company=&form=&section=&from=&to=&limit=&offset=&sort=&window=` | filings (every company searched) | best passage per section, with snippet, citation and a total ([Search](#search)) |
| `GET /v1/companies/{id}/events` | filings | 8-Ks since the latest 10-Q/10-K period end: items, an excerpt, the press release, `announced`, `stale` figures ([Earnings and events](#earnings-and-events)) |
| `GET /v1/companies/{id}/earnings?quarters=&tables=` | filings | each quarter's earnings release (Item 2.02; a foreign filer's 6-K), `quarters` 1-8 (default 4): headline, outlook, adjustments; `tables=true` adds the release's tables as grids |
| `GET /v1/companies/{id}/data-notes` | financials | data-quality and freshness notes, ranked, each with its filings, and the company's coverage gaps ([Data notes](#data-notes)) |
| `GET /v1/companies/{id}/annual-per-share?years=` | financials | EPS and weighted shares per fiscal year as the 10-K states them (036; `years` 1-30, default 10) |
| `GET /v1/companies/{id}/per-share?view=&basis=&shares=&quarters=` | financials | EPS and weighted shares on one share basis: `view` quarter, ttm or annual; `shares` adjusted (default) or as_filed; `quarters` 1-120 (default 40) ([Per-share figures](#per-share-figures-on-one-share-basis)) |
| `GET /v1/companies/{id}/restatements?as_of=` | financials | every quarter or TTM sum a later filing changed: original and restated value, each with its filing and day, and the kind (`split`, `another_line`, `same_line`) ([Point in time](#point-in-time)) |
| `GET /v1/data-notes?limit=&company_id=` | financials (every company covered) | the newest notes across the caller's companies, or up to 50 named ones |
| `POST /v1/formulas/validate` | token | AST, unit and inputs, or errors with positions ([Formulas](#formulas)) |
| `POST /v1/formulas/evaluate` | financials, every company in the formula | series; each point cites every input's filing |
| `POST /v1/sheets/values` | financials, once per company asked | up to 200 figures (25 companies) for a spreadsheet, each answered alone: value or the reason there is none, with its period and citation ([Spreadsheets](#spreadsheets)) |
| `GET /v1/screener?columns=&where=&...&metric=&format=` | token | companies with a figure in these columns: metadata + latest values where permitted, each with how to read it ([Screener](#screener)); `metric`: up to 5 of the caller's own metrics as columns ([Your data](#your-data)) |
| `GET /v1/screener/columns` | | column keys, filer categories, size bands |
| `GET /v1/watchlist?columns=&sort=&format=` | token, app DB | the caller's watchlist: metadata, last report, next expected filing, values where permitted |
| `PUT` / `DELETE /v1/watchlist/{id}` | token, app DB | add / remove a company (grants nothing) |
| `GET /v1/calendar/expected-filings?days=&scope=&format=` | token | expected 10-Q/10-K filings in the next N days (watchlist or readable companies) |
| `GET /v1/companies/{id}/expected-filings?periods=` | token | estimated next 10-Q/10-K dates with their basis |
| `GET /v1/filings/{accession}/recap?metrics=` | financials (flags: filings) | metrics against the prior quarter and a year ago, cited; one-time-item flags ([Filing recap](#filing-recap)) |
| `GET /v1/companies/{id}/peers?limit=&columns=&access=` | financials | suggested peers by business, else SIC and size, each with why ([Peers](#peers)) |
| `GET /v1/companies/{id}/peers/check/{candidate}` | financials on `{id}` | can `candidate` join these comps, and if not why |
| `GET /v1/sectors` | token | the open sector and industry grouping over SIC codes (migration 046) |
| `GET /v1/connections` | token | providers and the caller's market-data connections (never the key); both empty without `DATA_CONNECTIONS_KEY` ([Connect your data](#connect-your-data)) |
| `PUT /v1/connections/{provider}` | `DATA_CONNECTIONS_KEY`, token | `{"api_key": "…"}`: validated with one provider call, stored encrypted |
| `DELETE /v1/connections/{provider}` | `DATA_CONNECTIONS_KEY`, token | deletes the key and every price fetched with it |
| `GET /v1/companies/{id}/prices?from=&to=` | `DATA_CONNECTIONS_KEY`, financials, own connection | the caller's end-of-day closes, refreshed at most once per US Eastern day |
| `GET /v1/companies/{id}/valuation` | `DATA_CONNECTIONS_KEY`, financials, own connection | market cap, P/E, P/S, EV/EBIT from the caller's latest close and the latest gold figures |
| `GET /v1/alerts/unread` | token, app DB | the bell: unread alerts (filings and watched screens), the newest one's time, the job's last look ([Filing alerts](#filing-alerts)) |
| `GET /v1/alerts/notifications?company_id=&limit=` | token, app DB (figures: financials now) | the caller's alerts, newest first, with key figures and red lines where still readable |
| `POST /v1/alerts/notifications/read` | token, app DB | `{"ids": [...]}` (filing or screen alerts), `{"company_id": ...}` (filing alerts) or `{"all": true}` |
| `GET /v1/alerts/settings` | token, app DB | followed companies and their forms, webhooks (masked), email status, the last check |
| `PUT /v1/alerts/settings/{id}` | token, app DB | `{"forms": [...], "other_8ks": bool}` for one company (`other_8ks` absent = unchanged) |
| `GET /v1/alerts/red-lines?company_id=&evaluate=` | token, app DB (`evaluate`: financials) | the caller's red lines; `evaluate=true` checks each on the latest figures |
| `POST /v1/alerts/red-lines` | token, app DB | `{company_id, label, formula, view, op, threshold}`; no ticker; at most 10 per company, 200 per user |
| `DELETE /v1/alerts/red-lines/{rule}` | token, app DB | the caller's own; another's is the same 404 |
| `GET /v1/alerts/webhooks` | token, app DB | masked URL, format, on/off, last delivery |
| `POST /v1/alerts/webhooks` | token (not a demo sandbox), app DB | `{"url", "format"?}`: https to a public address only; the signing secret shown once; at most 3 |
| `PATCH /v1/alerts/webhooks/{id}` | token, app DB | `{"enabled": bool}` |
| `POST /v1/alerts/webhooks/{id}/rotate` | token, app DB | a new signing secret, shown once |
| `DELETE /v1/alerts/webhooks/{id}` | token, app DB | the webhook and its delivery log |
| `POST /v1/alerts/webhooks/{id}/test` | token, app DB | a signed test event now (5 an hour) |
| `GET /v1/alerts/deliveries?webhook_id=&limit=` | token, app DB | the delivery log: status, attempts, last HTTP status, error code, next try (`screen_alert_id` for a screen alert's) |
| `GET /v1/watches` | token, app DB | the caller's watched screens: what alerts, lines, the last check (`outcome`, `companies`) and the limits ([Saved screens that alert](#saved-screens-that-alert)) |
| `PUT /v1/watches/screen/{item}` | token, app DB | watch one of the caller's saved screens: `{entering, leaving, columns, lines: [{column, label?, direction, threshold, company_id}]}`; another's screen is the same 404; at most 20 |
| `DELETE /v1/watches/screen/{item}` | token, app DB | stop watching (the screen stays) |
| `GET /v1/alerts/screens?item_id=&limit=` | token, app DB (companies: financials now) | the caller's screen alerts, newest first, only companies still readable (`hidden` counts the rest) |

The portfolio and decision journal (sign-in only, never an API key or the MCP server; owner-only,
app DB; [Portfolio](#portfolio)):

| Route | Needs | Returns |
|---|---|---|
| `GET /v1/portfolio` | token | holdings with lots and sells, cost, realised gain; value, gain and weight on the caller's own price (source, day, `stale`); next report; linked project or thesis; `review_due` |
| `GET /v1/portfolio/position/{id}` | token | `{"position": {shares, open, first_bought, added_at} \| null}` (the company page) |
| `POST /v1/portfolio/holdings` | token | `{company_id \| symbol, name?, lot}`: a buy, making the holding if there is none; a ticker resolves to a company, else a not-covered symbol |
| `DELETE /v1/portfolio/holdings/{id}` | token | the holding, its buys, sales and price (the journal stays) |
| `POST /v1/portfolio/holdings/{id}/lots`, `DELETE /v1/portfolio/lots/{lot}` | token | a buy `{shares, cost_total \| cost_per_share, bought_on?, account_label?}` / delete one (409 if a sale would then sell more than held) |
| `POST /v1/portfolio/holdings/{id}/sells`, `DELETE /v1/portfolio/sells/{sell}` | token | a sale `{shares, price, sold_on}` (409 over the shares held that day) / delete one |
| `PUT` / `DELETE /v1/portfolio/holdings/{id}/price` | token | the caller's own price `{price, on}` (covered companies only) / clear it |
| `PUT /v1/portfolio/holdings/{id}/link` | token | `{project_id}`, `{thesis_item_id}` or `{}`: the caller's own Analysis project or thesis |
| `POST /v1/portfolio/import/preview` | token | Fidelity's Portfolio Positions CSV as the raw body (512 KB): rows matched or not covered, skipped rows and why, what's held; saves nothing, never returns an account number |
| `POST /v1/portfolio/import` | token | `{positions, price_on?}` chosen from a preview; companies matched again server-side |
| `POST /v1/portfolio/prices/refresh` | `DATA_CONNECTIONS_KEY`, own connection | asks the caller's provider for their open holdings' closes |
| `GET /v1/portfolio/journal?company_id=&symbol=&limit=` | token | entries newest first: `kept`, `editable_until`, `corrects` |
| `POST /v1/portfolio/journal` | token | `{company_id \| symbol, kind, why, sell_rules?, red_line_ids?, confidence?, decided_on?, corrects?}` |
| `PATCH` / `DELETE /v1/portfolio/journal/{entry}` | token | within 24 hours of writing it; after, 409 (add a correction) |
| `GET /v1/datasets` | token | the caller's datasets (fields and units, currency, counts, days, `known_from`), the limits, `uploads_allowed` ([Your data](#your-data)) |
| `POST /v1/datasets` | token, not a demo sandbox; key: write | multipart: `file` (CSV) and `name`, optionally `title`, `company_column`, `date_column`, `published_column`, `units`, `currency`, `decimal`, `dry_run`; 422 `file` with problems by row and column; 409 at a limit or a taken name; 429 per hour; 507 when everyone's space is full |
| `POST /v1/datasets/{id}/upload` | the same | a later file into one: `mode` replace (default) or append (same fields; unchanged rows skipped) |
| `GET /v1/datasets/{id}`, `PATCH`, `DELETE` | token (DELETE sign-in only) | one dataset; its `title`, `units`, `currency`; delete it and its rows. Another user's is the same 404 |
| `GET /v1/datasets/{id}/rows?company_id=&from=&to=&as_of=&versions=&limit=&offset=` | token | rows by company, newest day first, exact values by field; as known at the end of `as_of`, or every version |
| `GET /v1/companies/{id}/datasets?as_of=` | token | the caller's datasets with rows for this company and their latest 12 days (the company page's Your data) |

Saved work, preferences and the routes only agent-api calls (all owner-only, app DB):

| Route | Needs | Returns |
|---|---|---|
| `POST /v1/companies/{id}/model` | financials | runs a model state: history, cases, valuation; stores nothing ([Models](#models-one-copy-of-the-maths)) |
| `GET` / `POST /v1/workspaces` | token | the caller's workspaces / a new one (`{name, project?}`) ([Workspaces](#workspaces-saved-work)) |
| `GET` / `PATCH` / `DELETE /v1/workspaces/{id}` | token | one workspace with its items (theses' and reports' states null) / `{name?, project?}` / delete |
| `POST /v1/workspaces/{id}/duplicate` | token | a copy, with its `project` |
| `POST /v1/workspaces/{id}/items` | token | a new item (chart, comps, screen, thesis, model) |
| `GET` / `PUT` / `DELETE /v1/workspace-items/{id}` | token | one item with its state / save over or rename / delete |
| `POST /v1/workspace-items/{id}/open` | token | the state, recorded for Home |
| `POST /v1/workspace-items/{id}/duplicate` | token | a copy in the same or another workspace |
| `GET /v1/workspace-items/{id}/versions[/{version_id}]` | token | a thesis's or model's earlier versions / one with its state |
| `GET /v1/recent`, `PUT /v1/recent/filings` | token | recently opened items and filings / record a filing read |
| `POST /v1/workspaces/{id}/reports`, `PUT /v1/workspaces/{id}/reports/{item_id}` | agent-api only (nginx 404s them) | store or regenerate a report ([Reports](#reports)) |
| `POST /v1/thesis-checks/{item_id}` | agent-api only (nginx 404s it) | store a thesis check ([Thesis checks](#thesis-checks)) |
| `GET /v1/workspace-items/{id}/checks[/{check_id}]` | token | a thesis's checks, newest first / one with its result |
| `GET /v1/views`, `PUT` / `DELETE /v1/views/{view}` | token | saved views and defaults / save / reset ([Customizable views](#customizable-views)) |

Prometheus metrics are on `/metrics` (including `tributary_permission_checks_total{tier,result}`);
the web proxy blocks it and `/docs` from the public. OpenAPI docs at `http://localhost:8000/docs`.

## Run

```bash
make up-all                                       # in docker-compose, on http://localhost:8000
set -a; . ./.env; set +a; .venv/bin/query-api     # locally
make token USER=alice                             # query-api-token: dev JWT for a user
.venv/bin/query-api-account list                  # owner-created password accounts (below)
.venv/bin/query-api-alerts                        # one run of the filing alerts job (Helm CronJob alerts)
.venv/bin/query-api-demo-cleanup                  # delete expired demo sandboxes (Helm CronJob demo-cleanup)
```

Config (env): `DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (all required; the secret
must be at least 32 bytes), `APP_DATABASE_URL` (the app database, for watchlists and workspaces;
unset makes those routes answer 503), `FGA_API_TOKEN` (OpenFGA preshared key, when enabled),
`DEMO_USERS` (comma-separated accounts allowed to sign in without a password; unset disables
the demo routes), the demo sandbox settings below, `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` /
`GITHUB_REDIRECT_URI` ([GitHub sign-in](#github-sign-in)), `DATA_CONNECTIONS_KEY` ([Connect
your data](#connect-your-data)), `API_KEY_PER_MINUTE` / `API_KEY_PER_DAY` (each API key's limits,
default 60 and 10,000; [API keys](#api-keys)), `OAUTH_ISSUER` (the site's origin: connected apps
on; `OAUTH_RESOURCE` overrides the MCP server's URL, default `OAUTH_ISSUER` + `/mcp`; [Connected
apps](#connected-apps-oauth)), `PORT` (default 8000), `LOG_LEVEL`. Password
accounts and API keys need no other setting: their routes work whenever `APP_DATABASE_URL` is set. The alerts job reads
`DATABASE_URL`, `APP_DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (webhook secrets),
`PUBLIC_APP_URL` (where alert links point; default the public site) and the email settings in
[Filing alerts](#filing-alerts).

## Tests

`pytest services/query-api` (install with `pip install -e 'services/query-api[test]'`). Route
tests use a fake authorizer and store; `*_db.py` tests need `TEST_DATABASE_URL` (the Tributary
schema) or `APP_TEST_DATABASE_URL` (the app schema). Two guard the access model: `test_rls.py`
wires the API to an authorizer that allows everything and checks that row-level security alone
still hides ungranted rows, and `test_catalog_queries.py` fails if a route passes a catalog-sized
id list into SQL. Every test file and what it covers: [DESIGN.md, Tests](DESIGN.md#tests).
Fixtures: [tests/fixtures](tests/fixtures/README.md).

## Public metadata vs permissioned data

Company identity and metadata (`silver.companies`), the SEC filing index (`silver.filings`) and
what derives only from them (expected dates, peer candidates, `gold.financials_coverage`) are
public to any signed-in caller, still through `Store`. Values need `financials`, section text
`filings`. Routes that list many companies (screener, watchlist, peers) read values only for the
caller's readable companies and null the rest. `readable()` never asks OpenFGA to list the catalog,
and the catalog's ids never go into SQL: `Readable.ids(tier)` is `None` when the catalog is public
at that tier, and RLS alone bounds the rows. Design notes: what changes per route with the catalog
public, `jit = off`, inlining under RLS ([DESIGN.md](DESIGN.md#public-metadata-vs-permissioned-data)).

## Formulas

Chart layers can be formulas (`formula/`; the grammar is in `grammar.py`'s docstring):
`net_income / revenue`, `revenue / revenue[-4q] - 1`, `KO.revenue / PEP.revenue`,
`ttm(revenue)`, `abs(free_cash_flow)`. Both routes take
`{"formula", "default_company", "basis", "view", "from", "to"}`. Units are checked, a
dimensionless result says how it reads (`display`: percent, multiple or number), and inputs in
two currencies are refused (422 `currency`). The compiled SQL holds no user input. An unknown
ticker and an unreadable one give the same error. `mine.<dataset>.<field>` (or
`NVDA.mine.<dataset>.<field>`) reads the caller's own data ([Your data](#your-data)). Grammar
limits, alignment, n/m points and error shapes: [DESIGN.md](DESIGN.md#formulas).

## Screener

`GET /v1/screener`: every company with a figure in at least one requested column (`<metric>_ttm`
or `<metric>_q` for every catalog metric), with filters (SIC, sector, exchange, filer category,
size bands, `where=<column>:<op>:<number>`, `issue`), sorts, `limit`/`offset` and `format=csv`.
Companies you can't read are listed with metadata only and never match a value filter.
`readings` say how each value reads (migration 035, the only definition: `not_meaningful`,
`not_applicable` for banks, `old`, `behind`, `stale` and `not_computed` (069), `other_currency`
(056)); every reading but `behind` sorts after real values and never passes a filter or a band.
`/financials?readings=true` adds the same per row. Each row also carries `confidence` (migration
074, `gold.company_confidence`): `{level: high | medium | low, reasons: [...]}` in words, from public
signals (the checks' findings per 1,000 figures, the filings' own statements against gold, how old
the latest figures are, the core figures on file, the currency, shells, pools, trusts and BDCs);
`null` without the financials tier or a check. The web's company header reads it from its screener
row. The values come from the latest figures the gold build stores for the screener (migration 078),
read through the same rules with today's date and under RLS (a company with nothing stored is read
live): a cold first screen reads a few thousand pages instead of ~50,000. Filters, the JSON shape,
sectors and co-registrants: [DESIGN.md](DESIGN.md#screener).

## Per-share figures on one share basis

`GET /v1/companies/{id}/per-share` (`per_share.py`, its docstring is the spec) is the one
per-share pipeline the Statements tab reads. Split-adjusted by default (each row keeps `as_filed`
and the `splits` applied); fiscal Q4 and TTM are derived and labelled (`derivation`);
`shares=as_filed` reads each period as its own filing stated it (140). Gold and
`/financials` keep every per-share figure as filed. `/annual-per-share` stays (the 10-K's own
figures; MCP's tools read it). Rules: [DESIGN.md](DESIGN.md#per-share-figures-on-one-share-basis).

## Spreadsheets

`POST /v1/sheets/values` (`sheets.py`, its docstring is the spec) serves the Excel add-in and the
Google Sheets script (web `public/addins`): up to 200 asks of `{company, metric, period, as_of,
currency, shares, count}`, a ticker or CIK each, answered in order. One `allowed()` per distinct
company before any read, then the single routes' Store reads under RLS (`financials`,
`per_share_inputs`, and `sheet_readings`: `gold.latest_reading` and `gold.period_status`); an unknown
name and an unreadable company read the same (`not_permitted`). `value` only when a cell may show a
number (`current`, `behind`, `ok`), else `status` and `reason` (stale, old, not_meaningful,
not_applicable, not_computed, other_currency, no_figure, invalid). Periods: latest, TTM, FY, FY2024,
FY2024 Q3, CY2024 Q3, a quarter's end day, TTM before a quarter. An amount is a number only in the
currency asked (default USD). Counts once against a key's limits; at most 25 companies (and as-of
days) per request, 40 periods a series. Rules: [DESIGN.md](DESIGN.md#spreadsheets).

## Point in time

`as_of` (a past day) on `/financials`, `/per-share`, `/annual-per-share`, `/formulas/evaluate`
(body), `/screener`, `/filings`, `/sections`, `/search` and `/restatements`: what Tributary would
have shown at the end of that day, from the filings filed by then (gold's history, migrations
085-087). The day is echoed as `known_on` (`as_of` stays the response's time); before 2009 or after
today is a 422. `/financials?originals=true` adds `original` to each figure a later filing changed
and `history` (false until 086's catch-up has built the company's history: no figure can be marked
yet) (opt-in: the agent's JSON is unchanged). Readings are against the day (stale); segments, data notes
and the confidence mark stay today's. Spec: `point_in_time.py`'s docstring; rules:
[DESIGN.md](DESIGN.md#point-in-time).

## Watchlist and calendar

`GET /v1/watchlist` reads the caller's rows in the app database's `watchlists`
(`APP_DATABASE_URL`; unset → 503) and returns per company the public metadata, `last_filing`
(latest 10-K/10-Q or amendment: form, filing date, period), `next_expected` (below), `added_at`
and, only where the caller can read financials, `values` for `columns` (default `revenue_ttm`,
`revenue_growth_q` (YoY of the latest quarter), `operating_margin_ttm`, `free_cash_flow_ttm`).
A watchlist entry grants nothing. `PUT`/`DELETE /v1/watchlist/{id}` add or remove a company
(at most 500; an unknown company is a 404). `GET /v1/calendar/expected-filings?days=30` lists
expected filings of the watchlist (`scope=readable`: of companies you can read) up to `days`
ahead, including ones already due or overdue.

## Expected filing dates

`gold.expected_filings` (estimates, labelled `estimate: true` with `basis` and `method`): the
next fiscal period ends one quarter after the last reported one (snapped to month ends, else
91 days for 52/53-week years); it's a 10-K when it ends within 20 days of the fiscal year end.
`expected_date` = period end + the median filing lag of the company's last 8 10-Qs (or 4 10-Ks),
needing at least 2, capped at the SEC deadline (`basis` `history` or `history_capped`), else the
deadline (`deadline`); no history and no filer category → no date (`basis` null). SEC deadlines
(Form 10-K / 10-Q General Instruction A): 10-K 60 / 75 / 90 days, 10-Q 40 / 40 / 45 days after
the period end for large accelerated / accelerated / non-accelerated filers, moved off weekends
(Rule 0-3; holidays not modelled). `status`: `upcoming`, `due` (period over, before the
deadline), `overdue`. Companies with no periodic report in 400 days get none.

## Filing recap

`GET /v1/filings/{accession}/recap`: per metric, the current value against the prior quarter and
a year ago, cited, with `change` and `pct_change`. An unknown accession is the same 403 as a
company you can't read. `shapes.percent_change` is the one rule for a percentage change (recap and
alerts); the recap says why there is none in `pct_not_meaningful`. With `filings` access, `flags`
lists one-time-item wording in MD&A and EX-99 sections; flags are optional and never fail the
recap. Details: [DESIGN.md](DESIGN.md#filing-recap).

## Peers

`GET /v1/companies/{id}/peers` (`peers.py`, its docstring is the spec): `require(financials)`
first, then peers by business (the similarity of a 10-K's Item 1 or a 20-F's Item 4 and
competitors named, built weekly by parser-peers,
[services/parser](../parser/README.md#peers-by-business)) when the company has a profile, else by
SIC and size. A customer, supplier, partner or owner either text names is left out (160). Each peer
says why (`match`); `basis_reason` says why there was no business text; a bank gets bank columns. `/peers/check/{candidate}` says whether a company can join
the comps. Scoring, the fallback and what was measured: [DESIGN.md](DESIGN.md#peers).

## Workspaces (saved work)

Named folders of saved items in the app database (`workspace_api.py`, `workspaces.py`, app
migrations 004 and 009): charts, comps sets, screens, theses and models. A workspace can be an
**analysis project** (app migration 012: company, peers, notes, guided steps done). Owner-only
(forced RLS; another user's is the same 404), strictly validated, size-capped, and they grant
nothing: opening an item reads data through the permission-checked routes as usual. Not streamed
and not shared. Kinds, limits and what sharing would need: [DESIGN.md](DESIGN.md#workspaces-saved-work).

## Theses and models

Two item kinds the user writes (app migration 009) through the generic item routes. A `thesis` is
Markdown up to 100,000 characters with `[[embed:<item id>]]` lines; a `model` is the user's
assumptions per year and case (base, bear, bull) plus optional F2 fields. A save that changes a
thesis's or model's state keeps the version it replaced (app migration 014, the newest 30). Only
what the user typed is stored. Fields and limits: [DESIGN.md](DESIGN.md#theses-and-models).

## Models: one copy of the maths

`POST /v1/companies/{id}/model` (`model_api.py`) runs a model; the maths exist only in
`model_maths.py`, whose docstring is the spec. The web app's model page, thesis embeds and
agent-api's thesis check all call it. `require(financials)` first, then `Store.model_inputs` under
RLS; nothing is stored; bodies over 128 KiB are refused. The answer has history, TTM, the current
year, per-case defaults and projected years, and with a `price` a valuation (multiples only for
the current TTM and forecast years; none across two currencies). What it computes and the
measurements: [DESIGN.md](DESIGN.md#models-one-copy-of-the-maths).

## Reports

A report is a workspace item of kind `report` (app migration 005, `reports.py`) that agent-api
writes. `POST /v1/workspaces/{id}/reports` and `PUT .../reports/{item_id}` are for agent-api only
(nginx 404s them); the generic item routes refuse to create a report or write its state. Strict
`ReportState`, owner-only, grants nothing. Details: [DESIGN.md](DESIGN.md#reports).

## Thesis checks

"Check my thesis" results (app migration 010, `thesis_checks.py`), one row per check, kept with
the thesis. `POST /v1/thesis-checks/{item_id}` is agent-api only (nginx 404s it); the web app reads
`GET /v1/workspace-items/{item_id}/checks[/{check_id}]`. `CheckResult` is strict: a supported or
contradicted claim cites a verified filing; a claim made of the user's own model's figures has
status `model`. Kept in a table, not the thesis's state, so a verdict can't be forged. Rules:
[DESIGN.md](DESIGN.md#thesis-checks).

## Customizable views

Per-user preferences in the app database (migration 006, `views.py`): Home's cards and the
screener's and watchlist's column sets. With nothing saved, Home is the watchlist, new filings and
data notes (`DEFAULT_HOME`; the web app applies the same). Owner-only, validated, grants nothing.
Details: [DESIGN.md](DESIGN.md#customizable-views).

## Status page

`GET /v1/status/data` (`status.py`) backs the web app's public `#/status` page. No token, so it
returns only aggregate counts and timestamps (freshness, ingest latency, completeness, coverage,
data quality by tier with findings per 1,000 figures, the parser-output checks, the coverage
sweep's gaps per kind and reason (`gaps`, 076), the public catalog's size (`coverage.catalog_companies`,
141: the sidebar's count, so every page says one number), the last recorded suite runs), never a company, ticker, accession number, value or text. Each part comes from a SECURITY DEFINER function of migration 028, and `status.shape` copies
named fields only. Cached 60 s per process. Fields and an example: [DESIGN.md](DESIGN.md#status-page).

## Demo sandboxes

"Try the demo" (`sandbox.py`, `demo.py`, app migration 003) creates a temporary real user in one
app-DB transaction; CDC and authz-sync grant its access like any user's. `query-api-demo-cleanup`
(hourly CronJob) deletes expired ones. How it works: [DESIGN.md](DESIGN.md#demo-sandboxes).

| Env | Default | |
|---|---|---|
| `DEMO_SANDBOX_ENABLED` | false | needs `APP_DATABASE_URL` |
| `DEMO_SANDBOX_TTL_HOURS` | 24 | 1-168 |
| `DEMO_SANDBOX_PER_IP_PER_HOUR` | 3 | creations per client address (`X-Client-IP` from nginx, stored as an HMAC keyed by the JWT secret) |
| `DEMO_SANDBOX_MAX_LIVE` | 200 | unexpired sandboxes at once; then "The demo is full right now" |
| `DEMO_AGENT_ENABLED` | true | false: the assistant refuses sandbox users (429 with a clear message) |
| `DEMO_AGENT_QUESTIONS_PER_USER_PER_DAY` | 10 | per sandbox per UTC day |
| `DEMO_AGENT_QUESTIONS_PER_DAY` | 200 | across all sandboxes per UTC day |

In the Helm chart these come from `demo.sandbox` in values.yaml (chart README, "Demo sandboxes").

## GitHub sign-in

"Sign in with GitHub" (`github.py`, app migration 007) gives a visitor a permanent account with
the same access as a demo sandbox (user `gh-<GitHub id>`, a 7-day JWT). Off unless both client
settings are set; needs `APP_DATABASE_URL`. No OAuth scopes; the GitHub access token is never
stored, logged or returned. The flow, the first sign-in's rows and privacy:
[DESIGN.md](DESIGN.md#github-sign-in).

| Env | |
|---|---|
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | the OAuth app's; both, or sign-in is off |
| `GITHUB_REDIRECT_URI` | the OAuth app's "Authorization callback URL", e.g. `https://<host>/api/query/v1/auth/github/callback` |

Outbound: only `github.com` and `api.github.com` over HTTPS (Helm `uses: github`).

## Password accounts

Username/password accounts the owner creates by hand (`passwords.py`, `password_accounts.py`,
app migration 013), for testers who shouldn't need GitHub. **There is no signup**: no route
creates an account. The owner runs the CLI inside the Query API pod (Helm chart README,
"Password accounts"):

```bash
query-api-account create <username> --questions-per-day 100  # prompts twice for the password
query-api-account set-password <username>                    # prompts; unlocks; ends sessions
query-api-account set-limit <username> --questions-per-day 50
query-api-account disable <username>                         # refuses sign-in, ends sessions
query-api-account enable <username>                          # also unlocks
query-api-account list                                       # never prints a hash
```

The password is prompted (or one stdin line), never an argument. Hashes are stdlib scrypt.
Sign-in (`POST /v1/auth/password`) gives the same 401 for every refusal and runs one scrypt per
attempt; 10 failures lock an account for 15 minutes. A disable or new password ends sessions
within 10 s. The account's assistant questions come from its own `questions_per_day`. Hashing,
sign-in, sessions and the assistant cap: [DESIGN.md](DESIGN.md#password-accounts).

## API keys

Personal access tokens for a user's own scripts and their own AI models through the
[MCP server](../mcp-server) (`api_keys.py`, its docstring is the spec; app migration 019).

- **The key**: `trb_` + 32 random bytes, shown once; the app DB keeps its SHA-256 and first 8
  characters. Logs carry the key id, never the key.
- **Authentication**: `Authorization: Bearer trb_...` beside the JWT on every route that takes a
  token; the request is then the key owner's, through OpenFGA and RLS as usual. A bad key gets the
  same 401 as a bad JWT. Revocation takes effect on the key's next request.
- **Scopes** (default deny: a new route refuses keys until it is listed): `read` covers the data
  routes and the user's own watchlist and saved work; `write` adds changing them. Alerts, views,
  connections, key, demo and sign-in routes, reports and thesis checks refuse every key.
- **Limits**: `API_KEY_PER_MINUTE` (60) and `API_KEY_PER_DAY` (10,000) per key, across replicas;
  429 with `Retry-After`; `X-RateLimit-*` headers on every answer.
- **Who**: any signed-in account but a demo sandbox, 5 active keys each, expiry 30, 90 or 365 days.
- **Docs**: `GET /v1/openapi-keys.json` is generated from the app; the words come from
  `api_key_docs.py` (`ROUTE_DOCS`), never from docstrings. A one-file Python client is in
  [clients/python](../../clients/python/README.md); the spreadsheet add-ins use
  [`POST /v1/sheets/values`](#spreadsheets).

```bash
curl -H "Authorization: Bearer $TRIBUTARY_API_KEY" \
  "https://tributary.lucasgroves.dev/api/query/v1/companies/0001045810/financials?metrics=revenue"
```

Full rules, metrics and tests: [DESIGN.md](DESIGN.md#api-keys).

## Connected apps (OAuth)

The authorization server for the [MCP server](../mcp-server) (`oauth.py` and
`client_metadata.py`, their docstrings are the spec; app migration 025): a user adds
`https://tributary.lucasgroves.dev/mcp` in claude.ai or ChatGPT, the app sends them here to sign
in and approve it on the web app's consent page, and from then on it reads Tributary as them.

- **Discovery**: the MCP server's 401 names its resource metadata (RFC 9728), which names this
  issuer (the site); `/.well-known/oauth-authorization-server` says PKCE S256 only, both client
  registrations, `iss` in every authorization answer (RFC 9207).
- **Clients**: client ID metadata documents (the MCP spec's preferred way; claude.ai and ChatGPT
  use it when the metadata offers it with `none` auth: fetched over https from public addresses
  only, no redirects, 8 KB, 5 s, cached up to a day) or dynamic registration (RFC 7591; 30 an hour
  per address). Redirect URIs: https, or http on a loopback address; matched exactly (a loopback
  port may change).
- **Consent**: the request is sealed (no table) and bound to the browser by a nonce cookie;
  approving needs a sign-in (never a key or an app's token), refuses demo sandboxes, and stores a
  grant and a single-use 60-second code bound to the client, redirect URI and PKCE challenge.
- **Tokens**: opaque, stored as SHA-256; access 1 hour, refresh 30 days and rotated each use; a
  code or refresh token presented twice revokes its grant. An app's token is taken like a read API
  key: the user is the grant's, then OpenFGA and RLS as that user, only the read routes, the key
  limits per connection; account routes never; agent-api refuses it.
- **Settings**: Connected apps lists each app; Disconnect revokes all of its grants at once.

Full rules: [DESIGN.md](DESIGN.md#connected-apps-oauth).

## Connect your data

Each user connects their **own** market-data API key (Tiingo); the Query API fetches end-of-day
prices with it and shows them only to that user. No shared price cache across users, ever
(`connections.py`, `keybox.py`, `providers.py`, `valuation.py`, app migration 011). **Off unless
`DATA_CONNECTIONS_KEY` is set**: then `GET /v1/connections` answers empty lists and every other
route here is a 404. Keys are envelope-encrypted (AES-256-GCM) and never logged or returned. The
provider choice, what to verify before launch, key storage, prices and the valuation formulas:
[DESIGN.md](DESIGN.md#connect-your-data).

| Env | |
|---|---|
| `DATA_CONNECTIONS_KEY` | master key(s), `k1:<base64 32 bytes>[,k0:…]`; unset = feature off |

Outbound: the provider adapters' hosts only (today `api.tiingo.com`) over HTTPS (Helm
`uses: market-data`). Metric: `tributary_market_data_calls_total{provider,kind,result}`.

## Filing alerts

When a company a user watches or analyses files a 10-K, a 10-Q or an 8-K with a material item, the
user gets an alert in the app and, if they add one, at a webhook, with key figures and red lines
(`alerts.py`, its docstring is the rules; app migrations 016-018, owner-only, not streamed). Alerts
are made by `query-api-alerts`, a CronJob every 10 minutes that polls the database (no Kafka, no
SEC calls) and is idempotent. Webhooks go out only through `webhooks.py` (https to public
addresses, checked on save and before every send, signed). Followers, red lines, the job and the
webhook rules: [DESIGN.md](DESIGN.md#filing-alerts).

Email is a provider interface, off: `ALERT_SMTP_HOST` + `ALERT_SMTP_FROM` (`ALERT_SMTP_PORT`
default 587, `ALERT_SMTP_USER`, `ALERT_SMTP_PASSWORD`) turn it on in the job, but no user address
is verified yet, so nothing is sent. SMTP also needs `alertEmail.enabled` in the Helm values.

## Saved screens that alert

A user watches one of their saved screens (a workspace item of kind `screen`) and hears when a
company enters or leaves it, or a column's value crosses a line they set (for one company or any
in it), once the nightly figures land: the bell, the Alerts page, their webhooks (event `screen`)
and email once it is on (`watches.py`, its docstring is the rules; `watch_job.py`; app migration
024, owner-only, not streamed; tributary 110). The same `query-api-alerts` CronJob checks them, no
new job: each watch once after `ops.figures_landed_at()` (the nightly bulk job's last step) moves,
or a day later without it, and a new or changed watch at the next run. Each screen runs AS ITS
OWNER (OpenFGA, then Store with their `app.user_id`), only companies they can read, with the
screener's readings (n/m, n/a, old or another currency never cross). The first check, and the first
after the screen, the lines or the owner's access change, only records who's in it. One alert per
watch per check, at most 20 companies listed. Design and judgment calls:
[DESIGN.md](DESIGN.md#saved-screens-that-alert).

## Portfolio

What a user holds, valued only on their own prices, and the decision journal (`portfolio.py`,
`portfolio_maths.py`, `portfolio_import.py`, `journal.py`; their docstrings are the rules; app
migration 021, owner-only, forced RLS, not streamed). Cost is a running average cost; realised gain
is worked out on each sale; a sale of more than was held that day is refused. A price is the user's
own: their Connect-your-data close (only with `DATA_CONNECTIONS_KEY`) or one they typed or imported,
the newer winning, always with its source and day; over 7 days old it is `stale`. A sale's price
is the user's own price of its day (typed), unless they hold a newer one (`price_recorded`). Tributary
fetches no price for anyone. Funds and ETFs (a symbol that resolves to no company the caller reads
financials for) are kept by symbol and cost, without a price. Weights are rounded to a tenth of a
percent by largest remainder, so they add up to 100%; `totals.total_value` is the open holdings on
the user's prices with an unpriced one at its cost (`unpriced_cost`). The journal lists the newest
decided first.

Fidelity's export is read by header names; Account Number is dropped as it is read, an account
label that looks like a number is refused (and the database refuses it too); cash, money-market,
pending and option rows are skipped with a reason. Journal entries can be edited or deleted for 24
hours, then a trigger keeps them as written and a change is a new entry that `corrects` the old.
Open holdings join the filing alerts' follows (source `portfolio`); a review falls due when a
held company files a 10-K or 10-Q, or an 8-K with results (item 2.02: from the index's items, else
the parser's item headings), after the holding was added and after the last `review` entry,
and `GET /v1/alerts/unread` counts it (`reviews_due`). No API key reaches any of it (not in
`KEY_ROUTES`), so neither does the MCP server; agent-api has no tool for it.

## Your data

A user's own time series by company and date, beside Tributary's figures (`datasets.py` and
`datasets_csv.py`, their docstrings are the rules; tributary migration 100, schema `userdata`; app
migration 022 for own metrics). Owner-only: forced row-level security keyed on `app.user_id` and a
`user_id` filter on every statement; another user's dataset or metric is the same 404 or "You have
no dataset". Nothing here grants anything, and none of it is mixed into Tributary's figures, the
status page or anyone else's answers.

- **Upload** a CSV (multipart `file`, preview with `dry_run=true`): a company column (a ticker or
  CIK, matched exactly through `silver.resolve_company`; a row that matches nothing is left out and
  listed by row number, never guessed), a date column (ISO) and columns of numbers. A file with a
  non-number in a field (`=cmd` included) is refused whole, each problem by row and column, never
  quoting a cell. `units` say how each field reads (number, ratio, money, shares, per_share);
  money is in the dataset's `currency`, never converted.
- **Point in time**: a row is known from its declared `published_column` date, else from the
  upload day (US Eastern); as of a day only rows known by its end are read. `mode=append` keeps
  revisions as versions and skips rows that didn't change.
- **Formulas** read `mine.<dataset>.<field>` by bound ids (dataset id, field position): a date is a
  period end, keyed into quarters by gold's own fiscal and calendar rules; inputs carry
  `value_source: "yours"` and `known_at` instead of a citation.
- **Own metrics** (a workspace item of kind `metric`: a formula, a view, at most 20 per user) are
  screener columns `m_<id>` (`?metric=`, at most 5): evaluated at read time over each company's
  latest figures (078's stored readings) and the latest day of the datasets they read, sortable and
  filterable, blank (with the input's reading) where an input doesn't read as a value. No ticker or
  lag in a column metric.
- **Limits**: 20 MB, 200,000 rows and 50 columns a file; 50 datasets and 200 MB of uploads per
  user; 30 requests (10 stored) an hour; one file read at a time per process; demo sandboxes can't
  upload. API keys: reads with `read`, uploads with `write`, delete is sign-in only; the MCP server
  reads them (`list_my_datasets`, `read_my_dataset`); agent-api has no tool.

| Env | |
|---|---|
| `USER_DATA_SPACE_MB` | every user's rows on disk at most (default 4096); past it uploads answer 507 |

Design, limits' reasons, organisation sharing and own files (next): [DESIGN.md](DESIGN.md#your-data).

## Statement callouts

`/financials?callouts=true` (view `quarter`; `notes.py` `callouts`, its docstring is the spec) adds
`callouts`: quarters far outside their neighbours (`outlier`, with `seasonal`) or of the other sign
(`sign`), tested as `ops.check_financials` (041) tests them, plus the newest quarter against the two
before it (`edge`). Without the flag the answer is unchanged. Details:
[DESIGN.md](DESIGN.md#statement-callouts).

## Reported vs standardised

`GET /v1/companies/{id}/components` (`components.py`, its docstring is the spec) opens one figure
into what gold made it of: a derived figure's inputs, a TTM's quarters, a sum's parts, and the tags,
values and filings behind each (`gold.figure_facts`, 049). `GET .../filings/{accession}/figures`
gives what one filing itself says (`gold.filing_figures`, 051); agent-api's thesis check compares
claims with it. Details: [DESIGN.md](DESIGN.md#reported-vs-standardised).

## Segments

`GET /v1/companies/{id}/segments`: revenue, operating income and other `silver.segment_metrics`
per segment member, from `gold.segments` (migration 018; built by facts-fetcher's
`facts-fetcher-segments` from the filings' inline XBRL). `require(financials)` first, then
`Store.segments` as `query_api` under the `granted_financials` RLS policy on
`gold.segment_financials`.

- `axis`: `business_segment`, `geography`, `product_service` or `reconciliation` (corporate,
  eliminations); default all. `metric`: one of `silver.segment_metrics` (read once per process;
  unknown → 422 listing them); default all.
- `quarters` (1-40, default 8): the latest period ends per axis and metric (calendar basis:
  calendar quarters). `view=quarter|ttm`, `basis=fiscal|calendar` as in `financials`.
- Each row: `axis`, `member` (QName as last filed), `member_label`, `member_aliases` (earlier
  QNames merged into it), `metric`, periods, `value` as an exact decimal string, `value_source`
  (`reported`, `derived` = Q4 from annual minus year-to-date, `ttm`), `citation` (accession,
  form, filed, `xbrl_tag`, `xbrl_axis`, `xbrl_member`, `source_accessions`, `filing_url`) and
  `freshness`. `format=csv`: one row per value with the same fields.

## Data notes

`notes.py` (migration 023): data-quality and freshness notes from `ops.check_financials` /
`ops.check_segments` findings, filings companyfacts doesn't carry yet, derived quarters and stock
splits. Findings are about values, so the routes need `financials`. Nothing is dropped: repeats are
grouped into one note and every note has a `rank` (attention, note, minor) and a `summary`. The
wording is the web app's. Kinds, dates and ranking: [DESIGN.md](DESIGN.md#data-notes).
The company route also returns `coverage` (migration 076, `coverage_notes`): the nightly coverage
sweep's gaps for the company (`ops.coverage` under RLS), most telling first, each `{kind, cause, reason,
since, detail}` (detail: dates, forms, accessions, metric names, counts; never values); `[]` on a
database before 076. Not on the cross-company route.
A `tag_disagreement` note (072) also carries `detail.figures` (migration 075): the two figures as the
filing states them, `[{tag, value, unit}]`, gold's tag first, read when the note is read, as the user
(`gold.disagreeing_figures`); `detail.corrected: true` when silver no longer holds both. The stored
finding stays figure-free (014). `statement_mismatch` (074) notes name the line, the factor and the
filing.

## Earnings and events

`/v1/companies/{id}/events` and `/earnings` (`events.py`, `releases.py`, `announcements.py`,
`release_tables.py`; rules in their docstrings). Both `require(filings)`, then read one company's
8-Ks (`silver.company_8ks`, migration 045) and their text under RLS.

- **Events**: 8-Ks since the last 10-Q/10-K period end, with items, an excerpt and the press
  release; `stale` flags the balance-sheet figures an event after the period end can change.
- **Announced under 7.01 / 8.01**: a completed deal or a new financing announced without Item 2.01
  or 2.03 is read from fixed phrases and flagged like those items (`announced`).
- **Earnings releases**: each quarter's 2.02 release with its headline, outlook and adjustments,
  by fixed rules; no beat or miss. `tables=true` adds the release's own tables as grids (parser v7,
  `silver.filing_tables`, migration 067).
- **Foreign filers** (migration 095): a company with no 10-Q in the window gets its 20-F/40-F
  period ends (`Store.foreign_reports`, the public filing index) and its stored 6-Ks
  (`Store.results_6ks`: doc-fetcher stores only results releases) mapped to the quarter or half
  each reports, the first 6-K of a period as its release (its EX-99.1, else the `6-K` section);
  `report` is the 20-F/40-F for a fiscal year, none for an interim period. Search and the sections
  route take `form` 20-F, 40-F and 6-K too. The agent's and MCP tools' `form` enums still list
  10-K, 10-Q and 8-K (a tool-schema change re-records the replays).

The rules, the stale-figure table and what was measured: [DESIGN.md](DESIGN.md#earnings-and-events).

## What changed

`GET /v1/companies/{id}/filings/{accession}/changes`: for a 10-K (Risk Factors `1A`, MD&A `7`)
or 10-Q (`II-1A`, `I-2`), the paragraphs that are new, removed or rewritten since the
comparable earlier filing, from `gold.section_diffs` / `gold.section_changes` (migration 021,
built by the parser's `parser-index`). `require(filings)` first, then `Store` under RLS.
`basis=prior` (default) or `year_ago` (10-Q); `section`; `kind`. Each change has word diffs and
citations into both filings. A section re-parsed since its diff is `stale: true` with no changes.

### Read for materiality

With `view=reading|ranked` (or `q`, `numbers=new`) a second pass runs at query time (`changes.py`,
its docstring is the spec): page furniture, split paragraphs, calendar rollovers, figure-only
rows and a 10-Q's prior-year comparatives (`comparative`: the passage the earlier 10-Q already
wrote about that period, or one about a year no longer presented) are hidden and counted; each
change gets a period, figures, a score and why. Without them the
answer is exactly the stored one. Details and measurements:
[DESIGN.md](DESIGN.md#read-for-materiality).

## Insider filings

`GET /v1/companies/{id}/insiders` (`insiders.py`, its docstring is the spec): Forms 3, 4 and 5 from
`silver.insider_filings` / `insider_transactions` (migration 054) under RLS after
`require(filings)`. Per filing: owners and roles, a summary, each line with its Form 4 code in
words, and a citation; numbers as exact decimal strings. Pages by `cursor`. Not an agent tool.
Details: [DESIGN.md](DESIGN.md#insider-filings).

## Backlog and orders from the text

`GET /v1/companies/{id}/text-kpis` (`text_kpis.py`, its docstring is the spec): backlog, orders and
order growth a company states in words but doesn't tag in XBRL, from its 10-Ks, 10-Qs and earnings
releases, by fixed rules (no model). Each value cites every passage that states it; `latest` says
the newest filing read, so a page can say a figure isn't stated since. Not an agent tool. The
rules and the hand-checked measurements: [DESIGN.md](DESIGN.md#backlog-and-orders-from-the-text).

## Search

`GET /v1/search`: Postgres full-text search over `gold.section_search` (migration 021) through
`gold.search_page` (migration 052), which applies `authz.readable_companies('filings')` itself
because `@@` can't use the GIN index under RLS; snippets are read under RLS. Quoted phrases match
their exact words (065); page furniture is blanked in snippets (068). One result per section,
`sort=relevance|date`, a `total`. With the catalog public and no `company`, ranking takes the
newest window that fills the page (`searched_from` says how far back; `window=exact` skips it).
Queries stop after 8 s with 422 "search too broad". Details: [DESIGN.md](DESIGN.md#search).
