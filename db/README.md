# db

All SQL for Tributary. Two Postgres databases on one server:

| Database | Migrations | Contents |
|---|---|---|
| `tributary` | [`migrations/`](migrations) | the bronze / silver / gold medallion, the access tables, ops tables and views |
| `app` | [`app/migrations/`](app) | users, organizations, plans, grants, watchlists, saved work, filing alerts, watched screens ([app/README.md](app/README.md)) |

OpenFGA keeps its own `openfga` database, migrated by OpenFGA itself.

## Schemas in `tributary`

| Schema | Holds | Written by |
|---|---|---|
| `bronze` | data as received: `filings` (filings.raw sink), `submissions` / `submission_filings` (every filer's filing history and 8-K items), `company_facts` (XBRL payloads), `filing_documents`, `filing_exhibits` (gzip), `filing_xbrl` (facts extracted from the documents' inline XBRL), `filing_instances` / `filing_facts` (a filing's own XBRL where companyfacts lacks it), `ownership_documents` (Forms 3/4/5 as filed), load checkpoints | bronze-loader, company-loader, facts-fetcher, doc-fetcher |
| `silver` | typed and resolved: `companies`, `company_ciks`, `company_aliases`, `metrics`, `metric_tags`, `metric_sums`, `member_sums`, `financial_facts`, `filing_sections`, `filings` (view), `sic_sectors` / `company_sectors` (view), `segment_axes`, `segment_metrics`, `segment_metric_tags`, `segment_facts`, `insider_filings`, `insider_transactions` | company-loader, facts-fetcher and doc-fetcher (via SQL functions), parser |
| `gold` | what the Query API serves, with provenance (FR6): `quarterly_financials`, `derived_financials` (TTM and computed metrics), `financials` / `latest_financials` (views), `filing_sections` (view), `segment_financials` (view `segments`), `section_search`, `section_diffs`, `section_changes`, `business_profiles`, `business_peers`, `business_mentions` | `gold.refresh_financials`, `gold.refresh_segments`, `gold.refresh_section_search`, parser-index, parser-peers |
| `authz` | `user_access` (private grants: user, company, tier), `user_tiers` (each user's plan tiers), `public_access` (public catalog: company, tier); `readable_companies(tier)`, the read rule every policy uses | authz-sync |
| `ops` | freshness and pipeline-health views for Grafana, the ops exporter and the public status page; the data-quality findings; the shared SEC rate limit; the document crawl's queue; the section index's checkpoint; daily completeness; doc-fetcher's outbox and insider queue | the services named per migration below |
| `userdata` | a user's own data (migration 100): `datasets`, `rows` (by company, day and when known), `uploads` (the hourly limit), `series` (view); owner-only, forced RLS on `app.user_id`, nothing grants access; in every backup (it can't be rebuilt) | the Query API, as the user |

Transforms are SQL functions (`silver.refresh_financial_facts`, `gold.refresh_quarterly_financials`,
`gold.refresh_derived_financials`, `silver.refresh_company_metadata`, ...: [transforms/](transforms/README.md)
lists the latest definition of each); Python services only fetch data and call them. Company lookup:
`SELECT * FROM silver.resolve_company('FB');`.

Row-level security: the Query API's queries run as the `query_api` role with the transaction's
`app.user_id` set, and every per-company table it reads has a policy on
`authz.readable_companies(tier)`: private grants (`authz.user_access`) plus the public catalog
(`authz.public_access`, within the user's `authz.user_tiers`). No `app.user_id`, no rows. A few
SECURITY DEFINER functions apply the same rule themselves where a policy can't run an index or
`query_api` has no grant on the values ([rls/](rls/README.md)).

## Migrations

`scripts/migrate.sh <dir> <database-url>` applies `<dir>/*.sql` in name order, each once, in
one transaction together with its row in `public.schema_migrations`.

```bash
make migrate    # both databases, from .env
make seed-app   # demo users in the app database
make psql       # psql shell on tributary
```

Files are numbered (`001_bronze.sql`, ...) and never edited after they land: changes go in a
new file. On k3s, the Helm chart's `migrate` pre-install/upgrade Job runs the same script from
the image built by [`Dockerfile`](Dockerfile) and seeds demo users when `demo.enabled`.

A migration that changes which facts gold reads or what it computes reaches existing rows by
itself ([below](#versions-and-catch-up)); `facts-fetcher --rebuild` (`make financials-rebuild`;
production: the `financials-rebuild` job) does it all at once. Those that
replaced gold functions since 038 can be undone by hand from [`backout/`](backout/README.md):
newest first, as the migrate login; each file's guard refuses any other order or login and changes
nothing. Each migration's header has the measurements behind its thresholds and its lock impact on
production.

[`rls/`](rls) and [`transforms/`](transforms) hold no SQL; their READMEs point to the
migrations that define those objects. [`research/`](research) holds read-only queries behind curated
data (the re-registration search behind company-loader's `re_registrations.csv`); never run by a job.

### Versions and catch-up

Stored data records the version of the rule that built it, and the hourly jobs bring older data up
to date by themselves, oldest first, a budget per run, after their own work (migration 071):

| Kind | Current version | Stamp | Caught up by |
|---|---|---|---|
| parser | `PARSER_VERSION` (sections.py, both parsers) | `silver.filing_sections.parser_version` | `section-index` re-queues documents for `parser-backfill` |
| passages | `SEARCH_RULE` (index.py) | `ops.section_index.passages_rule` | `section-index` |
| diffs | `diff.ALGORITHM` | `ops.section_index.algorithm` | `section-index` (its own run) |
| gold | `gold.rules_version()` | `ops.company_builds` ('gold') | `segments`: the rebuild path, per company |
| checks | `ops.check_financials_version()`, `ops.check_segments_version()` | `ops.company_builds` | `segments`: the check re-run |
| known | `gold.rules_version()` | `ops.company_builds` ('known') and a `gold.known_companies` row | `segments`: each company's history replayed after its gold (086) |
| parser checks | `ops.check_sections_version()` | `ops.section_checks.version` | `section-index` (072) |

**A migration that changes gold's rules or `silver.metric_tags` (anything that needed
financials-rebuild) replaces `gold.rules_version()` with its own number; one that replaces
`ops.check_financials` or `ops.check_segments` bumps that check's version.** Every refresh stamps the
companies it built (`gold.refresh_financials`, `gold.refresh_segments`); a company without a stamp was
built with `ops.catchup.baseline`. Gold writers of one company take turns (`gold.lock_companies`).
Progress for ops_reader: `ops.catchup_status` (behind, in flight, failed, the last day's pace, ETA);
pause a kind with `UPDATE ops.catchup SET paused = true WHERE kind = '...'` (a paused kind is still
measured, and the alert ignores it). The parser kind ships paused: `UPDATE ops.catchup SET paused =
false WHERE kind = 'parser';` starts it. A backout leaves the
versions as they are: run `financials-rebuild` after one.

A run that did nothing says why in `note` (079: `skipped: lock held` while a hand-run job holds
parser-index's lock, with the last count), and a new version's backlog is recorded when its first
run starts. The screener's stored figures (078: `gold.screen_companies`, `gold.screen_figures`) are
written by every gold build and filled for companies without them by the `segments` job's catch-up,
first (`gold.refresh_screen_backlog`); a company without them is read live. Gold's history
(086: `gold.financials_known`) is kept by every gold build for a company the current rules replayed
(the filing dates whose facts changed), and replayed whole by the 'known' kind (every company after
the deploy and after every rules bump; it runs from the deploy, pausable like the others). The dumps
leave the history out: after a restore, the catch-up replays every company.

### What each migration adds

One line each; the full notes (what it changes, why, the measurements) are in [DESIGN.md](DESIGN.md).

- [`001_bronze.sql`](DESIGN.md#001_bronzesql): `bronze.filings`: one row per (filing, filer) from `filings.raw`, the message untouched.
- [`002_companies.sql`](DESIGN.md#002_companiessql): company identity (FR5): companies, CIKs, aliases, `silver.resolve_company`.
- [`003_financials.sql`](DESIGN.md#003_financialssql): XBRL financials: companyfacts payloads, metrics and tags, `silver.financial_facts`, `gold.quarterly_financials`.
- [`004_documents.sql`](DESIGN.md#004_documentssql): filing documents (gzip), `silver.filing_sections` and the view `gold.filing_sections`.
- [`005_filings_coverage.sql`](DESIGN.md#005_filings_coveragesql): `bronze.filings_daily_coverage`: how much of each day the live poller caught.
- [`006_user_access.sql`](DESIGN.md#006_user_accesssql): the `authz` schema and `authz.user_access`, written by authz-sync.
- [`007_query_api_rls.sql`](DESIGN.md#007_query_api_rlssql): the `query_api` role and the first RLS policies.
- [`008_filing_urls.sql`](DESIGN.md#008_filing_urlssql): a column grant on `silver.financial_facts (cik, accession_no)` for citation links.
- [`009_filing_exhibits.sql`](DESIGN.md#009_filing_exhibitssql): `bronze.filing_exhibits`: 8-K exhibits (EX-99.x), parsed as sections keyed by type.
- [`010_ops.sql`](DESIGN.md#010_opssql): the `ops` schema's first freshness views and the `ops_reader` role.
- [`011_bulk_financials.sql`](DESIGN.md#011_bulk_financialssql): every SEC filer's XBRL from the nightly companyfacts.zip.
- [`012_more_metrics.sql`](DESIGN.md#012_more_metricssql): the full income statement, balance sheet and cash flow lines as metrics.
- [`013_submissions.sql`](DESIGN.md#013_submissionssql): every filer's filing history from the nightly submissions.zip; `silver.filings`.
- [`014_gold_calendar.sql`](DESIGN.md#014_gold_calendarsql): calendar quarters, fiscal labels, TTM and derived metrics; the data-quality checks.
- [`015_gold_tiebreaks.sql`](DESIGN.md#015_gold_tiebreakssql): deterministic tie-breaks in gold refreshes.
- [`016_seed_source.sql`](DESIGN.md#016_seed_sourcesql): `source = 'seed'` for history loaded by doc-fetcher-seed.
- [`017_ui_data.sql`](DESIGN.md#017_ui_datasql): what the research UI reads: latest figures, coverage, expected filings, profiles.
- [`018_segments.sql`](DESIGN.md#018_segmentssql): segment and KPI facts from the documents' inline XBRL.
- [`019_sec_rate_limit.sql`](DESIGN.md#019_sec_rate_limitsql): one SEC rate limit for every process (token bucket in one locked row).
- [`020_doc_crawl.sql`](DESIGN.md#020_doc_crawlsql): the document crawl's queue, plan, pause switch and checkpoint.
- [`021_section_diffs_search.sql`](DESIGN.md#021_section_diffs_searchsql): full-text search passages and What changed diffs, built by parser-index.
- [`022_sec_limiter_role.sql`](DESIGN.md#022_sec_limiter_rolesql): the `sec_limiter` role: EXECUTE on the rate-limit functions only.
- [`023_data_notes.sql`](DESIGN.md#023_data_notessql): the `restatement` check and the Query API's read of data-quality findings.
- [`024_gold_ignores_proxy_statements.sql`](DESIGN.md#024_gold_ignores_proxy_statementssql): gold skips facts from proxy and information statements.
- [`025_filing_sections_company_pushdown.sql`](DESIGN.md#025_filing_sections_company_pushdownsql): `gold.filing_sections` keyed by company first, so company filters use indexes.
- [`026_feed_status.sql`](DESIGN.md#026_feed_statussql): `ops.feed_status()`: two freshness timestamps for uptime checks.
- [`027_filing_completeness.sql`](DESIGN.md#027_filing_completenesssql): daily completeness against EDGAR's daily index.
- [`028_public_status.sql`](DESIGN.md#028_public_statussql): the public status page's aggregate functions.
- [`029_gold_quality_fixes.sql`](DESIGN.md#029_gold_quality_fixessql): two causes of quality-check errors in gold fixed.
- [`030_public_access.sql`](DESIGN.md#030_public_accesssql): open access, layer 2: `authz.public_access`, `user_tiers`, `authz.readable_companies(tier)`.
- [`031_gold_one_quarter_series.sql`](DESIGN.md#031_gold_one_quarter_seriessql): one non-overlapping series of quarters per company and metric.
- [`032_open_access_speed.sql`](DESIGN.md#032_open_access_speedsql): fast cross-company reads with the catalog open (latest values, search passages).
- [`033_filing_sections_accession_pushdown.sql`](DESIGN.md#033_filing_sections_accession_pushdownsql): `gold.filing_sections` outputs the accession key so accession filters push down.
- [`034_company_search.sql`](DESIGN.md#034_company_searchsql): `silver.search_companies`: tickers, CIKs and names, ranked.
- [`035_value_readings.sql`](DESIGN.md#035_value_readingssql): the one definition of how a figure may be read (`not_meaningful`, banks, old periods).
- [`036_annual_per_share.sql`](DESIGN.md#036_annual_per_sharesql): `gold.annual_per_share`: the 10-K's own EPS and weighted shares per year.
- [`037_check_segments_subtotal.sql`](DESIGN.md#037_check_segments_subtotalsql): `ops.check_segments` stops adding a reconciliation member that restates the sum.
- [`038_metric_tags_moved.sql`](DESIGN.md#038_metric_tags_movedsql): tags a line moved to, and gold rules backed by `db/backout`.
- [`039_gold_fact_selection.sql`](DESIGN.md#039_gold_fact_selectionsql): `gold.usable_facts` and `gold.best_facts`: which fact gold believes.
- [`040_gold_derived_rules.sql`](DESIGN.md#040_gold_derived_rulessql): stated-year TTM, net debt and other derived-metric rules.
- [`041_quality_findings.sql`](DESIGN.md#041_quality_findingssql): quality findings: annual comparison from `best_facts`, `impossible_value`, `mistagged_fact`.
- [`042_fiscal_year_end_month.sql`](DESIGN.md#042_fiscal_year_end_monthsql): `silver.fiscal_year_end_month`: the month a 52/53-week year belongs to.
- [`043_interest_expense_sign.sql`](DESIGN.md#043_interest_expense_signsql): interest expense read as a cost (`abs(value)`).
- [`044_company_search_order.sql`](DESIGN.md#044_company_search_ordersql): company search ties broken by coverage and name length.
- [`045_filing_events.sql`](DESIGN.md#045_filing_eventssql): 8-K item numbers readable by the Query API (`silver.company_8ks`).
- [`046_sectors.sql`](DESIGN.md#046_sectorssql): sectors: Tributary's own open grouping of SIC codes.
- [`047_business_peers.sql`](DESIGN.md#047_business_peerssql): peers by business: profiles, similar companies and named competitors from 10-K Item 1.
- [`048_bank_metrics.sql`](DESIGN.md#048_bank_metricssql): a bank's own lines (net interest income, deposits, CET1 ...).
- [`049_kpis_cash_flow.sql`](DESIGN.md#049_kpis_cash_flowsql): cash flow subtotals and the KPIs analysts asked for.
- [`050_filers_debt_signs.sql`](DESIGN.md#050_filers_debt_signssql): a filing read as the company its XBRL names; debt signs.
- [`051_filing_figures.sql`](DESIGN.md#051_filing_figuressql): `gold.filing_figures`: one filing's facts as gold reads them.
- [`052_search_sort_total.sql`](DESIGN.md#052_search_sort_totalsql): `gold.search_page`: search with an order and a total.
- [`053_segment_same_basis.sql`](DESIGN.md#053_segment_same_basissql): segment quarters derived on the same basis.
- [`054_insider_transactions.sql`](DESIGN.md#054_insider_transactionssql): insider filings (Forms 3, 4, 5).
- [`055_filing_xbrl_facts.sql`](DESIGN.md#055_filing_xbrl_factssql): a filing's own XBRL where companyfacts lacks it (`source` 'filing', 'filing_extra').
- [`056_ifrs_currencies.sql`](DESIGN.md#056_ifrs_currenciessql): foreign filers: IFRS tags and any ISO currency.
- [`057_currency_readings.sql`](DESIGN.md#057_currency_readingssql): companies read in their own currency; readings say the currency.
- [`058_bank_holding_companies.sql`](DESIGN.md#058_bank_holding_companiessql): Schwab, American Express, Synchrony and Ally read as banks.
- [`059_fetch_outbox_insider_queue.sql`](DESIGN.md#059_fetch_outbox_insider_queuesql): doc-fetcher's document outbox and insider queue.
- [`060_annual_filer_currency.sql`](DESIGN.md#060_annual_filer_currencysql): an annual filer's currency from its TTM rows.
- [`061_screener_currency_inline.sql`](DESIGN.md#061_screener_currency_inlinesql): the currency rule made set-returning so the screener can afford it.
- [`062_segment_refresh_speed.sql`](DESIGN.md#062_segment_refresh_speedsql): segment refresh in time linear in the companies of a call.
- [`063_rebuild_speed.sql`](DESIGN.md#063_rebuild_speedsql): two slow parts of the financials rebuild made faster.
- [`064_revenue_net_of_interest_part.sql`](DESIGN.md#064_revenue_net_of_interest_partsql): two revenue rules in `gold.best_facts` (net-of-interest part, scale slips).
- [`065_search_exact_phrase.sql`](DESIGN.md#065_search_exact_phrasesql): search: a quoted phrase matches its exact words.
- [`066_company_search_classified_first.sql`](DESIGN.md#066_company_search_classified_firstsql): company search: a company with a SIC code before a fund named after it.
- [`067_filing_tables.sql`](DESIGN.md#067_filing_tablessql): `silver.filing_tables`: each section's HTML tables beside its text (parser v7).
- [`068_search_page_furniture.sql`](DESIGN.md#068_search_page_furnituresql): search passages without page furniture.
- [`069_readings_not_computed_stale.sql`](DESIGN.md#069_readings_not_computed_stalesql): readings `not_computed` and `stale`.
- [`070_check_segments_overlap.sql`](DESIGN.md#070_check_segments_overlapsql): `ops.check_segments`: overlapping members aren't a deviation when a set adds up.
- [`071_auto_catchup.sql`](DESIGN.md#071_auto_catchupsql): rule versions, per-company build stamps and the catch-up's state, status and parser re-queue.
- [`072_data_trust_checks.sql`](DESIGN.md#072_data_trust_checkssql): the tag-disagreement check, check tiers and findings per 1,000 figures, parser-output checks, the extremes report.
- [`078_screener_figures.sql`](DESIGN.md#078_screener_figuressql): the screener's latest figures, stored by the gold build; the readings' rules in `gold.reading`.
- [`079_catchup_status_cleanups.sql`](DESIGN.md#079_catchup_status_cleanupssql): catch-up status says why a run did nothing; backlogs as anti-joins; search's old forms dropped.
- [`080_revenue_industry_tags.sql`](DESIGN.md#080_revenue_industry_tagssql): revenue under industry totals (goods, services, real estate) where no other tag gives it; total assets from liabilities and equity.
- [`081_coverage_notes.sql`](DESIGN.md#081_coverage_notessql): the coverage sweep's notes after 080: revenue tagged only yearly, commodity trusts, partners' capital.
- [`082_sector_hvac_controls.sql`](DESIGN.md#082_sector_hvac_controlssql): SIC 3822 (Trane) in Industrials with the HVAC makers.
- [`083_gross_sales_revenue.sql`](DESIGN.md#083_gross_sales_revenuesql): a trading venue's gross sales read as not-meaningful revenue (Bullish).
- [`084_net_sales_gross_margin.sql`](DESIGN.md#084_net_sales_gross_marginsql): Walmart's and Costco's net sales, and their gross margin on them.
- [`085_point_in_time_pipeline.sql`](DESIGN.md#085_point_in_time_pipelinesql): gold's pipeline as pure functions of a knowledge date (`p_as_of`); the same rows.
- [`086_financials_known.sql`](DESIGN.md#086_financials_knownsql): gold's history of itself (`gold.financials_known`): every version of every row, from the filing that first stated it; the 'known' catch-up kind.
- [`087_point_in_time_reads.sql`](DESIGN.md#087_point_in_time_readssql): reading gold as of a day: `gold.financials_at`, the single-company read, the readings' probes with `p_as_of`, restatements.
- [`100_userdata.sql`](DESIGN.md#100_userdatasql): Bring your own data: the schema `userdata` (a user's own datasets, their rows by company, day and when known, the upload log), owner-only under forced RLS, in every backup.
- [`110_figures_landed.sql`](DESIGN.md#110_figures_landedsql): `ops.figures_landed_at()`, when the nightly figures last landed (the bulk job's coverage run): when watched screens are checked.
