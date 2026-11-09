# query-api

FR8, week 2. HTTP API over the gold tables, read-only except for the caller's own watchlist
(app database); the [agent](../agent) and the [web](../web) app reach data only through it.
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
the catalog. Nothing in the API changes shape except `/v1/me`'s `catalog` (below).

The caller is identified by an HS256 JWT (`sub` = app user_id). The user is never read from
request parameters. Dev tokens: `make token USER=alice`.

| Route | Needs | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok"}` |
| `GET /v1/status` | | pipeline freshness for outside uptime checks: `last_accepted_at` (EDGAR acceptance of the newest filing) and `last_ingested_at`, from `ops.feed_status()` (migration 026); no filing or company |
| `GET /v1/status/data` | | the public status page: freshness, ingest latency, completeness against EDGAR, coverage counts, data-quality totals and the last recorded suite runs; aggregate counts and timestamps only, cached 60 s ([Status page](#status-page)) |
| `GET /v1/demo/users` | `DEMO_USERS` set | demo accounts (404 when disabled) |
| `POST /v1/demo/token` | `DEMO_USERS` set | an 8-hour token for a demo account (never a sandbox user) |
| `GET /v1/demo/sandbox` | `DEMO_SANDBOX_ENABLED` | what "Try the demo" creates: lifetime, plan, tickers, whether every company is open (`catalog_public`), assistant allowance ([Demo sandboxes](#demo-sandboxes)) |
| `POST /v1/demo/sandbox` | `DEMO_SANDBOX_ENABLED` | 201: a new temporary user and its token (expires with the sandbox); 429 when the per-IP or live-sandbox cap is hit. Takes no input |
| `GET /v1/demo/agent-quota` | token | agent-api only (nginx hides it): what POST would still allow today, taking nothing: `{"capped": true, "per_day": n, "left_today": n}`, or `{"capped": false}` for users not limited here |
| `POST /v1/demo/agent-quota` | token | agent-api only (nginx hides it): takes one assistant question (or `{"questions": n}`, 1-5, all or none: a report counts as 3) from a sandbox's or GitHub account's daily caps, or from a password account's own daily cap; 429 with the message to show; `{"demo": false}` for other users |
| `POST /v1/demo/agent-quota/refund` | token | agent-api only (nginx hides it): gives back `{"questions": n}` (1-5) taken for a run agent-api gives back (a model error before any output, a thesis check cut off at max_tokens; at most twice a day per user: agent README, Limits), at most what the caller used today (UTC) and never below zero (a run that started before midnight comes off today's count: usage moves a day, no question is gained); `{"demo": true, "refunded": n}`, or `{"demo": false}` for users not capped here |
| `GET /v1/auth/providers` | | `{"github": true\|false, "password": true\|false}`: which sign-in buttons the web app shows |
| `POST /v1/auth/password` | `APP_DATABASE_URL` | `{"username", "password"}` → `{"token", "expires_in"}` (a 7-day JWT) for an owner-created account; 401 with one body for every refusal, 429 per client address, 422 never echoes the input ([Password accounts](#password-accounts)) |
| `GET /v1/auth/github/start` | `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | 302 to GitHub's authorize page (no scopes) with a signed `state`, and the state's nonce in a short-lived cookie (404 when not configured; [GitHub sign-in](#github-sign-in)) |
| `GET /v1/auth/github/callback?code=&state=` | `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | GitHub's redirect back: 302 to `/#/signed-in?token=…&expires_in=…` (a 7-day JWT, in the URL fragment), or `/#/signed-in?error=<code>` |
| `GET /v1/me` | token | `catalog`: `{public, tiers, companies}`, whether every catalog company is readable to the caller and with which tiers (`companies`: how many); `companies`: with the catalog public, only what the caller reads beyond it (private grants), else every company the caller can read, with tiers; `sandbox` (expiry, plan, assistant allowance) for a demo sandbox user; `github` (login, plan, the demo's company ids, assistant allowance) for a GitHub sign-in; `account` (username, plan, `agent_questions_per_day`) for a password account |
| `GET /v1/companies/search?q=&access=&limit=` | token | tickers, CIKs and any part of a name, best match first (`silver.search_companies`, migration 034): public identity + caller's access; `limit` 1-25, default 5; `access=financials`: only companies you can read, narrowed before the limit |
| `GET /v1/metrics` | | metric names served by `financials`: `silver.metrics` and `gold.derived_metrics`, read once |
| `GET /v1/companies/{id}/financials?metrics=&quarters=&view=&basis=&format=` | financials | values + citation + freshness; `quarters` 1-120 (every quarter there is, the Statements tab's Max); `format=csv`: one row per value with its source filing |
| `GET /v1/companies/{id}/segments?axis=&metric=&quarters=&view=&basis=&format=` | financials | values per segment member (business segment, geography, product/service, reconciliation), exact decimal strings, citation per value ([Segments](#segments)) |
| `GET /v1/companies/{id}/filings?limit=&accession_no=` | filings | recent parsed filings (or one) and their section outline, no text |
| `GET /v1/companies/{id}/filing-index?form=&from=&to=&limit=&offset=` | token | every filing in the SEC index (public metadata), newest first; `sections` count only where the caller can read filings (OpenFGA check, then RLS) |
| `GET /v1/filing-index?company_id=&form=&from=&to=&limit=&offset=` | token | the same across up to 50 companies (default: the caller's readable companies) |
| `GET /v1/filings/latest?limit=` | token | newest parsed filings across the companies the caller can read filings for |
| `GET /v1/companies/{id}/sections/{key}?form=&accession_no=&limit=&max_chars=` | filings | section text + citation; `max_chars` default 8,000, up to 1,000,000 (the reader asks for the whole section) |
| `GET /v1/companies/{id}/filings/{accession}/changes?section=&basis=&kind=&view=&q=&numbers=` | filings | new / removed / rewritten Risk Factors and MD&A paragraphs vs the comparable earlier filing, word diffs, citations in both filings; with `view` read for materiality (noise hidden and counted, periods, figures, score and why; a 10-Q against a year ago by default; topic and new-figure filters) ([What changed](#what-changed)) |
| `GET /v1/search?q=&company=&form=&section=&from=&to=&limit=&offset=` | filings (every company searched) | best passage per section, ranked, with snippet and citation ([Search](#search)) |
| `GET /v1/companies/{id}/events` | filings | 8-K filings since the latest 10-Q/10-K period end, newest first: items (SEC title, kind), an excerpt of the 8-K's own words, the press release exhibit, `announced` (a completed deal or new financing read from a 7.01/8.01 announcement), and `stale`: figures at that period end an event after it may have changed ([Earnings and events](#earnings-and-events)) |
| `GET /v1/companies/{id}/earnings?quarters=` | filings | each quarter's earnings release (8-K Item 2.02), newest first, `quarters` 1-8 (default 4): the release, its headline, outlook passages and management's adjustments (reconciliation tables, dollar notes), each with offsets into the release; no beat or miss ([Earnings and events](#earnings-and-events)) |
| `GET /v1/companies/{id}/data-notes` | financials | data-quality and freshness notes: gaps, overlapping quarters, restatements, derived quarters, filings companyfacts doesn't carry yet, each with its filings ([Data notes](#data-notes)) |
| `GET /v1/companies/{id}/annual-per-share?years=` | financials | EPS and weighted shares per fiscal year, as the 10-K states them (migration 036): per-share figures don't add, so quarters can't give them |
| `GET /v1/data-notes?limit=&company_id=` | financials (every company covered) | the newest notes across the caller's companies, or the named ones of them (at most 50), without derived-quarters notes ([Data notes](#data-notes)) |
| `POST /v1/formulas/validate` | token | AST, unit and inputs, or errors with positions |
| `POST /v1/formulas/evaluate` | financials, every company in the formula | series; each point cites every input's filing |
| `GET /v1/screener?columns=&where=&...&format=` | token | companies with a figure in these columns: public metadata + latest values where permitted, each with how to read it ([Screener](#screener)) |
| `GET /v1/screener/columns` | | column keys, filer categories, size bands |
| `GET /v1/watchlist?columns=&sort=&format=` | token, app DB | the caller's watchlist: metadata, last report, next expected filing, values where permitted |
| `PUT` / `DELETE /v1/watchlist/{id}` | token, app DB | add / remove a company (grants nothing) |
| `GET /v1/calendar/expected-filings?days=&scope=&format=` | token | expected 10-Q/10-K filings in the next N days (watchlist or readable companies) |
| `GET /v1/companies/{id}/expected-filings?periods=` | token | estimated next 10-Q/10-K dates with their basis |
| `GET /v1/filings/{accession}/recap?metrics=` | financials (flags: filings) | metrics vs prior quarter and year-ago, cited; one-time-item flags |
| `GET /v1/companies/{id}/peers?limit=&columns=&access=` | financials | suggested peers by business (10-K Item 1 similarity, competitors named, SIC division and size), each with why; a bank's peers on bank columns; values where permitted ([Peers](#peers)) |
| `GET /v1/companies/{id}/peers/check/{candidate}` | financials on `{id}` | can `candidate` join these comps, and if not why: a foreign filer (20-F/40-F), no XBRL figures yet, no annual report ([Peers](#peers)) |
| `GET /v1/sectors` | token | the open sector and industry grouping over SIC codes (migration 046), each industry's SIC ranges and company count |
| `GET /v1/connections` | token | providers offered and the caller's market-data connections (provider, `…last4`, dates; never the key); both empty without `DATA_CONNECTIONS_KEY` ([Connect your data](#connect-your-data)) |
| `PUT /v1/connections/{provider}` | `DATA_CONNECTIONS_KEY`, token | `{"api_key": "…"}`: validated with one provider call, stored encrypted |
| `DELETE /v1/connections/{provider}` | `DATA_CONNECTIONS_KEY`, token | deletes the key and every price fetched with it |
| `GET /v1/companies/{id}/prices?from=&to=` | `DATA_CONNECTIONS_KEY`, financials, the caller's own connection | the caller's end-of-day closes (exact decimals), refreshed at most once per US Eastern day |
| `GET /v1/companies/{id}/valuation` | `DATA_CONNECTIONS_KEY`, financials, the caller's own connection | market cap, P/E, P/S, EV/EBIT from the caller's latest close and the latest gold figures, each with its date and citation |

`{id}` is the canonical 10-digit `company_id`. Prometheus metrics are on `/metrics` (including
`tributary_permission_checks_total{tier,result}`); the web proxy blocks it and `/docs` from the
public. OpenAPI docs at `http://localhost:8000/docs`.

## Formulas

Chart layers can be formulas (`formula/`, spec in `docs/design/spec.md` §6):
`net_income / revenue`, `revenue / revenue[-4q] - 1`, `KO.revenue / PEP.revenue`,
`ttm(revenue)`, `abs(free_cash_flow)`. Both routes take
`{"formula", "default_company", "basis", "view", "from", "to"}`; `default_company` is the
company_id of metrics written without a ticker.

- **Grammar** (`grammar.py`): numbers, metric names (every base and derived metric),
  `TICKER.metric`, lags `[-Nq]` / `[-Ny]` on any non-constant term, `ttm()`, `abs()`, `+ - * /`,
  unary minus, parentheses. A recursive-descent parser builds an AST; nothing is evaluated.
  Limits: 500 characters, 24 levels of nesting, 100 nodes, 40 quarters of lag, 5 companies,
  20 distinct input series.
- **Checks** (`check.py`): unknown metrics (every one at once, each with "did you mean" when
  there's a close name; `GET /v1/metrics` lists them), metrics without values in the requested
  view (`ttm(eps_diluted)`), a division by a constant that is zero (`revenue / 0`), and units: +
  and - need the same unit (a constant takes the other side's), * and / combine them (USD /
  shares = USD/shares, USD / USD = ratio).
- **Display** (rules in `check.py`'s docstring): a dimensionless result isn't necessarily a
  percent, so both routes also say `display`: `percent` (`net_income / revenue`,
  `revenue / revenue[-4q] - 1`), `multiple` (`NVDA.revenue / MSFT.revenue`, `debt_to_equity`) or
  `number` (`receivables / revenue * 365`, days); `null` when the result has a unit. Values don't
  change, only how a chart reads them. A saved chart layer's `display` overrides it.
- **Tickers** resolve through `silver.resolve_company` (best ticker match). A ticker that
  matches nothing and one the caller can't read give the same error
  ("KO isn't one of your companies"); suggestions come only from the caller's own companies.
- **SQL** (`compile.py`): one parameterized query over `gold.financials`; the SQL text holds
  no user input. Each input row gets a period key: with one company and basis `fiscal`, its
  fiscal quarter; otherwise (basis `calendar`, or several companies) the calendar quarter of
  migration 014's rule, using `calendar_primary` rows. A lag of n moves an input's rows n keys
  later. A point exists where the expression is not NULL: a missing input or a division by zero
  in the data is a gap. `view` picks quarter or TTM inputs; `ttm(...)` forces TTM inside it. The rows are
  also filtered on the input companies' ids as a bound array: `gold.financials` is a UNION
  view, and a company filter that arrives only through a join can't reach its indexes.
- **Errors**: `{"code", "message", "pos", "length", "hint"}`. `validate` returns them with
  200 and `valid: false`; `evaluate` returns 422 (formula) or 403 (company) with
  `{"detail": {"errors": [...]}}`.

`evaluate` checks `can_read_financials` for every company the formula references before the
query, which runs through `Store` under RLS like every other route. Each point has `period`
(`FY2026 Q1` or `CY2025 Q4`), `period_key`, `period_end` (the latest of its unlagged inputs),
`value`, `source_accessions`, and per input its period, value, citation and freshness.

## Public metadata vs permissioned data

Company identity and metadata (`silver.companies`: name, tickers, SIC, exchange, fiscal year
end, state, filer category), the SEC filing index (`silver.filings`: accession, form, dates,
period of report) and what is derived only from them (expected filing dates, peer candidates,
"has financials" via `gold.financials_coverage`) are public: any signed-in caller gets them, still
through `Store`. Financial values need `financials` on that company and section text `filings`,
checked in OpenFGA and again by RLS. Routes that list many companies (screener, watchlist,
peers) take the caller's readable companies from OpenFGA (`readable()`), read values only for
those (in SQL, and RLS filters again) and null any value of other companies before responding.
SQL for these routes is in `db/migrations/017_ui_data.sql` and `032_open_access_speed.sql`.

`readable()` never asks OpenFGA to enumerate the catalog (ListObjects over ~6,000 companies is
capped at 1,000 by default, and slow). `FgaAuthorizer.access()` makes one Check
(`catalog:sec#reader`): closed → ListObjects `can_read_*` as before (two small lists); public →
the user's tiers (ListObjects `subscriber` on `tier`) and private grants (ListObjects `granted`,
which never includes the catalog). The catalog's company ids come from `authz.public_access`
(`Store.catalog_companies`, read under RLS) and are cached for 60 s in the process: every
user sees the same list. `require()` stays a single Check.

**The catalog's ids never go into SQL.** `readable()` keeps the whole answer in Python for layer
1's per-row checks, but a route narrows a query with `Readable.ids(tier)`: a short sorted list
while the catalog is closed (or not public at this tier), `None` when it is public at this tier,
and then the query takes no id list and RLS alone bounds the rows. The list would only repeat
`authz.public_access`, which RLS reads anyway. `tests/test_catalog_queries.py` fails if a
route passes a catalog-sized list. What changes per route when public:

| Route | With the catalog public | Why it has to differ |
|---|---|---|
| `/v1/screener`, `/v1/watchlist`, `/peers` | two stages: `gold.latest_reading` per company for the sort and value filters, the other columns only for the page | pivoting `gold.latest_financials` read and sorted 1.6M rows for a page of 50 |
| `/v1/filings/latest` | documents newest first, stop at `limit` with sections, then the same grouping | grouped every section under RLS, then `LIMIT` |
| `/v1/filing-index` (no `company_id`) | filing-date windows (3, 14, 90, 365 days, all time) on CIKs and `filed_on`, stopping at the first that fills the page: the same rows, because the query sorts newest first | `silver.filings` joined and de-duplicated 5M rows, then `LIMIT` |
| `/v1/search` (no `company`) | ranks the newest window that fills the page (30, 180, 730 days, all time): **`searched_from` in the response says how far back** | can't rank 276,000 passages ("supply chain") in 8 s; see Search |
| `/v1/data-notes` | findings under RLS; "behind" starts from recent filings without facts | gold grouped for every company |
| `/v1/calendar/expected-filings?scope=readable` (the home page) | `gold.expected_filings_recent`: the same estimates from the last 1,500 days of reports | read every 10-K/10-Q of all time; a company with long gaps can get fewer lag samples |

The formula routes' ticker suggestions on an unknown ticker still pass the list (an error path,
an index lookup on a small table).

Every `Store` session also sets `jit = off`: a LATERAL is charged the policy's hashed subplan on each
rescan, so these queries read few rows but cost millions on paper, and JIT spent 0.7-1.6 s compiling
them for nothing.

## Screener

`GET /v1/screener`: every company with a figure in at least one of the requested columns
(`access=financials`: only companies you can read). A row of blanks isn't a result, so it isn't
counted; `q` and `issue` list a company whatever it has, and with the catalog closed a company you
can't read is listed anyway. `issue` (a data-quality check's name, the status page's rows) lists only
companies with a finding of it, read under RLS: one the caller can't read never matches, so it says
nothing about them. The JSON echoes `issue`, so a client can tell an older API that ignored it (and
listed everyone) from an answer. Columns are `<metric>_ttm` (trailing twelve months; balance-sheet metrics at the
quarter end) or `<metric>_q` (latest fiscal quarter) for every metric in the formula catalog,
each the latest value with its own `sources[key]` (`period_end`, `accession_no`, `form`,
`filed`). Filters: `sic` (4-digit, repeatable), `sic_prefix` (1-3 digits), `industry` (SIC
description contains), `q` (name contains or exact ticker), `exchange`,
`fiscal_year_end_month`, `sector` / `sector_industry` (repeatable, exact names from `/v1/sectors`),
`company_id` (repeatable, at most 21: only these companies, including
ones without gold financials, for a comps table or a tearsheet), `filer_category` (`large_accelerated`, `accelerated`,
`non_accelerated`), `revenue_band` / `assets_band` (`lt100m`, `100m-1b`, `1b-10b`, `10b-100b`,
`gte100b` on `revenue_ttm` / `total_assets_q`) and `where=<column>:<gt|gte|lt|lte>:<number>`
(ratios as fractions: `operating_margin_ttm:gte:0.2`). `fiscal_year_end_month` and the
`fiscal_year_end` sort read `silver.fiscal_year_end_month` (migration 042): a 52/53-week year ending
in a month's first week belongs to the month before (J&J's `0103` is December), while the row keeps
the SEC's MMDD. `sort` is a column key or `name`, `sic` (the
Industry column: by its name, not the code), `sector` (sector, then industry: the web app's grouping),
`fiscal_year_end`, `filer_category`, `state_of_incorporation`, `-` for descending; nulls last.
`limit` (500 as JSON, 5000 as CSV), `offset`, `format=json|csv`. JSON:
`{as_of, total, limit, offset, sort, columns: [{key, metric, span, unit, balance}], rows: [{company_id,
name, tickers, exchanges, sic, industry, fiscal_year_end, filer_category, filer_categories,
state_of_incorporation, access: [tiers], values: {key: value}, sources: {key: {...}},
readings: {key: {status, reason}}, latest_period, not_applicable: [metrics], sector, sector_industry,
co_registrants: [{company_id, name, accession_no, filed, filed_by}]}]}`. CSV adds a `notes`
column. `balance`: a point in time, so its `_ttm` column is the same number as its `_q` one.

**Sectors** (migration 046, `GET /v1/sectors`): Tributary's own grouping of the SEC's SIC codes into
12 sectors and 88 industries (not GICS or any licensed scheme), one row per SIC range in
`silver.sic_sectors`; the judgment calls (Alphabet's 7370 stays Technology, Walmart's 5331 is
Consumer Staples retail, UnitedHealth's 6324 Health Care, blank checks and commodity trusts "Funds &
Vehicles") are listed in the migration's header. Rows carry `sector` and `sector_industry` beside the
SIC `industry`, which stays the filing's own label.

**Co-registrants** (#135, migration 047): some companies file one combined 10-K as separate
registrants, a parent and its subsidiary (PG&E Corp and Pacific Gas & Electric, Southern Co and
Georgia Power, Corteva and EIDP) or a REIT and its operating partnership. They are two companies
with their own figures, not a duplicate; `co_registrants` names the other one(s), from the 10-K
accessions filed under more than one company's CIKs in the last 400 days (`silver.co_registrants()`,
10 such filings on 2026-11-09). Read once an hour per process (~190 ms cold); a failure leaves the
list empty rather than failing the screener.

**How a value reads** (`readings`, migration 035, the only definition, used by the screener, watchlist
and peers): `not_meaningful` (with `reason`): a margin on under $100K of revenue, a gross or operating
margin over 100%, any margin over ±1,000%, growth of over 1,000% from under $10M or from a base under
$100K, debt over 100 times equity, debt or equity below zero, ROE or payout over ten times, a current
ratio over 100x. `not_applicable` (with `reason`): a bank's gross and operating margin, FCF, FCF margin,
net debt, debt to equity and current ratio, whether or not a value exists. `old`: more than 300 days
before the company's `latest_period` (the newest of its revenue, net income and total assets, never a
future date). `behind`: more than 45. `values` keeps the exact figure in every case, but all except
`behind` sort after real values and never pass a `where` or a size band: sorting by gross margin
doesn't open on 209,828%. `/financials?readings=true` adds the row's `not_meaningful` (debt/equity and
ROE on that period's negative equity first) and `not_meaningful_periods: [{metric, period_end,
reason}]`, the periods a requested metric has no row because of the company's equity there (gold has
no debt/equity or ROE on negative equity), taken from the company's own latest `quarters` equity
periods whatever metrics were asked (`debt_to_equity` alone still gets them), both by
`gold.not_meaningful` (`Store.equity_readings`, under RLS, after `require()`); off
by default, so the agent's tool sees what it always did. The web app reads these instead of working
out equity itself.

Companies you can't read financials for are **listed with metadata only** (values and sources
null, `access` without `financials`), because identity is public and the screener is how you
find companies; but **they never match a value filter** (it can't be evaluated without reading
the value, and evaluating it would be an oracle), and a value sort puts them last. The SQL
(`screener.py`) holds no user input: column and sort keys come from fixed catalogs, every
value is a bind parameter.

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

`GET /v1/filings/{accession}/recap` resolves the filing in the public index (unknown accession
→ the same 403 as a company you can't read), checks `financials`, then returns per metric and
span (`quarter`, `ttm`) the `current` value (period ending within 7 days of the period of
report), `prior_quarter` (80-120 days earlier) and `year_ago` (350-380 days), each with
citation and freshness, and `change` / `pct_change` (6 decimals; null for ratios, where
`change` is in ratio points, and from 0). Values are as currently in gold, so a restated value
cites the restating filing. With `filings` access, `flags` lists one-time-item keyword matches
(impairment, restructuring, one-time/non-recurring, gain or loss on sale, write-down,
litigation settlement) in MD&A and EX-99 sections, each citing section and character offset;
without it `flags` is null. Flags are optional: they run under their own 2 s timeout, and if they
fail the recap still answers with `flags: null` and `flags_unavailable` saying why (the same field
explains a plan without `filings`).

## Peers

`GET /v1/companies/{id}/peers` (F4, H6): `require(financials)` on the company first, like every
company route, then by business when its 10-K has a Business section on file (`basis: "business"`),
else by SIC and size (`basis: "sic"`). The ranking is specified in `query_api/peers.py`'s docstring:

- **Candidates**: the 60 companies whose latest 10-K Item 1 is most similar to this one's (cosine of
  TF-IDF term vectors, `gold.business_peers`, built weekly by the parser image's `parser-peers` job,
  [services/parser](../parser/README.md#peers-by-business)), plus any company either Item 1 names in
  a sentence about competition (`gold.business_mentions`). All three tables read under RLS at
  `filings` (a similarity needs both companies' filings readable), so a plan without filings text
  falls back to SIC.
- **Score** = similarity x SIC division (same SIC x1.15, same group x1.10, same division x1.0,
  another division x0.80: a maker vs its customers) x size (1 / (1 + 0.3 x |log10 revenue ratio|), only
  when the caller reads both revenues; no current revenue counts as $10M) + 0.05 if this company's
  10-K names it + 0.03 if it names this company. Blank checks (SIC 6770) are never suggested for an operating company.
- **`match`** says why: `similarity`, `terms` (the shared terms that add the most), `named_by_target` /
  `names_target` (the sentence: accession, section, offsets, to cite), `sic_level`, `same_sector`,
  `same_industry`, `same_filer_category`, `revenue_band`, `same_revenue_band`. `business` is the
  company's own profile (filing, its most distinctive terms). The old fields stay, so an older web app
  still reads `sic_level` and the bands.
- **A bank** (035's rule) without `columns` gets bank columns: revenue, net interest income, efficiency
  ratio, provision for credit losses, loans, deposits, ROE, net margin (migration 048).

Measured on hermes' Item 1 texts of 2026-11-09 (run locally through this route; before = production's
SIC and size answer the same day; the root README has the full table):
VRT before LG Display, Impinj...; after Eaton and Johnson Controls (both named in Vertiv's 10-K),
AAON, nVent, Forgent Power, Super Micro, Ciena, Flex, Amphenol. DECK before Crocs, Nike, Silverton
Energy, Carlisle...; after Crocs, Columbia, Steve Madden, Under Armour, Nike, Kontoor, Wolverine.
NVDA before Micron, Tigo Energy, Broadcom, Intel...; after AMD, Ambarella, Qualcomm, Super Micro,
Broadcom, Micron, Marvell. JPM before Bank of America, Wells Fargo, Citigroup...; after PNC, Morgan
Stanley, Bank of America, U.S. Bancorp, Capital One, Huntington, Wells Fargo. Precision at 10 against
hand-written lists for nine companies: 47 of 90 (services/parser README).
Gaps: a 10-K whose Item 1 is a cross-reference (Intel, General Electric) or wasn't parsed
(Citigroup) has no profile, so it is never suggested by business (NVIDIA's list loses Intel, TI and
ADI; JPMorgan's loses Citigroup).

`GET /v1/companies/{id}/peers/check/{candidate}`: `require(financials)` on the comps' company, then
public filing-index metadata about the candidate: `ok`, `reason` (`null`; `metadata_only`: it has
figures your plan doesn't cover; `foreign_filer`: its annual reports are 20-F/40-F, IFRS, which
Tributary doesn't read, e.g. Birkenstock; `no_xbrl`: it files 10-Ks but no XBRL figures are here yet;
`no_annual_report`), `annual_form`, `annual_filed`. The web app asks before adding a peer and says why.

By SIC and size (`basis: "sic"`): companies with financials in the same 4-digit SIC, then the
3- and 2-digit groups; within a level, the same TTM revenue size band first (only for
companies you can read, and only if you can read the company's own revenue), then the same
filer category, revenue closeness and name. A revenue is read as the screener sorts it
(`gold.latest_reading`, migration 035): a TTM over 300 days behind the company's own latest period,
or not meaningful, gives no band and no closeness, for a peer or the company itself (a peer whose
revenue stopped in 2024 isn't "same size band"). Each peer has screener-style metadata, `values`
where permitted and `match` (`sic_level`, `same_filer_category`, `revenue_band`,
`same_revenue_band`). `access=financials` ranks only companies you can read (the web app asks
for that in a demo sandbox, which lists nothing else); the default ranks every company.

## Workspaces (saved work)

Workspaces are named folders of saved items in the app database (`workspace_api.py`,
`workspaces.py`, app migrations 004 and 009): a chart builder state (`chart`: title, range,
alignment, up to 12 layers: `metric`, `formula`, or `segment` (company, segment metric,
`segmentAxis`, 1-20 `members` as XBRL QNames, transforms q / ttm / yoy / contrib; the web app's
Revenue view saves these, and reads their values from `/segments`); a metric or formula layer may
say how a dimensionless value reads, `display`: percent, multiple or number), a comps set (`comps`: target, up to 20 peers, columns), a screener
screen (`screen`: filters and sort), a thesis (`thesis`) or a model (`model`; both below).
A workspace can also be an **analysis project** (app migration 012, the web app's Analysis
tab): its `project` is `{companyId, peers (up to 20), notes (up to 4,000 characters, "How it
makes money"), done (guided steps marked done: business, numbers, changes, peers, model,
thesis, monitor)}`, validated strictly (`ProjectState`: unknown fields, a peer twice or the
company among its peers refused); every other workspace has `project: null` and works as
before. `POST /v1/workspaces` takes `{name, project?}`; `PATCH /v1/workspaces/{id}` takes
`{name?, project?}` (a project for a plain workspace is a 422: folders stay folders). A project's
charts, models and thesis are ordinary items in it, so everything below applies unchanged;
duplicating a project copies its `project` too.
`GET/POST /v1/workspaces`, `PATCH/DELETE /v1/workspaces/{id}`,
`POST /v1/workspaces/{id}/duplicate`, `POST /v1/workspaces/{id}/items`,
`GET/PUT/DELETE /v1/workspace-items/{id}`, `POST /v1/workspace-items/{id}/open` (returns the
state and records it for Home), `POST /v1/workspace-items/{id}/duplicate` (same or another of
your workspaces), `GET /v1/recent` (recently opened items and recently read filings, newest
first) and `PUT /v1/recent/filings` (the reader records what you open; the newest 20 are kept).

- **Owner-only.** The user is the JWT `sub`; no route takes a user id (a `user_id` in a body is
  a 422). Another user's workspace or item and a missing one get the same 404. Ids are random
  UUIDs; anything else is a 422. The tables' row-level security is FORCEd, so even a statement
  that forgot its `WHERE user_id` sees only the transaction's `app.user_id`
  (`test_workspaces_db.py` proves it as the table owner).
- **Validated.** Each kind's state has a strict schema (unknown fields refused) mirroring the web
  app's state; names are 1-100 (workspace) / 1-120 (item) characters without control
  characters; a state is at most 16,000 bytes of compact UTF-8 JSON (a thesis 120,000, a model
  60,000: app migration 015); item
  create/save bodies over 160,000 bytes, other saved-work bodies over 32 KiB (or any without a
  Content-Length) are refused before they are read. Caps: 50 workspaces, 100 items per
  workspace, 500 items per user (409 beyond).
- **Grants nothing.** An item holds company ids and settings. Opening it reads no data: the web
  app reads through the permission-checked routes as usual, so a chart saved for a company you
  have since lost shows the same "no access" state as anywhere else.
- **Demo sandboxes** start with the workspace "PepsiCo vs Coca-Cola" (`sandbox.py`
  `sandbox_workspaces`: a margins chart for PEP vs KO, comps for PEP vs KO, KDP, MNST), written
  in the sandbox's creation transaction; it is deleted with the sandbox user (ON DELETE CASCADE).
- **Not streamed, no OpenFGA type yet.** Sharing (read-only by link, the roadmap's owner/viewer)
  is the next step: a `workspace` type in `authz/model.fga` (`owner`, `viewer`) with model tests,
  `workspaces` (+ a shares table) in Debezium's `table.include.list` with REPLICA IDENTITY FULL,
  the authz-sync mapping to tuples, the setup image redeploying the model, and these routes
  checking `viewer` through OpenFGA before reading (keeping the owner filter for writes).

## Theses and models

Two item kinds the user writes (app migration 009), through the same generic item routes as
charts (`POST /v1/workspaces/{id}/items`, `PUT /v1/workspace-items/{id}` to save over or
rename), owner-only and validated like every item:

- **`thesis`** (`ThesisState`): `{companyId?, markdown}`. The item's name is the thesis's title;
  the company is optional. Markdown up to 100,000 characters, newlines and tabs but no other
  control characters, the state at most 120,000 bytes (the table allows 128 KiB, as for
  reports). Lines `[[embed:<item id>]]` draw another item of the same workspace in the web app;
  they aren't checked here (an embedded item may be deleted later) and the web app drops any
  that don't resolve. `GET /v1/workspaces/{id}` leaves a thesis's state out (null, like a
  report's); `/v1/workspace-items/{id}` returns it. **Versions** (app migration 014; models too
  since F2): a save that changes a thesis's or a model's state keeps the version it replaced
  (`item_versions`, in the same transaction; the newest `MAX_VERSIONS` = 30 per item; a rename or
  a save that changes nothing keeps none). `GET /v1/workspace-items/{id}/versions` lists them
  newest first without their state (`version_id`, `name`, `saved_at`, `replaced_at`, `characters`
  of a thesis's text, a model's forecast `years` and whether it had bear and bull `cases`), `GET
  /v1/workspace-items/{id}/versions/{version_id}` returns one with its state; restoring is the web
  app loading it into the editor, and saving it is an ordinary save. Owner-only like the item
  (another user's is the same 404), forced RLS on `app.user_id`, deleted with the item.
- **`model`** (`ModelState`): `{companyId, historyYears (3-10, default 8), assumptions: [{growth,
  margin, note?}] (1-10)}`, growth -100..1000 and operating margin -1000..100, in percent; notes
  up to 500 characters (blank ones dropped). Optional `scenarios: {bear?, bull?}`, each a list
  of the same per-year assumptions for exactly as many years as `assumptions`, which is the
  **base** case (a model saved before scenarios is base only and validates unchanged); the
  thesis check reads the base case only. F2 adds, all optional so a model saved before
  validates and saves unchanged: per year `nonOperating` (interest and other, % of revenue,
  -100..100), `taxRate` (-100..100), `shareChange` (diluted shares, % a year, -50..100) and
  `fcfMargin` (-1000..100), absent meaning the default from history; `ytd: true` (year 1 is the
  current fiscal year's reported quarters plus the remainder, its growth and margin the
  remainder's); `adjustments` (up to 20 `{name (1-80), fy, quarter (1-4), kind: one_off |
  acquired, amount (USD)}`, the user's own one-offs and acquired revenue); `price` (`{value > 0,
  date}`, a price per share the user typed). Numbers in these are JSON numbers (a string or a
  `"yes"` is refused). Only what the user typed is stored: every run reads the history again
  ([Models](#models-one-copy-of-the-maths)), as the caller, so a model of a company you can't
  read draws nothing (403 as always).

Both grant nothing (a company id, the user's text and numbers). A thesis can be checked by the
assistant ([Thesis checks](#thesis-checks)). Reports never include them:
`ReportItem` still accepts only charts, comps and screens, and agent-api's report writer skips
them. Tests: `test_workspaces.py` (states, sizes, bodies, scenarios, F2's fields, model
versions, projects, another user's thesis, model or project is a 404 for every route),
`test_workspaces_db.py` (forced RLS as the table owner, the 128 KiB check for theses, 64 KiB for
models, a full model's versions, projects and their 16 KiB check).

## Models: one copy of the maths

`POST /v1/companies/{id}/model` (`model_api.py`) runs a model: the body is a model item's state
(`ModelState` above, strict; its `companyId` must be the path's), the answer everything the model
page shows. The maths exist only here, in `model_maths.py`, whose docstring is the spec: the web
app's model page and thesis embeds, agent-api's thesis check, and later MCP all call this route
(before F2 they were two hand-kept copies, the web's `models/model.ts` and agent-api's
`model_stats.py`). `require(financials)` comes first (the same 403 for any company you can't
read), then `Store.model_inputs` reads in one session under RLS: TTM rows (40 quarters) and
quarterly rows (12) of revenue, operating income, pretax income, income tax, net income,
operating cash flow, capex, free cash flow, buybacks and diluted weighted shares; the 10-K's EPS
and diluted shares (`gold.annual_per_share`, 036); the latest net debt and shares outstanding
with their readings (`gold.latest_reading`, 035). A run stores nothing; bodies over 128 KiB are
refused before they are read.

The answer: `history` (fiscal years: reported and adjusted operating income and margin, interest
and other, tax rate, net income, the 10-K's diluted EPS and shares, share change, operating cash
flow, capex, FCF and its margin, buybacks, each figure's filing; a diluted share count that moves
by more than half in a year is a split or a merger, not buybacks, so that year has no share change
or EPS growth and the years before it no P/E: Deckers' 6-for-1, NVIDIA's 4- and 10-for-1,
Vertiv's SPAC merger), `ttm` (the latest TTM when it
isn't a fiscal year end), `current` (the current fiscal year's reported quarters and the same
quarters a year earlier), `stats` and `run_rate` (B2 and B7's, unchanged), and per case (`bear`,
`base`, `bull`; bear and bull `preview`ed from the base case until the user adds them) the
`defaults` (what Reset sets, per year) and the projected `years` (revenue, growth, organic growth,
operating income reported and adjusted, margin, pretax and net income, shares, EPS and its
growth, FCF; which rates were `defaulted`; for a `ytd` year 1 its `actual` and `remainder`). The
base case also has the B2 `check` and `user_set` the thesis check reads. A model saved before F2
(no `ytd`) gets, per case, the `implied_remainder`: the remainder its full-year year 1 implies
after the reported quarters, which the web app offers to rebuild year 1 from without changing it. With a `price`,
`valuation`: market cap (price x latest shares outstanding), net debt with its date and reading,
EV (only when net debt is current or merely behind; `ev_note` says why not: `old`, `absent`, ...)
and P/E, EV/Revenue, EV/EBIT and FCF yield for every history year, the TTM and each case's years;
`"n/m"` over losses.

Measured on the real inputs in `tests/fixtures/model` (production answers of 2026-11-09):
Vertiv's FY2026 is Q1-Q3 reported ($10.24B, 39.3% over the same nine months) plus a Q4 whose
default grows Q4 FY2025's $2.88B at that 39.3%, at Q4 FY2025's margin moved by this year's
change so far (23.3%); Deckers' FY2027 is H1 reported plus H2, with diluted shares down 8.4% a
year (FY2027 Q2 against FY2026 Q2) as the default share change, and a $46M one-off in Q2 FY2027
taking the year-to-date margin from 21.8% to 19.9%; Deckers' latest net debt reading is from
2014 (`old`), so its EV multiples stay blank with that reason. B2 and B7's shared cases
(`fixtures/model/b2-b7-cases.json`) give the same statistics, flags, run rates, defaults and
`user_set` as before. Tests: `test_model_maths.py`, `test_model_api.py` (403 first and the same
for any company, strict states, body cap), `test_rls.py` (the Store read under RLS alone). The web
app's run fixtures are this route's answers: `fixtures/model/make_web_fixtures.py` regenerates
them.

## Reports

A report is a workspace item of kind `report` (app migration 005, `reports.py`): the research
note agent-api writes from a workspace's charts, comps and screens (services/agent README,
"Reports"). `GET /v1/workspaces/{id}` returns one workspace with its items' states (a report's
only from `/v1/workspace-items/{id}`); `POST /v1/workspaces/{id}/reports` (`{name, state}`)
and `PUT /v1/workspaces/{id}/reports/{item_id}` (`{state}`, regenerate) store one. Those two
are for agent-api only: nginx 404s them on the public site, and the generic item routes refuse
to create a report or write its state (renaming, duplicating, deleting and opening work like
any item).

- **Validated.** `ReportState` is strict: Markdown (at most 40,000 characters, no control
  characters) whose `[[embed:item-N]]` lines name exactly the report's items; up to 8 item
  snapshots (ref, item id, kind, name, state), each state validated against its kind like a
  saved one; citations (accession, form, filed, company, `https://www.sec.gov/` URL, section
  keys); the figure verification, whose `ok` is recomputed here; as-of, written-at, model,
  questions (1-10), stop. At most 120,000 bytes (the table allows 128 KiB for reports and theses,
  16 KiB for every other kind); report bodies may be 160,000 bytes, like item bodies (a thesis);
  the other saved-work routes keep 32 KiB.
- **Owner-only and grants nothing**, exactly like other items: the same filters, the same forced
  row-level security, the same 404 for someone else's. The web app draws each embedded chart from
  its snapshot, reading the data through the permission-checked routes as the viewer.

## Thesis checks

"Check my thesis" results (app migration 010, `thesis_checks.py`; services/agent README, "Thesis
checks"), one row per check in `thesis_checks`, kept with the thesis they checked.

- `POST /v1/thesis-checks/{item_id}` (`{result}`) stores one. For agent-api only: nginx 404s
  `/v1/thesis-checks` on the public site, like the report routes. The item must be the caller's
  thesis (anything else, someone else's included, is the same 404); the result must name it.
- `GET /v1/workspace-items/{item_id}/checks`: the thesis's checks, newest first, without
  results (check id, created, `text_hash`, model, cost, counts of supported / contradicted /
  unverifiable claims, flagged assumptions, points not mentioned, `ok`), plus what the web
  app's Revisit notice compares, read out of the stored result: the thesis's `company_id`,
  `checked_at` and `filings` (`[]` for checks stored before Revisit).
  `GET /v1/workspace-items/{item_id}/checks/{check_id}` returns one with its result.
- **Validated.** `CheckResult` is strict: a supported or contradicted claim and every "not
  mentioned" point cites at least one filing the agent's tools returned (`verified`); a
  supported claim has no figure that isn't in the data (none of its `figures` and no citation's
  `figure_status` is `unverified`: the agent downgrades such a claim to unverifiable); citation
  URLs only `https://www.sec.gov/`; accession numbers, section keys, the text hash and
  timestamps by pattern; numbers finite; at most 30 claims, 10 points, 4 models; the
  verification's `ok` recomputed. `filings` (Revisit, optional so older results still
  validate; `version` stays 1): at most 12 companies, once each, `latest` a 10-K or 10-Q,
  `evidence` one of the result's own verified citations of that company. At most 120,000 bytes (the table allows 128 KiB); bodies up to
  160,000 bytes. The newest 20 checks per thesis are kept.
- **Why a table and not the thesis's state:** the thesis state is written by the browser through
  the generic item routes, so a verdict stored there could be forged; a history of results
  wouldn't fit a thesis's 128 KiB; and an old check must stay readable after the text changes
  (its `text_hash` is what the web app's stale notice compares).
- **Owner-only and grants nothing,** like every saved item: statements filter on the JWT
  `sub`, row-level security is FORCEd on `app.user_id`, and `(item_id, user_id)` references
  the thesis, so a check can't name another owner and goes when the thesis is deleted. Tests:
  `test_thesis_checks.py` (routes, validation, nginx), `test_thesis_checks_db.py` (forced RLS
  as the table owner, the composite key, the cascade).

## Customizable views

Per-user preferences in the app database (migration 006, `views.py`): Home's card arrangement
(`home`: cards `status`, `recent`, `watchlist`, `filings`, `expected`, `notes`, each shown or
hidden, in order; a card left out shows after the listed ones) and the column sets of the
screener (`screener`, 1-12) and the watchlist (`watchlist`, 1-8), each a list of screener column
keys from `GET /v1/screener/columns`. `GET /v1/views` returns each view's saved state (null = the
default), the defaults and the options; `PUT /v1/views/{view}` with `{"state": ...}` saves;
`DELETE /v1/views/{view}` resets. Owner-only like workspaces (JWT `sub`, filtered statements,
FORCEd row-level security; `test_views_db.py`), validated against fixed catalogs, not streamed,
and grants nothing: the pages still read every value through the permission-checked routes, so a
column added for an ungranted company stays masked.

## Status page

`GET /v1/status/data` (`status.py`) backs the web app's public `#/status` page. No token: it
returns only aggregate counts and timestamps, never a company, ticker, accession number, value
or text. query_api can't read the bronze and ops tables behind it, so each part comes from a
SECURITY DEFINER function of migration 028 (search_path pinned, EXECUTE only for query_api), and
`status.shape` copies named fields only, so a function that returned more couldn't reach the
route. The result is cached per process for 60 s (`max_age_seconds`).

| Field | Source |
|---|---|
| `freshness` | `ops.feed_status()` (026): newest EDGAR acceptance and ingest time in `bronze.filings` |
| `ingest` | `ops.public_ingest()`: filings accepted in the last 7 days, how many the live feed caught (`live_pct`), and p50/p95 seconds from EDGAR acceptance to bronze for those (feed rows only, as `ops.filing_latency`) |
| `completeness` | `ops.public_completeness()`: the nightly completeness check (`ops.filing_completeness`, 027) over the last 30 days: days checked, accessions in EDGAR's daily indexes, missing at each day's latest check (then republished), rows republished. `days_checked` is 0 until the first run |
| `coverage` | `ops.public_coverage()`: companies with `gold.quarterly_financials` rows, `bronze.filing_documents` rows, search-indexed filings and their sections (`ops.section_index`) |
| `quality` | `ops.public_quality()`: `ops.quality_summary` (findings and companies per check, companies checked, last run) |
| `recorded` | `query_api/data/recorded_results.json`: the last recorded adversarial and citation runs and the golden 10-K figures, with the commit that last changed each; written by `make status-results` ([tests/status](../../tests/status)) |

```json
{"as_of": "2026-11-02T15:00:00+00:00", "max_age_seconds": 60,
 "freshness": {"last_accepted_at": "…", "last_ingested_at": "…"},
 "ingest": {"window_days": 7, "window_start": "…", "filings": 0, "via_feed": 0, "live_pct": null,
            "p50_seconds": null, "p95_seconds": null},
 "completeness": {"window_days": 30, "window_start": "2026-10-03", "days_checked": 0, "indexed": null,
                  "missing": null, "republished": null, "days_with_missing": 0, "last_checked_at": null},
 "coverage": {"companies_with_financials": 0, "documents": 0, "indexed_filings": 0, "sections": 0,
              "last_indexed_at": null},
 "quality": {"companies_checked": 0, "last_checked_at": null,
             "checks": [{"check": "impossible_value", "severity": "error", "findings": 0, "companies": 0}]},
 "recorded": {"adversarial": {"recorded_at": "2026-10-18T20:51:00Z", "attacks": 59, "leaks": 0, "…": "…"},
              "citations": {"…": "…"}, "golden": {"…": "…"}}}
```

## Run

```bash
make up-all                                       # in docker-compose, on http://localhost:8000
set -a; . ./.env; set +a; .venv/bin/query-api     # locally
make token USER=alice                             # query-api-token: dev JWT for a user
.venv/bin/query-api-account list                  # owner-created password accounts (below)
```

Config (env): `DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (all required; the secret
must be at least 32 bytes), `APP_DATABASE_URL` (the app database, for watchlists and workspaces;
unset makes those routes answer 503), `FGA_API_TOKEN` (OpenFGA preshared key, when enabled),
`DEMO_USERS` (comma-separated accounts allowed to sign in without a password; unset disables
the demo routes), the demo sandbox settings below, `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` /
`GITHUB_REDIRECT_URI` ([GitHub sign-in](#github-sign-in)), `PORT` (default 8000), `LOG_LEVEL`.
Password accounts need no setting: their route exists whenever `APP_DATABASE_URL` is set.

## Demo sandboxes

"Try the demo" (`sandbox.py`, `demo.py`, app migration 003) creates a temporary REAL user so a
visitor exercises the whole access path instead of sharing an account: one app-DB transaction
writes `users`, `subscriptions` (plan pro), `company_grants` for the 20 companies in
`sandbox.py` `DEMO_TICKERS` (the only list; resolved through `silver.resolve_company`, never
CIK = company; written only while the public catalog is closed: with it open the sandbox reads
every company through it, so `grant_demo_access` writes no grant), a watchlist (`DEMO_WATCHLIST`: PEP, KO, MSFT, NVDA, COST, JPM) and
`demo_sandboxes` (expiry). The Query API writes no tuples: Debezium → Kafka → authz-sync turns
those rows into OpenFGA tuples and `authz.user_access` like any user's, so the web app polls
`/v1/me` until all 20 companies are readable (with the catalog open there is nothing to wait
for: `/v1/me` says every company is readable). The response's token is an ordinary JWT, `sub` =
the new user (`demo-` + 16 hex), `exp` = the sandbox's expiry; nothing can extend it, and the
creation route takes no input, so it can't pick a plan, companies or lifetime.
`query-api-demo-cleanup` (Helm CronJob `demo-cleanup`, hourly) deletes expired sandbox users
(only users with a `demo_sandboxes` row); revocation then travels through CDC like any user
deletion, and their watchlist and usage rows cascade.

| Env | Default | |
|---|---|---|
| `DEMO_SANDBOX_ENABLED` | false | needs `APP_DATABASE_URL` |
| `DEMO_SANDBOX_TTL_HOURS` | 24 | 1-168 |
| `DEMO_SANDBOX_PER_IP_PER_HOUR` | 3 | creations per client address (`X-Client-IP` from nginx, stored as an HMAC keyed by the JWT secret) |
| `DEMO_SANDBOX_MAX_LIVE` | 200 | unexpired sandboxes at once; then "The demo is full right now" |
| `DEMO_AGENT_ENABLED` | true | false: the assistant refuses sandbox users (429 with a clear message) |
| `DEMO_AGENT_QUESTIONS_PER_USER_PER_DAY` | 10 | per sandbox per UTC day |
| `DEMO_AGENT_QUESTIONS_PER_DAY` | 200 | across all sandboxes per UTC day |

Creation runs under a transaction-level advisory lock so the caps can't be raced. In the Helm
chart these come from `demo.sandbox` in values.yaml (chart README, "Demo sandboxes").

## GitHub sign-in

"Sign in with GitHub" (`github.py`, app migration 007) gives a visitor a permanent account with
the same access as a demo sandbox. Off unless both `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`
(a GitHub OAuth app's) are set: then `/v1/auth/providers` says `{"github": true}` and the two
routes below exist; otherwise they 404. It needs `APP_DATABASE_URL`.

1. `GET /v1/auth/github/start` redirects to `https://github.com/login/oauth/authorize` with the
   client id, `GITHUB_REDIRECT_URI` (if set; else GitHub uses the app's registered callback),
   an empty `scope` and a `state` = `nonce.expiry.HMAC-SHA256` (key derived from
   `QUERY_API_JWT_SECRET`, 10 minutes). It sets the nonce in a cookie `tributary_gh_nonce`:
   HttpOnly, Secure, SameSite=Lax, `Path=/api/query/v1/auth/`, 10 minutes.
2. `GET /v1/auth/github/callback` checks the state's signature and expiry and that its nonce
   equals the cookie's (so a state only works in the browser that started it: no login CSRF,
   no replay elsewhere), and clears the cookie. It POSTs the code to
   `https://github.com/login/oauth/access_token` with the client secret (server-side), GETs
   `https://api.github.com/user` once with the access token, and discards the token. Then it
   finds or creates the user and mints the app's ordinary JWT (`sub` = `gh-<GitHub id>`, 7 days),
   and redirects to `/#/signed-in?token=…&expires_in=604800`. The token is in the URL fragment,
   which browsers never send to a server, so it isn't in any access log; the web app moves it
   to its session storage and clears the address bar. Every failure redirects to
   `/#/signed-in?error=<code>` with no detail: `state` (bad, expired or from another browser),
   `denied` (consent refused), `github` (GitHub refused the code or didn't answer),
   `unavailable` (app database down, or no demo company resolves).

A first sign-in writes, in ONE app-DB transaction, `users` (`user_id` `gh-<id>`, `email`
GitHub's no-reply form `<id>+<login>@users.noreply.github.com`), `github_accounts` (id, login,
first and last sign-in) and exactly what a sandbox gets (`sandbox.grant_demo_access`): plan pro,
`company_grants` on the 20 `DEMO_TICKERS` (only while the public catalog is closed), the
`DEMO_WATCHLIST` and the starter workspace. The
Query API writes no tuples: CDC → authz-sync grants the access, and the web app shows the same
propagation progress as for a sandbox. A returning sign-in changes no access and keeps the
user's workspaces and watchlist; only `github_accounts.login` and `last_sign_in_at` are
updated. The account never expires: there is no `demo_sandboxes` row, so `demo-cleanup`
never deletes it. Its assistant questions count against the same caps as a sandbox's
(`DEMO_AGENT_QUESTIONS_PER_USER_PER_DAY` per account, and the shared
`DEMO_AGENT_QUESTIONS_PER_DAY`), even with `DEMO_SANDBOX_ENABLED` off. `/v1/demo/token` never
mints a `gh-` user's token.

Privacy: no OAuth scopes are requested, so GitHub shares only the public profile; the API keeps
the numeric id and login, never an email address, name or the access token (stored nowhere,
never logged). The app's JWT is stateless: "Sign out" in the web app forgets it, and it expires
after 7 days.

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

The password is read with `getpass` (twice, must match) on a terminal, or as one line from
stdin when it isn't one; it is never an argument, never printed. Usernames match
`^[a-z0-9_.-]{3,32}$`; passwords are 12-256 characters. `create` writes, in ONE app-DB
transaction, `users` (`user_id` `pw-<username>`, email `<username>@password.invalid`),
`password_accounts` and exactly what a GitHub account gets (`sandbox.grant_demo_access`): plan
pro, the demo's watchlist and starter workspace and, only while the public catalog is closed,
grants on the 20 `DEMO_TICKERS` (with it open every signed-in user reads the catalog). Plain
app-DB rows: authz-sync grants the access through CDC; nothing writes OpenFGA tuples. It needs
`APP_DATABASE_URL` and, to resolve the demo's tickers, `DATABASE_URL` (both in the pod).

**Hashing.** stdlib `hashlib.scrypt` (no dependency), a random 16-byte salt per password,
n=2^15, r=8, p=1, 32-byte key, stored self-describing as
`scrypt$n=32768,r=8,p=1$<salt b64>$<key b64>` so the parameters can be raised later (a weaker
stored hash still verifies and is replaced at the next sign-in). Each call passes an explicit
`maxmem` (scrypt needs 128·r·n = 32 MiB here, OpenSSL's default limit) and verification reads
the parameters with bounds, so a bad row can't exhaust memory. Comparison is
`hmac.compare_digest`. At most 2 hashes run at once per replica (`SCRYPT_SLOTS`; the pod has
384 MiB); a sign-in that waits more than 10 s gets 503.

**Sign-in** (`POST /v1/auth/password`, public through nginx like the GitHub routes): the body
is parsed by hand (exactly `username` and `password`, strings, at most 4 KiB), so a validation
error never echoes the password (FastAPI's 422 would). Unknown username, wrong password,
disabled and locked all answer the same `401 {"detail": "Wrong username or password, or the
account is locked."}`, and each attempt runs exactly one scrypt (an unknown or malformed
username is checked against a dummy hash), so neither the body nor the timing says whether an
account exists. 10 consecutive failures lock the account for 15 minutes (the count starts
again after the lock; a success resets it). Each client address gets 20 attempts per 15
minutes (`X-Client-IP`, which nginx sets from Cloudflare's `CF-Connecting-IP`; in process, per
replica): then 429. Logs carry the user id of a known account and the refusal reason, never the
password, the token or a username that matched no account. Success mints the ordinary JWT
(`sub` = `pw-<username>`, 7 days).

**Sessions.** Every route's token check also refuses a `pw-` token when its account is
disabled, deleted, or the token was issued before `sessions_valid_after` (moved by
`set-password` and `disable`); the check is cached 10 s per user, so a disable takes effect
within 10 s. `/v1/demo/token` never mints a `pw-` user's token.

**Assistant.** `agent-quota` takes a password account's questions from its own
`questions_per_day` (0 turns its assistant off), counted in `demo_agent_usage` and never in, or
blocked by, the demo's shared `DEMO_AGENT_QUESTIONS_PER_DAY`: the owner chose that allowance,
and the shared cap exists to bound anonymous visitors, so a busy demo day must not stop a
tester. `DEMO_SANDBOX_ENABLED` and `DEMO_AGENT_ENABLED` don't apply either; agent-api's
hourly per-user and per-IP limits and its daily model budget (`AGENT_DAILY_BUDGET_USD`) still
do. Refusals read "Your account has used its N assistant questions for today…".

## Connect your data

Share prices can't be shown publicly: market-data vendors license them for personal use, not
redistribution. So each user connects their **own** market-data API key; the Query API fetches
end-of-day prices with that key, stores them for that user only and shows them only to that
user. There is no shared price cache across users (`connections.py`, `keybox.py`,
`providers.py`, `valuation.py`, app migration 011).

**Off unless `DATA_CONNECTIONS_KEY` is set.** Without it `GET /v1/connections` answers
`{"providers": [], "connections": []}` (the web app asks on every load; a 404 there was an error
line in every visitor's console), every other route below is absent (404), and the web app hides
the feature, so deploying the code changes nothing. A malformed value stops the API
at start (never a silent "off"). It needs `APP_DATABASE_URL`.

**Provider: Tiingo** (end-of-day daily prices, US equities). Chosen because its developer guide
explicitly covers this model: software that requires each user to supply their own Tiingo API
token, without contacting Tiingo, as long as the software does not redistribute the data and
Tiingo is clearly attributed as the source (every price view and chart footnote names it). Free
plan (as published by third parties; check Tiingo's pricing page): 50 requests/hour,
1,000/day, 500 symbols/month. Others considered:

| Provider | Why not first |
|---|---|
| Financial Modeling Prep | terms restrict a key to the individual's own use and prohibit "enabling" others' use of the services; a key used by a third-party server is a grey area |
| Massive (formerly Polygon.io) | individual plans are personal, non-business use; no statement on third-party software holding the key |
| Alpha Vantage | free tier 25 requests/day; adjusted daily series is premium |
| Finnhub | daily candles moved to paid plans |
| EODHD | free tier 20 calls/day |
| Yahoo | no official API |

**Verify before launch** (terms change; this was read on 2026-11-02 from tiingo.com's
documentation, the developer appendix and third-party summaries, not a lawyer's review):
(1) Tiingo's current Terms of Service still allow a user's own token in third-party
server-side software, with the attribution wording they require; (2) the free plan's limits
above; (3) `Authorization: Token <key>` header auth and the metadata endpoint used to validate
a key (`GET /tiingo/daily/SPY`); (4) re-record the test fixtures from the live API once
(`tests/fixtures/README.md`: today they follow the documented shape).

**Keys at rest** (`keybox.py`): envelope encryption with AES-256-GCM (Python `cryptography`).
`DATA_CONNECTIONS_KEY` = one or more `<key id>:<base64 of 32 random bytes>`, comma-separated,
newest first. Per save: a fresh random 256-bit data key encrypts the API key (12-byte random
nonce); the current master key wraps the data key (own nonce). Both use associated data
`"tributary data-connection v1\0" + user id + "\0" + provider`, so a ciphertext moved to another
user's row (or provider) fails to decrypt. Stored: `key_id`, `wrapped_key`, `nonce`,
`ciphertext`, `last4`. Rotation: put the new key first in `DATA_CONNECTIONS_KEY` and keep the
old one listed; new saves use the new key, old rows still open (`Keyring.rewrap` re-wraps a row
without touching the API key; a bulk rewrap job is not built yet). The key is decrypted only in
process, into a `Secret` (repr `Secret(***)`), straight into the provider request's
`Authorization` header: never into a URL (httpx logs request URLs), a log line, an exception
message or a response. The `PUT` body is parsed by hand (FastAPI's 422 would echo the input):
a key must match the provider's pattern (Tiingo: 20-64 letters/digits), bodies over 1 KiB are
refused with 413, then ONE provider call validates it; a refused key is not stored.

**Prices** (app DB `market_prices`, `market_price_fetches`): fetched on demand when the user
opens a company's Price tab, valuation, or a chart with a price layer. A (user, company) is
refreshed at most once per US Eastern day: the first fetch asks for 10 years, later ones from
10 days before the newest stored day (catches the provider's corrections). The ticker is the
company's current SEC ticker (alphabetically first if several). Per-user limits, in process
(per replica): 30 provider calls and 5 key saves per hour; over the limit, or when the provider
fails, the stored prices are served with `stale: true` and a `warning`. A key the provider now
refuses answers 409 ("reconnect"). Disconnecting deletes the key and (FK `ON DELETE CASCADE`)
every price and fetch row; deleting the user deletes everything.

**Two layers** (like every private thing here): layer 1, `require(financials)` on the company
(OpenFGA) before anything else, and the route reads only the JWT user's own connection; layer 2,
the three tables' FORCEd owner-only row-level security on `app.user_id` (every statement also
filters on it). Why the app DB and not the tributary DB: prices are per-user private data, like
saved work, so they live next to `users` with the same owner-only policy, cascade with the
user and the connection, and never mix with the shared, catalog-wide analytics tables (whose
RLS is per company, not per user). The valuation joins them in Python: gold through `Store`
(as `query_api`, RLS), prices through `AppConnections` (owner RLS).

**Valuation** (`valuation.py`; latest close × latest gold figures, each with its own date):

| Figure | Formula |
|---|---|
| Market cap | close × `shares_outstanding` (latest reported) |
| P/E | market cap / TTM `net_income` (net income ≤ 0: not meaningful) |
| P/S | market cap / TTM `revenue` |
| EV | market cap + `net_debt` (long-term debt incl. current − cash − short-term investments) |
| EV/EBIT | EV / TTM `operating_income` (≤ 0 or no net debt: not shown) |

P/E is market cap over TTM net income, not price over TTM diluted EPS: gold has no TTM EPS
(fiscal Q4 EPS is never reported or derived, migration 014); the two differ by the diluted vs
outstanding share count and preferred dividends. The close used is the newest within 14 days.
A company with several share classes (several current tickers) prices one class against the
total share count; the response lists them in `share_classes`.

| Env | |
|---|---|
| `DATA_CONNECTIONS_KEY` | master key(s), `k1:<base64 32 bytes>[,k0:…]`; unset = feature off |

Outbound: the provider adapters' hosts only (today `api.tiingo.com`) over HTTPS (Helm
`uses: market-data`). Metric: `tributary_market_data_calls_total{provider,kind,result}`.
The agent has no price tool (deferred: its citation contract has no shape for licensed data).

## Tests

`pytest services/query-api` (install with `pip install -e 'services/query-api[test]'`).
`test_api.py` and `test_formula_api.py` use a fake authorizer and store; `test_formula.py`
covers the formula grammar, checks and compiled SQL; `test_formula_db.py` evaluates formulas
over real gold rows (PepsiCo, Coca-Cola, Apple) with both permission layers (needs
`TEST_DATABASE_URL`); `test_screener.py` and `test_ui_api.py` cover the UI routes with fakes
(including stores that return values for every company, to prove layer 1 nulls them);
`test_github.py` covers GitHub sign-in with a fake GitHub (state tampering, expiry, the
cookie nonce, error redirects, the fragment token, the caps) and `HttpGitHub` over
GitHub-shaped responses (never GitHub itself); `test_github_db.py` runs it on the real app
schema (`APP_TEST_DATABASE_URL`); `test_passwords.py` covers password accounts with fakes
(scrypt round trip and parameter encoding, bounds, one scrypt per attempt and one 401 for
every refusal, lockout and reset, the per-address limit, bodies never echoed, nothing secret in
any log record, disable and set-password ending sessions, the per-account assistant cap, the
CLI's password prompt) and `test_password_accounts_db.py` on the real app schema (`create` in
one transaction, the owner commands, lockout rows, the account's own cap beside a used-up
demo day; `APP_TEST_DATABASE_URL`);
`test_ui_db.py` runs expected dates, recaps, flags and peers on real Postgres (and the status
functions of 026 and 028 as query_api, which still can't read the tables behind them) and
`test_watchlist_db.py` the app-database watchlist (`APP_TEST_DATABASE_URL`); `test_rls.py` wires the API to an authorizer that
allows everything and checks that row-level security alone still hides ungranted rows, and that
a public catalog is readable only with a user id, within the user's tiers, while private grants
stay per user (needs `TEST_DATABASE_URL`); `test_authorizer.py` checks which OpenFGA calls
`access()` makes (never ListObjects over a public catalog). Connect your data:
`test_keybox.py` (round trip, a ciphertext moved to another user or tampered fails, rotation,
nothing prints key material), `test_connections.py` (routes absent without the master key, the
key never in a response or any log record, forged and oversized input refused before any
provider call, another user can't see my connection or prices, layer 1 before the provider,
once-a-day refresh, rate limits, disconnect deletes the prices, valuation formulas; the real
Tiingo adapter over `httpx.MockTransport` with `tests/fixtures/`, never Tiingo itself) and
`test_connections_db.py` (the three tables' forced owner RLS for another user and for no user,
WITH CHECK, cascades, checks; `APP_TEST_DATABASE_URL`).

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

`query_api/notes.py` (migration 023). Findings are about values, so they are not public
metadata: the company route calls `require(financials)`; `/v1/data-notes` takes the companies
from `readable()` (financials) and passes them into the SQL (with the catalog public, no list:
`Readable.ids`); RLS (`granted_financials` on `ops.quality_findings`, `ops.quality_companies`,
`gold.quarterly_financials`) filters again.

- Findings from `ops.check_financials` / `ops.check_segments` (`kind` = check name: `series_gap`,
  `overlapping_quarters`, `calendar_collision`, `annual_sum`, `impossible_value`, `sign_flip`,
  `outlier`, `segment_sum_*`, `restatement`, `mistagged_fact`, `net_debt_gap`), with `severity`
  error / warning / info and `detail` as the check wrote it (dates, filings, relative sizes; never
  values). Migration 041: `mistagged_fact` is a fact gold leaves out (`rule`
  `period_after_filing`, with `filed` and `periods`; `debt_above_liabilities`); `net_debt_gap` is
  the latest balance sheet with debt and cash but no net debt (`investments_end` when an investments
  series stopped, else debt that can't carry its interest); a `series_gap` with `repeated` is the same
  quarter missing that many years.
- `derived_quarters` (company route only): per metric, how many quarters are derived from
  year-to-date differences, the period range and the latest one's filing; `computed: true` when
  they're gross profit as revenue less cost of revenue (migration 039), not a year-to-date difference.
- `companyfacts_behind`: 10-K/10-Q filings in the public index (`silver.filings`), filed on or
  after the latest filing in the company's gold rows and within the last 120 days
  (`store.BEHIND_DAYS`; companyfacts lags by days, not months), that no XBRL fact cites yet.
  The companies' CIKs are looked up first and `silver.filings` is filtered on that array.
- `stock_split`: what a split's restatements read as, one note per split (`detail`: `split`
  "10-for-1", `reverse`, `findings`, `periods`, `metrics`, first and last period). Needs a share count
  restated by a clean ratio and EPS by its reciprocal in the same period and filing. Without the other
  side a finding stays a `restatement`, with `may_be_split`; a factor of 1,000 or 1,000,000 gets
  `unit_change`. An `outlier` the same quarter also has in two other years gets `seasonal` and
  severity info. No check is suppressed: every finding still reaches a note.
- Every note has `date` (restating or missing filing, a mistagged fact's filing, else the period
  end; newest first: a 2033 period mustn't top the list) and
  `filings` (`role`, accession, form, filed, EDGAR `filing_url` from the public index). The
  plain-language wording is the web app's (`services/web/src/company/notes.ts`).

## Earnings and events

`/v1/companies/{id}/events` and `/v1/companies/{id}/earnings` (`events.py`, `releases.py`; rules
in their docstrings) put the filings an analyst reads between 10-Qs next to the figures: what the
company filed under Form 8-K since its last report, and each quarter's earnings release. Both
`require(filings)` first, then read through `Store` as `query_api`: the 8-K list and its items
are filing-index metadata (`silver.company_8ks`, migration 045, from the SEC's submissions
history; a filing too new for the nightly load shows the items the parser found in it, under
RLS), the excerpts and passages are section text under RLS on `silver.filing_sections`.

- **Events since the last report.** `since` is the period end of the latest original 10-K or
  10-Q (the last 400 days, `window: "recent"`, when there is none that recent); `events` are the
  8-K and 8-K/A filed after it, at most 25, each with `items` (`{item, title, kind}`; `kind`:
  deal, debt, agreement, leadership, impairment, non_reliance, earnings, disclosure, ...),
  `items_from` (`index` or `parsed`), `material` (false when every item is routine: a vote,
  nominations, exhibits alone), `event_date` (the 8-K's date of report), `excerpt` (the opening of
  its most material item's section after the item's heading, cited with offsets; an item that only
  says another is incorporated by reference gives way to the next), `release` (its first EX-99 exhibit) and
  `affects`. **Stale figures**: an original 8-K whose event is dated after the period end flags
  the balance-sheet figures it can change, listed once each in `stale` as of that period end with
  the events behind them: 2.01 completed acquisition or disposition (cash, debt, net debt, total
  assets), 2.03 new debt and 2.04 accelerated debt (debt, net debt, debt to equity; 2.03 cash
  too), 2.06 impairment (total assets, equity), 3.02 unregistered equity sale (shares, equity),
  1.03 bankruptcy and 4.02 non-reliance (`*`: every figure). A flag, never a recomputed figure.
- **Announced under 7.01 / 8.01** (F1b, `announcements.py`, rules in its docstring): many
  companies announce a closed acquisition or a new financing in a Regulation FD or Other Events
  8-K with a press release, without Item 2.01 or 2.03. Such an 8-K (7.01 or 8.01, never one with
  2.02: an earnings release recaps its quarter) is read at the opening of its press release and
  item text, sentence by sentence, by fixed phrases: a **completed** acquisition or disposition
  ("Completes Acquisition", "has acquired", "closed the sale of", not "agreement to acquire",
  "expected to close", "upon completion") flags 2.01's figures; a **new financing** (notes priced
  or issued, a credit facility or term loan entered, a term loan drawn; not a proposed or launched
  offering, an amendment, a buyback, a dividend or guidance) flags 2.03's. `announced` on the
  event says what was read: `kind` (deal, financing), `item` (7.01 or 8.01), `words` (the phrase
  that matched, as written), `sentence` and a `citation` with offsets into the section it came
  from. Checked on real 8-Ks (`tests/fixtures/announcements`, from production): Vertiv's Oct 26,
  2026 7.01 8-K ("Vertiv Completes Acquisition of King Environmental Services") flags; its Sep 24
  agreement to acquire the same company, its UIG agreement and plan of merger, its Oct 28 earnings
  release, United Therapeutics' $2B buyback, a dividend, BWX's launched notes offering and National
  Fuel's planned separation don't; Blackstone's, Las Vegas Sands' and TKO's priced notes do. Of
  50 more 8-Ks sampled from production's search (7.01 and 8.01 sections, Oct 1 - Nov 6, 2026), 38
  are read (7.01 or 8.01, no 2.02) and 2 flag, both rightly (Peoples Bancorp "Completes
  Acquisition of Citizens National", TKO's priced notes). Reading the other 36's completion,
  pricing and credit sentences by hand, none announces a completed deal or a new financing the
  rules missed, except a "pricing of the Offering" whose 8-K never says what was offered (two
  more completions there are 2.01 8-Ks, flagged by their item).
- **Earnings releases.** A quarter's release is the 8-K with Item 2.02 filed after its end (within
  120 days, the latest quarter end before the filing), the last one on or before the quarter's
  10-Q/10-K when there are several (`other_releases` lists the rest); a quarter whose 10-Q isn't
  filed yet is `estimated` (gold.expected_filings' next period end). Its text is the first EX-99
  exhibit, else the 8-K's `2.02` section. From it, by fixed rules (no model): `headline`,
  `outlook` (the blocks under the release's own Outlook or Guidance headings) and `adjustments`
  (reconciliation tables, and footnotes before the statements that state a dollar amount). Each
  passage has `heading`, `text` (lines as the parser wrote them), `truncated` and a `citation`
  with offsets into the release. A release without such a heading or table gets none: the web app
  and the agent say so. No beat or miss: Tributary holds no estimates.
- **Measured** (hermes, 2026-11-08, operating companies, filings since 2024-11-01): 32,188 8-Ks
  with Item 2.02 from 3,994 companies, every one stored, 30,578 (95%) with a parsed EX-99.1;
  of 40,588 10-Q/10-K quarters, 30,284 (75%) have a 2.02 8-K between the period end and the
  report (+7 days) and 28,866 (71%) a parsed EX-99. On 45 releases sampled across industries (one
  per two-digit SIC, Oct 15 - Nov 6, 2026), 33 had an Outlook or Guidance heading and the rules
  found 32 (the miss: a release whose text breaks every printed line); every block found was the
  release's own outlook section, some opening with its disclaimer. 8-K items of operating companies: 111,113 8-Ks, 76,711 stored;
  material event items are stored only when the document crawl took them (`CRAWL_8K_ITEMS`,
  doc-fetcher README): an event whose 8-K isn't stored is still listed, from the index, with
  its EDGAR link and no excerpt.
- Per company, bounded: one company's 8-Ks (an index probe per CIK, 1.4 ms for NVIDIA's two
  years on hermes), the sections of at most 25 events or 8 releases. No catalog-wide form.

## What changed

`GET /v1/companies/{id}/filings/{accession}/changes`: for a 10-K (Risk Factors `1A`, MD&A `7`)
or 10-Q (`II-1A`, `I-2`), the paragraphs that are new, removed or rewritten since the
comparable earlier filing, from `gold.section_diffs` / `gold.section_changes` (migration 021,
built by the parser's `parser-index`; algorithm in `services/parser/src/filing_parser/diff.py`).
`require(filings)` first, then `Store` as `query_api` under the `granted_filings` policies.

- `basis=prior` (default): the previous 10-K / 10-Q; `basis=year_ago` (10-Q): the 10-Q for the
  same quarter a year earlier. `section` (default all compared sections), `kind`
  (`new`, `removed`, `rewritten`).
- Per section: `compared_to` (the earlier filing), `summary` counts (`unchanged` includes
  `minor_edits`; `numbers_only` = unchanged once numbers and years are masked), `changes` in
  reading order, `stale` and `freshness` (filed, ingested_at, computed_at).
- Per change: `kind`, `similarity` (rewritten), `text` / `prior_text`, `words` (rewritten:
  `[op, text]` runs, `=` both, `-` prior only, `+` this filing only), `citation` /
  `prior_citation` with character offsets into each filing's document text.
- Gold stores offsets and word-diff opcodes, not text: paragraphs are cut from the section text
  the diff was computed on (matched by parser and parsed_at). A section re-parsed since then is
  returned with `stale: true` and no changes until the job recomputes it.
- An accession that isn't a parsed filing of this company → 404 (the same for another company's
  filing); a parsed filing with nothing compared yet → 200 with `sections: []` and a `note`.

### Read for materiality (F3: `view`, `q`, `numbers`)

The stored diff counts every paragraph a page break split, every "second quarter" that became
"third", every table row whose figures moved: Deckers' Q2 FY27 10-Q had 121 changes against its
Q1 10-Q, half of them fragments, and its three-month text was diffed against six-month text
(analyst tests, audit H4). With `view` the route runs a second pass over the stored changes at
query time (`changes.py`, rules in its docstring), from the two section texts it already reads:
no stored row changes and nothing is rebuilt.

- `view=reading` (reading order) or `view=ranked` (highest score first); `q` or `numbers` alone
  imply `reading`. Without any of the three the answer is exactly as above (the agent's
  `get_section_changes` and an older web app read that).
- `basis` defaults, with `view`, to the same quarter a year earlier for a 10-Q that has one
  (else the prior filing); `comparisons` lists the filings this one was compared with
  (`basis`, accession, form, filed, period), from `Store.section_bases` (one PK probe of
  `gold.section_diffs` under RLS, 1.6 ms on hermes).
- The pass: page furniture dropped ("Table of Contents 25", running headers with their page
  number masked, never a table row); paragraphs a page break or a line-wrapped document split
  joined; then hidden and counted per section in `material.hidden`: `resplit` (only split or
  rejoined, or only furniture differed), `calendar` (equal once month names, "second/third
  quarter" and the quarter's three months vs the year to date's six or nine months are masked),
  `figures_only` (a table row whose words didn't change) and `minor` (similarity >= 0.9, the
  stored diff's threshold). Pairs are re-made over what's left: never a quarter's passage with a
  year-to-date one, a split-and-edited paragraph takes in its neighbours, and two passages with
  the same run-in heading ("Gross Profit.") and period pair even when rewritten past
  recognition.
- Per change, besides the stored fields: `period` (10-Q: "three months", "six months", "nine
  months", "three and six months"; from the passage, else its period heading), `figures`
  (`new`: figures the compared passage doesn't state, `removed`), `moved` (the most significant
  replaced figure that moved 10% or more: a dollar amount first, the largest first), `risk_terms` (risk wording new to the passage), `subsection`,
  `score`, `why` (what counted, in words: "new passage", "5 new figures", "0.2% → 4.8%", "new
  risk wording: tariff", "Liquidity and capital resources") and `merged` (stored paragraphs
  behind it). Score = where it sits (Risk Factors 1.5; MD&A liquidity 1.3, results 1.0, critical
  accounting 0.8, non-GAAP 0.6, boilerplate 0.3) x (new 2, removed 1.5, rewritten 1-3 by how
  much; + new and removed figures; + how far figures moved; + new risk wording), halved under
  12 words. `ranking` is the one line that says so. In a rewritten passage's `words`, a
  calendar-only swap ("second" → "third" quarter, "June 30, 2025" → "September 30, 2026") is shown
  as the new words, unmarked; every other word change is marked.
- Filters, after the pass: `q` (2-100 characters; every word must appear in the passage or its
  compared one, as a word prefix with a plural dropped, so "tariffs" finds "tariff"; "quoted
  phrases" as written), `numbers=new` (passages with a new figure), `kind`, `section`. The answer
  echoes `view` and `filters` (`terms` is what was matched), and counts `matched` (shown) and
  `total` (after the pass, before filters). A web app that gets no `view` back is talking to an
  older API.
- Measured on production's texts (the stored rows reproduced exactly from them with the parser's
  diff), changes shown after the pass, before filters: Deckers 10-Q Q2 FY27 against Q2 FY26
  147 → 91 (against Q1: 121 → 80, of which 51 six-month passages new in Q2, now labelled);
  Vertiv 10-Q Q3 2026 against the prior 10-Q 39 → 22 (9 calendar rollovers hidden), against Q3
  2025 62 → 54; NVIDIA 10-K FY26 133 → 122, 10-Q Q2 FY27 against a year ago 129 → 119; Vertiv
  10-K 2025 112 → 93; Deckers 10-K FY26 379 → 264. With `q=tariffs` Deckers' 10-Q shows 10
  passages, the $46.0M IEEPA refund second. The pass costs 20-70 ms per 10-Q section and up to
  145 ms for Deckers' 10-K Risk Factors (178 stored changes), in the request.
- Per company, per filing: the same three per-accession reads as without a view plus
  `section_bases`; no catalog ids, no `@@` (the topic is matched in Python on the passages the
  caller already read under RLS).

## Search

`GET /v1/search`: Postgres full-text search over `gold.section_search` (one row per
~2,000-character passage of every parsed section, `tsvector` with a GIN index; migration 021).
With `company`, `require(filings)` on it; without, the companies `readable()` lists with the
filings tier (no OpenFGA list entry → no query), or with the catalog public at that tier every
company the read rule allows. The first stage is `gold.search_passages` (migration 032), which
applies `authz.readable_companies('filings')` itself: `@@` isn't leakproof, so under RLS on
`gold.section_search` the planner can't use the GIN index at all. It returns offsets and ranks,
no text; the snippet's text is still read through RLS on `silver.filing_sections`.

- `q`: `websearch_to_tsquery('english', q)`: words, `"a phrase"`, `or`, `-excluded`.
  Filters: `company`, `form` (`10-K`, `10-Q`, `8-K`, `/A` amendments), `section`, `from` / `to`
  (filing date). `limit` 1-50 (default 10), `offset` up to 1000, `has_more`.
- One result per section: its best passage by `ts_rank_cd`, then newest filing first;
  `passages_matched` counts the section's matching passages. `snippet` is `ts_headline` over that
  passage only (matches between « and »), cut from `silver.filing_sections` at query time.
  `citation` has the passage's character offsets into the document text.
- Ranking runs over every match of the companies searched. With a company or a closed catalog
  that is all time. With the catalog public (no `company`) ranking every match of a common phrase
  can't finish in time, so the route ranks the newest window that fills the page: 30 days back
  from `to` (or today), then 180, 730, then all time (a phrase with few recent matches has few
  matches), never before `from`. `searched_from` in the response is that lower bound (`null`:
  all time or `from`). A consequence: a page past the end of one window re-ranks a wider one, so
  deep paging can repeat a result. Queries still stop after 8 s (`SEARCH_TIMEOUT`) with 422
  "search too broad".
