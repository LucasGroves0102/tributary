# query-api

FR8, week 2. HTTP API over the gold tables, read-only except for the caller's own watchlist
(app database); the [agent](../agent) and the [web](../web) app reach data only through it.
Every data route is permission-checked twice:

1. **OpenFGA** (`authorizer.py`): `can_read_financials` / `can_read_filings` on the company,
   before any query. Denied → 403 (same response whether the company exists or not).
   OpenFGA unreachable → 503: fail closed.
2. **Postgres row-level security** (`store.py`): queries run as the `query_api` role with
   `app.user_id` set for the transaction; policies return only rows in `authz.user_access`.
   This holds even if a route forgot layer 1.

The caller is identified by an HS256 JWT (`sub` = app user_id). The user is never read from
request parameters. Dev tokens: `make token USER=alice`.

| Route | Needs | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok"}` |
| `GET /v1/demo/users` | `DEMO_USERS` set | demo accounts (404 when disabled) |
| `POST /v1/demo/token` | `DEMO_USERS` set | an 8-hour token for a demo account |
| `GET /v1/me` | token | companies the caller can read, with tiers |
| `GET /v1/companies/search?q=` | token | candidates (public identity) + caller's access |
| `GET /v1/metrics` | | metric names served by `financials`: `silver.metrics` and `gold.derived_metrics`, read once |
| `GET /v1/companies/{id}/financials?metrics=&quarters=&view=&basis=&format=` | financials | values + citation + freshness; `format=csv`: one row per value with its source filing |
| `GET /v1/companies/{id}/segments?axis=&metric=&quarters=&view=&basis=&format=` | financials | values per segment member (business segment, geography, product/service, reconciliation), exact decimal strings, citation per value ([Segments](#segments)) |
| `GET /v1/companies/{id}/filings?limit=` | filings | recent filings and their sections |
| `GET /v1/filings/latest?limit=` | token | newest parsed filings across the companies the caller can read filings for |
| `GET /v1/companies/{id}/sections/{key}?form=&accession_no=&limit=&max_chars=` | filings | section text + citation |
| `GET /v1/companies/{id}/filings/{accession}/changes?section=&basis=&kind=` | filings | new / removed / rewritten Risk Factors and MD&A paragraphs vs the comparable earlier filing, word diffs, citations in both filings ([What changed](#what-changed)) |
| `GET /v1/search?q=&company=&form=&section=&from=&to=&limit=&offset=` | filings (every company searched) | best passage per section, ranked, with snippet and citation ([Search](#search)) |
| `POST /v1/formulas/validate` | token | AST, unit and inputs, or errors with positions |
| `POST /v1/formulas/evaluate` | financials, every company in the formula | series; each point cites every input's filing |
| `GET /v1/screener?columns=&where=&...&format=` | token | companies with financials: public metadata + latest values where permitted ([Screener](#screener)) |
| `GET /v1/screener/columns` | | column keys, filer categories, size bands |
| `GET /v1/watchlist?columns=&sort=&format=` | token, app DB | the caller's watchlist: metadata, last report, next expected filing, values where permitted |
| `PUT` / `DELETE /v1/watchlist/{id}` | token, app DB | add / remove a company (grants nothing) |
| `GET /v1/calendar/expected-filings?days=&scope=&format=` | token | expected 10-Q/10-K filings in the next N days (watchlist or readable companies) |
| `GET /v1/companies/{id}/expected-filings?periods=` | token | estimated next 10-Q/10-K dates with their basis |
| `GET /v1/filings/{accession}/recap?metrics=` | financials (flags: filings) | metrics vs prior quarter and year-ago, cited; one-time-item flags |
| `GET /v1/companies/{id}/peers?limit=&columns=` | token | suggested peers (SIC, size band, filer category), values where permitted |

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
- **Checks** (`check.py`): unknown metrics (with "did you mean"), metrics without values in
  the requested view (`ttm(eps_diluted)`), and units: + and - need the same unit (a constant
  takes the other side's), * and / combine them (USD / shares = USD/shares, USD / USD = ratio).
- **Tickers** resolve through `silver.resolve_company` (best ticker match). A ticker that
  matches nothing and one the caller can't read give the same error
  ("KO isn't one of your companies"); suggestions come only from the caller's own companies.
- **SQL** (`compile.py`): one parameterized query over `gold.financials`; the SQL text holds
  no user input. Each input row gets a period key: with one company and basis `fiscal`, its
  fiscal quarter; otherwise (basis `calendar`, or several companies) the calendar quarter of
  migration 014's rule, using `calendar_primary` rows. A lag of n moves an input's rows n keys
  later. A point exists where the expression is not NULL: a missing input or a division by zero
  is a gap. `view` picks quarter or TTM inputs; `ttm(...)` forces TTM inside it.
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
SQL for these routes is in `db/migrations/017_ui_data.sql`.

## Screener

`GET /v1/screener`: every company with gold financials (`access=financials`: only companies you
can read). Columns are `<metric>_ttm` (trailing twelve months; balance-sheet metrics at the
quarter end) or `<metric>_q` (latest fiscal quarter) for every metric in the formula catalog,
each the latest value with its own `sources[key]` (`period_end`, `accession_no`, `form`,
`filed`). Filters: `sic` (4-digit, repeatable), `sic_prefix` (1-3 digits), `industry` (SIC
description contains), `q` (name contains or exact ticker), `exchange`,
`fiscal_year_end_month`, `company_id` (repeatable, at most 21: only these companies, including
ones without gold financials, for a comps table or a tearsheet), `filer_category` (`large_accelerated`, `accelerated`,
`non_accelerated`), `revenue_band` / `assets_band` (`lt100m`, `100m-1b`, `1b-10b`, `10b-100b`,
`gte100b` on `revenue_ttm` / `total_assets_q`) and `where=<column>:<gt|gte|lt|lte>:<number>`
(ratios as fractions: `operating_margin_ttm:gte:0.2`). `sort` is a column key or `name`, `sic`,
`fiscal_year_end`, `filer_category`, `state_of_incorporation`, `-` for descending; nulls last.
`limit` (500 as JSON, 5000 as CSV), `offset`, `format=json|csv`. JSON:
`{as_of, total, limit, offset, sort, columns: [{key, metric, span, unit}], rows: [{company_id,
name, tickers, exchanges, sic, industry, fiscal_year_end, filer_category, filer_categories,
state_of_incorporation, access: [tiers], values: {key: value}, sources: {key: {...}}}]}`.

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
without it `flags` is null.

## Peers

`GET /v1/companies/{id}/peers`: companies with financials in the same 4-digit SIC, then the
3- and 2-digit groups; within a level, the same TTM revenue size band first (only for
companies you can read, and only if you can read the company's own revenue), then the same
filer category, revenue closeness and name. Each peer has screener-style metadata, `values`
where permitted and `match` (`sic_level`, `same_filer_category`, `revenue_band`,
`same_revenue_band`).

## Run

```bash
make up-all                                       # in docker-compose, on http://localhost:8000
set -a; . ./.env; set +a; .venv/bin/query-api     # locally
make token USER=alice                             # query-api-token: dev JWT for a user
```

Config (env): `DATABASE_URL`, `FGA_API_URL`, `QUERY_API_JWT_SECRET` (all required; the secret
must be at least 32 bytes), `APP_DATABASE_URL` (the app database, for watchlists; unset makes
the watchlist routes answer 503), `FGA_API_TOKEN` (OpenFGA preshared key, when enabled),
`DEMO_USERS` (comma-separated accounts allowed to sign in without a password; unset disables
the demo routes), `PORT` (default 8000), `LOG_LEVEL`.

## Tests

`pytest services/query-api` (install with `pip install -e 'services/query-api[test]'`).
`test_api.py` and `test_formula_api.py` use a fake authorizer and store; `test_formula.py`
covers the formula grammar, checks and compiled SQL; `test_formula_db.py` evaluates formulas
over real gold rows (PepsiCo, Coca-Cola, Apple) with both permission layers (needs
`TEST_DATABASE_URL`); `test_screener.py` and `test_ui_api.py` cover the UI routes with fakes
(including stores that return values for every company, to prove layer 1 nulls them);
`test_ui_db.py` runs expected dates, recaps, flags and peers on real Postgres and
`test_watchlist_db.py` the app-database watchlist (`APP_TEST_DATABASE_URL`); `test_rls.py` wires the API to an authorizer that
allows everything and checks that row-level security alone still hides ungranted rows (needs
`TEST_DATABASE_URL`).

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

## Search

`GET /v1/search`: Postgres full-text search over `gold.section_search` (one row per
~2,000-character passage of every parsed section, `tsvector` with a GIN index; migration 021).
With `company`, `require(filings)` on it; without, the companies `readable()` lists with the
filings tier (no OpenFGA list entry → no query). Either way the query runs through `Store` and
RLS (`granted_filings`) filters again.

- `q`: `websearch_to_tsquery('english', q)`: words, `"a phrase"`, `or`, `-excluded`.
  Filters: `company`, `form` (`10-K`, `10-Q`, `8-K`, `/A` amendments), `section`, `from` / `to`
  (filing date). `limit` 1-50 (default 10), `offset` up to 1000, `has_more`.
- One result per section: its best passage by `ts_rank_cd`, then newest filing first;
  `passages_matched` counts the section's matching passages. `snippet` is `ts_headline` over that
  passage only (matches between « and »), cut from `silver.filing_sections` at query time.
  `citation` has the passage's character offsets into the document text.
- Ranking runs over every match, so a very common word across thousands of companies can be
  slow: queries stop after 8 s (`SEARCH_TIMEOUT`) with 422 "search too broad".
