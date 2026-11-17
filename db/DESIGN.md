# db: design notes

What each migration adds and why, with the measurements behind its thresholds. The entry point is
[README.md](README.md) (schemas, how migrations run, the list of migrations). Each migration's own header
has its lock impact on production; [transforms/](transforms/README.md) lists the latest body of each function.

## What each migration adds

### 001_bronze.sql

`001_bronze.sql`: `bronze.filings`, one row per (filing, filer) from the `filings.raw` topic, the
Kafka message untouched.

### 002_companies.sql

`002_companies.sql`: company identity (FR5): `silver.companies`, `silver.company_ciks`,
`silver.company_aliases`, `silver.normalize_name` and `silver.resolve_company`. `company_id` is the
first CIK in the company's lineage.

### 003_financials.sql

`003_financials.sql`: XBRL financials: `bronze.company_facts` (each distinct companyfacts payload per
CIK), `silver.metrics`, `silver.metric_tags`, `silver.financial_facts`, `gold.quarterly_financials`
and the first refresh functions.

### 004_documents.sql

`004_documents.sql`: `bronze.filing_documents` (each filing's primary document, gzip),
`silver.filing_sections` and the view `gold.filing_sections`.

### 005_filings_coverage.sql

`005_filings_coverage.sql`: `bronze.filings_daily_coverage`, how much of each day the live poller
caught before the daily-index backfill filled the rest.

### 006_user_access.sql

`006_user_access.sql`: the `authz` schema and `authz.user_access` (user, company, tier), written by
authz-sync.

### 007_query_api_rls.sql

`007_query_api_rls.sql`: the `query_api` role and the policies on `gold.quarterly_financials` and
`silver.filing_sections` (behind `gold.filing_sections`) ([rls/](rls/README.md)).

### 008_filing_urls.sql

`008_filing_urls.sql`: a column grant on `silver.financial_facts (cik, accession_no)`, for SEC links
in financial citations; never the values.

### 009_filing_exhibits.sql

`009_filing_exhibits.sql`: `bronze.filing_exhibits`, 8-K exhibits (EX-99.x press releases); the
parser stores each as a section keyed by its type ("EX-99.1").

### 010_ops.sql

`010_ops.sql`: the `ops` schema's first views (`ops.filing_latency`, `ops.filings_daily`,
`ops.filings_daily_coverage`, `ops.companyfacts_lag`) and the `ops_reader` group role (SELECT on
`ops` views only).

### 011_bulk_financials.sql

`011_bulk_financials.sql`: XBRL for every SEC filer from the nightly companyfacts.zip beside the
per-company API: `bronze.company_facts.raw_gzip` (the whole payload, so a tag change re-projects
without the SEC) and `source`, `bronze.bulk_loads` checkpoints, set-based refreshes over arrays of
CIKs and companies, `gold.financials_pending`.

### 012_more_metrics.sql

`012_more_metrics.sql`: the full income statement, balance sheet and cash flow lines as metrics.

### 013_submissions.sql

`013_submissions.sql`: every filer's filing history and metadata from the nightly submissions.zip:
`bronze.submissions`, `bronze.submission_filings`, `bronze.submissions_loads`,
`silver.refresh_company_metadata`, and the view `silver.filings` (live feed and history unioned).

### 014_gold_calendar.sql

`014_gold_calendar.sql`: calendar quarters, fiscal labels, TTM and derived metrics
(`gold.derived_financials`, `gold.derived_metrics`, the view `gold.financials`), and the data-quality
checks run after every gold refresh (`ops.check_financials`, `ops.quality_runs` /
`quality_companies` / `quality_findings`, `ops.quality_summary`, `ops.quality_issues`).
`gold.refresh_financials` runs both refreshes and the checks.

### 015_gold_tiebreaks.sql

`015_gold_tiebreaks.sql`: deterministic tie-breaks, so repeated gold refreshes pick the same row.

### 016_seed_source.sql

`016_seed_source.sql`: history loaded by `doc-fetcher-seed` carries `source = 'seed'` and its
corrected acceptance time; earlier seed rows are marked and corrected.

### 017_ui_data.sql

`017_ui_data.sql`: what the research UI reads: `gold.latest_financials` (security_invoker, RLS
applies), `gold.financials_coverage` (company ids with financials only; runs as its owner, so
public), `silver.filings` granted as the public SEC filing index, and the functions
`gold.expected_filings`, `gold.filing_recap`, `gold.filing_flags`, `gold.peer_candidates`.

### 018_segments.sql

`018_segments.sql`: segment and KPI data from the stored 10-K/10-Q documents' inline XBRL
(facts-fetcher's `facts-fetcher-segments`): `bronze.filing_xbrl`, `silver.segment_facts`,
`gold.segment_financials` (RLS `granted_financials`, like quarterly financials) and the view
`gold.segments` (fiscal/calendar labels), the `segment_sum_*` quality checks
(`ops.check_segments`) and `ops.segment_load_status` for `ops_reader`.

### 019_sec_rate_limit.sql

`019_sec_rate_limit.sql`: one SEC rate limit for every process: a token bucket in one locked row
(`ops.sec_rate_limit`, `ops.sec_rate_classes`), taken by `ops.sec_take_token` before every SEC
request and slowed by `ops.sec_report_throttle`, with priority classes so live traffic goes ahead of
bulk loads; `ops.sec_rate_limit_status` for `ops_reader` ([libs/sec](../libs/sec/README.md)).

### 020_doc_crawl.sql

`020_doc_crawl.sql`: the document crawl's queue and checkpoint (`ops.doc_crawl_targets`, planned by
`ops.doc_crawl_plan`), its pause switch (`ops.doc_crawl_control`) and the `ops.doc_crawl_progress` /
`ops.doc_crawl_status` views for `ops_reader`.

### 021_section_diffs_search.sql

`021_section_diffs_search.sql`: full-text search and "what changed" over parsed sections, both
built by the parser's `parser-index` job: `gold.section_search` (one row per ~2,000-character
passage: offsets + tsvector, GIN index, no text; `gold.refresh_section_search`),
`gold.section_diffs` / `gold.section_changes` (paragraph-level Risk Factors and MD&A diffs vs the
comparable earlier filing: offsets and word-diff opcodes, no text), all three under RLS
`granted_filings`; the checkpoint `ops.section_index` with `ops.section_index_priors` /
`ops.section_index_pending`, and `ops.section_change_summary` / `ops.section_index_status` (counts
only) for `ops_reader`. All three gold tables are derived: rebuild them by running the job.

### 022_sec_limiter_role.sql

`022_sec_limiter_role.sql`: the `sec_limiter` group role: EXECUTE on `ops.sec_take_token` /
`ops.sec_report_throttle` only (now SECURITY DEFINER with a fixed `search_path`, revoked from
PUBLIC), no table access. The login is created on the host
([deploy/helm/tributary](../deploy/helm/tributary/README.md), "SEC limiter login").

### 023_data_notes.sql

`023_data_notes.sql`: the `restatement` check (severity `info`) in `ops.check_financials` and
`ops.quality_summary`, and the Query API's read of `ops.quality_findings` / `ops.quality_companies`
(USAGE on `ops`, RLS `granted_financials`) for the product's data notes.

### 024_gold_ignores_proxy_statements.sql

`024_gold_ignores_proxy_statements.sql` (found by the [golden 10-K figures](../tests/golden/README.md)):
`gold.refresh_quarterly_financials` skips facts from proxy and information statements (forms
starting DEF or PRE; silver keeps them), and adds Costco's 10-Q pre-tax tag
(`IncomeLossAttributableToParent`, third `pretax_income` tag). Needs `make financials-rebuild`.

### 025_filing_sections_company_pushdown.sql

`025_filing_sections_company_pushdown.sql`: `company_id` first in `gold.filing_sections`' DISTINCT
ON key (same rows), so a company filter reaches the base tables' indexes instead of de-duplicating
every section first.

### 026_feed_status.sql

`026_feed_status.sql`: `ops.feed_status()`: when the newest filing was accepted by EDGAR and when
it reached bronze, two timestamps and nothing else. It is SECURITY DEFINER (query_api still can't
read `bronze.filings`) and backs the Query API's public `/v1/status`, which the
[outside uptime check](../deploy/uptime/README.md) reads.

### 027_filing_completeness.sql

`027_filing_completeness.sql`: `ops.filing_completeness` (per day of EDGAR's daily index:
accessions indexed, accessions missing from bronze at the last check, rows republished), written
by the nightly completeness check ([poller-backfill](../services/poller/README.md#backfill-fr1b)),
and `ops.filing_completeness_status` for ops_reader. Counts only.

### 028_public_status.sql

`028_public_status.sql`: the public status page (Query API `/v1/status/data`): four SECURITY
DEFINER functions (search_path pinned, EXECUTE only for query_api, like 026) that return
aggregates only: `ops.public_ingest()` (last 7 days of `bronze.filings`: filings, how many the live
feed caught, p50/p95 seconds from acceptance to bronze), `ops.public_completeness()` (the last 30
days of `ops.filing_completeness`), `ops.public_coverage()` (companies with gold financials by a
loose index scan of its primary key, stored documents, search-indexed filings and sections) and
`ops.public_quality()` (`ops.quality_summary`). It also indexes `bronze.filings (filed_at)` so the
7-day window is a range scan; the index is built inside the migration, so writes to
`bronze.filings` wait for it.

### 029_gold_quality_fixes.sql

`029_gold_quality_fixes.sql`: two causes of the quality checks' errors in
`gold.quarterly_financials`: flow metrics tagged at an instant (UnitedHealth's dividend payment
dates) no longer become extra quarter rows (1,838 rows on hermes), and a derived quarter that
comes out negative for a metric that can't be (revenue, costs, capex, D&A, SBC, buybacks,
dividends) is left missing instead of published (11,320 rows; e.g. Cytek's Q4 2022 revenue at
-$112.7M after a later 10-K re-tagged the year). Needs `make financials-rebuild`.

### 030_public_access.sql

`030_public_access.sql` (open access, layer 2): `authz.public_access` and `authz.user_tiers`
(written by authz-sync), the read rule `authz.readable_companies(tier)` / `authz.can_read`,
`authz.catalog_companies()` (catalog membership: companies with gold financials or a stored
document, read by authz-sync) and the nine `granted_*` policies of the time re-pointed at the rule.
Ships with `public_access` empty: no one's access changes until the catalog is opened
([app/README.md](app/README.md), "Open access").

### 031_gold_one_quarter_series.sql

`031_gold_one_quarter_series.sql`: one non-overlapping series of quarters per company and metric in
`gold.quarterly_financials`. Gold kept one row per period end, so a quarter reported with two
(start, end) pairs survived twice: an end date a day apart (NVIDIA's Q2 FY2011 ends Aug 1 in the
10-Q, Jul 31 in a later 10-K), last year's quarters re-labelled to another calendar in this
year's comparatives (ATI, Dine Brands), calendar-month quarters next to fiscal ones after a
change of fiscal year (TranSuite.org). On hermes that was 11,080 `overlapping_quarters` findings
at 620 companies. The rule, for duration rows (instants are points and keep one row per date):
(a) calendar votes: per company and date, how many metrics have a balance-sheet value on it and
how many have any fact ending on it; a quarter scores its last day plus the day before its first
day, so the series whose boundaries carry the company's own balance sheets wins (a re-labelled
comparative has none); (b) rank, best first: instant votes, metric votes, then 029's order
(tag priority, reported over derived, latest filed, accession, ...), then period end: total, so
refreshes stay byte-identical; the same order picks the one row per period end; (c) two rows
overlapping by more than 7 days conflict (quarters are 80-120 days, so they can't share a
series; up to 7 days is a start tagged a day or two early, tolerated like the TTM's 0-7 day
gaps); (d) greedy, best first: a row is dropped only for a kept row it conflicts with (no
quarter is lost to a chain), and rows without a conflict are untouched. Derived quarters never
mix series: both inputs come from one year-to-date chain (same start), and the start-day vote
keeps the Q4 of the chain whose quarters are kept. A read-only simulation on hermes' gold took
the 620 companies from 11,041 overlaps to 1,686, all of 1-7 days, dropping ~6,400 rows; the
fixture companies (Costco's 16-week Q4 included) and the golden 10-K figures are unchanged.
Trade-off: when a company re-labels and also restates last year's quarters (ATI 2022-2023),
gold keeps the original filing's dates and values. Needs `make financials-rebuild`.

### 032_open_access_speed.sql

`032_open_access_speed.sql` keeps the cross-company routes fast with the catalog open, without
changing who reads what: `gold.latest_value(company, span, metric)` (`gold.latest_financials`' rule
as one index lookup per table, security invoker, inlined), a last column `filed_on` on
`silver.filings` (the date from the row's own table, so a window reaches both UNION branches), and
`gold.search_passages` (search's first stage: `@@` isn't leakproof, so under RLS the GIN index on
`gold.section_search` is unusable; SECURITY DEFINER with `authz.readable_companies('filings')`
applied inside, search_path pinned, `query_api` only, returns offsets and ranks, no text), and
`gold.expected_filings_recent` (017's estimates for every readable company, from the last 1,500 days
of reports). No index is built; the view takes a brief lock (`lock_timeout` 30 s, the migration fails
rather than queueing readers).

### 033_filing_sections_accession_pushdown.sql

`033_filing_sections_accession_pushdown.sql`: `gold.filing_sections` outputs `s.accession_no`, the
key column, so a filter on accession reaches the base tables too. Before it, `gold.filing_flags` and
so `/v1/filings/{acc}/recap` sorted all 1.8M sections and timed out, and
`gold.refresh_section_search` sorted them once per batch (locally 3.3 s → 12 ms). Replacing the view
takes ACCESS EXCLUSIVE on it, so its `lock_timeout` is 3 s: if the hourly section-index job holds
the view, the deploy hook fails fast (re-run it) instead of queueing every filing page behind it.

### 034_company_search.sql

`034_company_search.sql`: `silver.search_companies(q)`, what a search box does with what people
type: exact ticker first (`BRK.B` = `BRK-B`), then CIK, exact name, retired ticker or former name,
then prefix, a later word, and contained (3+ characters), with spaces ignored for `JP Morgan` →
JPMorgan Chase. `silver.resolve_company` keeps its exact-match meaning for formulas and the
sandbox. Reads all 33,000 aliases per call (25-38 ms on hermes); no index was needed.

### 035_value_readings.sql

`035_value_readings.sql`: the one definition of how a figure may be read: `gold.not_meaningful`
(thresholds from the data, in its header), `gold.not_applicable_metrics` (banks),
`gold.period_status` ("stale", once: over 300 days behind the company's latest period is old, over
45 is behind), `gold.latest_period` and `gold.latest_reading` (032's `gold.latest_value` plus the
reading, inlinable, under RLS). New functions only; no table changes and nothing to rebuild.

### 036_annual_per_share.sql

`036_annual_per_share.sql`: `gold.annual_per_share`: the 10-K's own EPS and weighted shares per
fiscal year, SECURITY DEFINER because `query_api` has no grant on the facts table's values, with
`authz.readable_companies` as its only rule.

### 037_check_segments_subtotal.sql

`037_check_segments_subtotal.sql`: `ops.check_segments` no longer adds a reconciliation member that
just restates the segments' sum (NVIDIA's "109.0% more" is 4.5%). Stored findings keep their old
figure until the check runs again (`make segments-rebuild`, or `SELECT
ops.check_segments(array_agg(DISTINCT company_id)) FROM gold.segment_financials`).

### 038_metric_tags_moved.sql

`038_metric_tags_moved.sql`: tags a line moved to (Nike's investments to
`DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent`, DTE's and Xcel's revenue to
`RegulatedAndUnregulatedOperatingRevenue`, Tesla's, Adobe's, Lennar's and Oracle's debt to
`LongTermDebt` / `LongTermNotesPayable` / `LongTermNotesAndLoans` / `DebtCurrent` /
`NotesPayableCurrent`, small filers' `Cash`, VF's `PaymentsForCapitalImprovements`) and
`silver.metric_sums`: a line filed as two tags, both from one filing for one date (a bank's
`InterestIncomeExpenseNet + NoninterestIncome`; NVIDIA's `DebtSecuritiesCurrent + EquitySecuritiesFvNi`).
A sum's parts are `silver.metric_tags` rows with `gold_line = false`: stored, never a line alone.
Needs `financials-rebuild`; backout `backout/gold_rules_038_041.sql` (038-041 together).

### 039_gold_fact_selection.sql

`039_gold_fact_selection.sql`: `gold.usable_facts(ids)` (silver joined to the tags by tag, so a tag
two metrics map serves both: `EarningsPerShareBasicAndDiluted` gave no diluted EPS at 173 of the
sample's companies; the sums; a loss quarter's basic EPS as diluted; without proxy statements,
periods ending over 7 days after their filing, debt over five times both liabilities and assets, or
a fallback debt tag at a bank; a fallback debt total less its current portion, where it is one:
equal to the current portion, or above it and above all noncurrent liabilities; below the current
portion it is the noncurrent line) and `gold.best_facts(ids)` (each tag's latest filing, then for
revenue: a zero loses to another tag's later or same-filing value of $1M or more; Revenues under 20%
of contract revenue is a part; then tag priority). `gold.refresh_quarterly_financials` reads
`best_facts` (031's series rule unchanged), drops a reported quarter rounded for a sentence for the
exact year-to-date difference (NVIDIA's dividends 6,000 → 6,047), and adds gross profit as revenue
less cost where none is printed, behind the guard in the header (SIC, cost ≥ 15% of revenue, margin
≥ operating margin, no large unexplained cost, within 8 points of the company's median, quarters
since 2019). Validated on hermes where gross profit is reported: 98.5% within a point; fills 7,675
quarters at 559 companies.

### 040_gold_derived_rules.sql

`040_gold_derived_rules.sql`: `gold.refresh_derived_financials` adds a TTM from the stated year where
four quarters don't exist (not when a must-be-positive year is negative or below its own quarters;
ends within 7 days are one year, 031's rule, so ATI's FY2021 tagged ending Dec 31 and Jan 2 is in
gold once), and net debt's rules: investments inside cash aren't subtracted twice; a recent, material
investments series with no value at the date, where the balance sheet has room for it, makes net
debt absent; so does debt that can't carry its interest (Ford). Net debt counts short-term
investments only.

### 041_quality_findings.sql

`041_quality_findings.sql`: `ops.check_financials` reads `best_facts` for the annual comparison,
flags negative debt as an `impossible_value`, and adds `mistagged_fact` (error: a period after its
filing, debt over five times liabilities and assets), `net_debt_gap` (warning) and a repeated
`series_gap` (the same quarter missing three or more years); `ops.quality_summary` lists them.

### 042_fiscal_year_end_month.sql

`042_fiscal_year_end_month.sql`: `silver.fiscal_year_end_month(mmdd)`, the month a 52/53-week year
belongs to (a day of 1-7 is the month before). The screener's filter and sort; labels unchanged.

### 043_interest_expense_sign.sql

`043_interest_expense_sign.sql`: interest expense is a cost, so `gold.usable_facts` reads it as
`abs(value)` under every tag (filings flip its sign between filings and in comparatives: Disney's
FY2025 10-K -1,812M against +1,396M nine months in its 10-Qs made Q4 -3,208M; it's 416M), and a
derived interest expense quarter that is still negative is left out, like 029's other costs. On
hermes before: 2,951 negative quarters at 986 companies, 1,096 `sign_flip` findings. Silver keeps
the filing's figure. Needs `financials-rebuild`; backout `backout/interest_expense_043.sql`.

### 044_company_search_order.sql

`044_company_search_order.sql`: breaks ties within a rank by whether the company has figures here
(`gold.financials_coverage`, public ids) and the normalized name's length, so "bank of" lists Bank
of America before Bank of Chile (a 20-F filer) and isn't pushed down by an SEC suffix (`/DE/`).

### 045_filing_events.sql

`045_filing_events.sql`: lets the Query API read 8-K item numbers: a column grant on
`bronze.submission_filings (accession_no, cik, items)` (filing-index metadata, public like
`silver.filings`) and `silver.company_8ks(company, from)`, one company's 8-K and 8-K/A with their
items as an array (NULL until the nightly submissions load has the filing). The earnings and
events routes read it ([query-api](../services/query-api/README.md#earnings-and-events)).
Catalog changes only: the GRANT takes no lock on the table.

### 046_sectors.sql

`046_sectors.sql` (F4): `silver.sic_sectors`, Tributary's own open grouping of the SEC's SIC codes
into 12 sectors and 88 industries (ranges, never overlapping; the judgment calls are in its
header), and the view `silver.company_sectors`.

### 047_business_peers.sql

`047_business_peers.sql`: what the parser image's weekly `parser-peers` job derives from 10-K
Item 1 text: `gold.business_profiles`, `gold.business_peers` (cosine similarity, shared terms) and
`gold.business_mentions` (competitors named, with the sentence's offsets), all under RLS at
`filings` (a peer row needs both companies readable), and `silver.co_registrants()` (SECURITY
DEFINER: query_api still reads no `bronze.filings`), companies filing one combined 10-K (PG&E Corp
and Pacific Gas & Electric).

### 048_bank_metrics.sql

`048_bank_metrics.sql`: a bank's own lines: `net_interest_income`, `noninterest_income`,
`noninterest_expense`, `provision_for_credit_losses`, `loans` (net of the allowance), `deposits`,
`cet1_ratio` (where tagged) and the derived `efficiency_ratio`; its header has the tags measured on
every bank's companyfacts and why net interest margin, tangible book value and ROTCE are left out.
Needs `financials-rebuild`; backout `backout/bank_metrics_048.sql`.

### 049_kpis_cash_flow.sql

`049_kpis_cash_flow.sql` (F5): maps what the analysts asked for that filings tag but gold didn't
read: the cash flow statement's `investing_cash_flow`, `financing_cash_flow`, `change_in_cash` and
its ending `cash_and_restricted_cash` (what the change reconciles to; `cash` stays the balance
sheet's line), `contract_liabilities_current` (customer advances and billings ahead of revenue),
`contract_liabilities` (the total, or current + noncurrent as a 038 sum),
`remaining_performance_obligations` (revenue under contract not yet recognised: the nearest us-gaap
element to a backlog) and `accounts_payable`; plus the derived `cash_and_investments` (cash and
short-term investments by net debt's own reading, so net debt = debt - cash and investments; n/a
for a bank, like net debt) and `gold.figure_facts`, the facts behind one figure for the Query API's
`/components` (SECURITY DEFINER under `authz.readable_companies`, like 036). Tags and coverage
measured on 599 random companies are in its header; on the 613-company sample rebuilt before and
after it, every existing gold row was identical. Needs `financials-rebuild`; backout
`backout/kpis_049.sql`.

### 050_filers_debt_signs.sql

`050_filers_debt_signs.sql` (F7a): reads a filing's facts as the company's whose CIK the filing's
own XBRL names (`bronze.filing_xbrl.entity_cik`) when that CIK filed it: since July 2026 the SEC's
companyfacts files some multi-registrant 10-K/10-Qs only under a co-registrant (DTE Energy's under
DTE Electric, Southern's under Mississippi Power; 91 filings in a year), so their gold had stopped
and nine subsidiaries with gold of their own showed the parent's figures. `gold.usable_facts` takes
those facts from the CIK's company (`lent`) and gives them to the named one (`borrowed`);
`gold.companies_for_ciks` tells the facts loaders which companies a changed payload reaches;
`gold.filed_by` is the filer test. Debt balances read as `abs(value)` like 043's interest expense;
debt parts at 1,000 or 1,000,000 times the same filing's own total are left out (`mistagged_fact`,
rule `debt_parts_scale`: Bank First's 2019 10-K). Stock-based compensation keeps its sign (its
negative quarters reconcile with the filings' year to date: forfeitures, remeasured liability
awards); its `sign_flip` is severity `info`, rule `benefit`, unless another filing reports the
quarter positive. Measurements in its header. Needs `financials-rebuild`; backout
`backout/filers_debt_050.sql`.

### 051_filing_figures.sql

`051_filing_figures.sql`: `gold.filing_figures(company, accession)`: one filing's facts as gold
reads them and the net debt, cash and investments and free cash flow computed from that filing alone
(040's rules), for the thesis check's guard against certifying the app's own figure (Query API
`/v1/companies/{id}/filings/{accession}/figures`). SECURITY DEFINER under
`authz.readable_companies('financials')`, like 036 and 049. No rebuild.

### 052_search_sort_total.sql

`052_search_sort_total.sql`: `gold.search_page`: 032's `search_passages` with an order (`relevance`
or `date`) and a `total` of the matching sections, for the search page's count and "Newest first".
Same SECURITY DEFINER read rule; `search_passages` stays for an API pod from before it. Catalog
only: no table is locked.

### 053_segment_same_basis.sql

`053_segment_same_basis.sql`: replaces `gold.refresh_segment_financials` (018's body + edits marked
"053"): a segment quarter derived as a year-to-date difference (Q4 = FY - 9 months) whose later
value was restated after it was first reported takes that value from the first filing on or after
the earlier value's filing, so both come from one reporting cycle and one segment basis (JPMorgan's
FY2022 Q4 CIB was its FY2024 10-K's recast year, with Commercial Banking, less the old nine months:
$22.13B beside $12B quarters, and the TTM fell 10% when it left the window). Measured on
production's segment facts of JPM, NVDA, DECK and VRT, both bodies run on a throwaway copy: 13 of
1,034 quarters change (and the TTMs over them), each now in line with its neighbours and the
reported totals: JPM CIB FY2022 Q4 $22.13B -> $10.60B; NVIDIA's FY2024 geography TTMs now equal its
FY2024 10-K (US $26.966B, Taiwan $13.405B, China $10.306B) and its FY2024/FY2025 Q4 "Corporate"
operating income -$1.35B/-$1.84B instead of $41.4B/$92.6B; Vertiv's FY2021 Q4 regions sum to $1,411M
against $1,410M of revenue (was $1,580M) and its FY2020 Q4 products/services to $1,020M/$285M (was
$780M/$525M); Deckers unchanged. A year reported once differently but later as first reported
(Vertiv's FY2023 10-K on FY2022) isn't "restated": the latest value, as before. Needs the segments
refresh after the deploy (`gold.refresh_segment_financials` over every company in
`gold.segment_financials`, in batches, or `make segments-rebuild`, which also re-projects the stored
extracts), then `ops.check_segments`; backout `backout/segments_same_basis_053.sql`. Catalog only.

### 054_insider_transactions.sql

`054_insider_transactions.sql`: insider filings: `bronze.ownership_documents` (each Form 3/4/5's
ownershipDocument XML as filed, from doc-fetcher), `silver.insider_filings` (issuer company, form,
period, reporting owners with their relationship as jsonb) and `silver.insider_transactions` (each
line of the non-derivative and derivative tables in document order), filled by
`silver.refresh_insider_filings(accessions)`: XMLTABLE in Postgres, delete then insert, idempotent on
the accession; a document that isn't well formed XML, or whose issuer isn't a company here, gets no
silver rows. Both silver tables: GRANT SELECT to query_api, RLS `granted_filings` on company_id
(030's form). New tables and functions only: nothing existing is locked. Volume and cost in the
doc-fetcher README ("Insider filings").

### 055_filing_xbrl_facts.sql

`055_filing_xbrl_facts.sql` (F8a): reads a filing's own XBRL where SEC companyfacts doesn't carry
it: the filings companyfacts never received (52 of 12,006 10-K/10-Qs in 200 days: Citigroup's 2026
10-Qs, Clorox's FY2026 10-K), a company's own tags proven to be a standard line (NVIDIA's 10-K capex
tag, FY2016-22) and balance sheet lines filed only by segment member (Ford's and GM's debt, summed
where the members are lines of the consolidated balance sheet). `silver.financial_facts` gains
`taxonomy` and `source` (`companyfacts`, `filing`: the filing's own XBRL while companyfacts lacks it,
`filing_extra`: what companyfacts never carries); `silver.metric_tags` gains `company_id` (a
company's own tag) and a primary key with the taxonomy; `silver.member_sums` names each sum.
`bronze.filing_instances` keeps instance documents fetched from EDGAR, `bronze.filing_facts` each
filing's facts as read (with where from), `silver.refresh_filing_facts` maps them into silver,
`silver.filing_facts_caught_up` and `silver.supersede_filing_facts` hand a filing back to
companyfacts once it has it, so a filing's facts are never in silver twice. `gold.usable_facts`
joins tags by taxonomy and company. Filled by facts-fetcher's `facts-fetcher-filings`
([README](../services/facts-fetcher/README.md), "Filings companyfacts lacks"); measurements and the
proofs in its header. Locks: two ADD COLUMNs with constant defaults and a NOT VALID CHECK on
`silver.financial_facts` in one statement (no rewrite, an instant of ACCESS EXCLUSIVE; lock_timeout
3 s), the primary key of the 122-row `silver.metric_tags` rebuilt. Backout
`backout/filing_xbrl_facts_055.sql`.

### 056_ifrs_currencies.sql

`056_ifrs_currencies.sql` (F8a): reads foreign filers: 984 of the 6,066 listed companies had no
financials, 525 of them 20-F/40-F filers whose companyfacts carry ifrs-full tags (486) or US GAAP in
their own currency, which silver never read (us-gaap only, USD only). `silver.metric_tags` gains the
core IFRS tags (revenue, operating profit, profit, EPS, assets, equity, cash, borrowings, operating
cash flow, capex...: counts and the borrowings evidence in its header), after each metric's us-gaap
ones. `silver.unit_fits` lets silver keep an amount in any ISO currency; `gold.usable_facts` reads
each company in one currency, its reporting currency (the one most of the monetary facts of its
latest report are in, so a 20-F's convenience translation drops out), and computed amounts take it
too. `gold.latest_reading` gives an amount in another currency the status `other_currency` with its
`unit` (DROP and CREATE: one more column), so the screener, size bands and the web's medians never
put it beside dollars; ratios compare as before. A year or a half-year is never split into quarters
(gold derives a quarter only from year-to-date figures 80-120 days apart): a 20-F filer has fiscal
years (040's stated-year TTM) and year-end balances; one whose 6-Ks tag quarters (Birkenstock) has
them. On the 517 such companies with a payload, rebuilt locally from production's payloads: 490 get
gold (revenue 426, net income 487, total assets 484, operating cash flow 477), 113 of them with
quarters since 2024, none with two currencies; about 200 companies with gold today (Alibaba, Toyota,
ASML, Canadian National...) move from scraps of convenience-translated dollars to their own
currency. Every gold row of the 46-company replica (all USD) was identical. Needs the
financials-rebuild job (re-projects the stored payloads with the IFRS tags, no SEC requests).
Catalog changes and small inserts only. Backout `backout/ifrs_currencies_056.sql`.

### 057_currency_readings.sql

`057_currency_readings.sql` (F8b): reads the ~700 companies in their own currency where 056 still
read dollars. `gold.amount_currency(company)`: its amounts' currency (the latest revenue row's unit,
else net income's, else USD; 060 adds annual-only filers). 035's not-meaningful rules gain a currency
(`gold.not_meaningful`'s fifth argument): the absolute bases (revenue under $100K, growth over 1,000%
from under $10M) apply only to dollar amounts, the ratio bounds to every currency; the four-argument
form reads as dollars (an older Query API). Why not per-currency equivalents: there are no exchange
rates here, a fixed table would drift unnoticed, and the bases exist for shells and pre-revenue
filers, which file in dollars. `gold.latest_reading` passes the base's currency (one PK probe, six
metrics only; still inlinable). `gold.filing_figures` (051) returns each figure's `unit` (DROP and
CREATE, 051's grants). `ops.check_financials` (050's body) compares a debt fact with liabilities and
assets in its own unit (a 20-F's convenience dollars beside yen debt read as a scale slip). Catalog
only, no rebuild; findings change at each company's next check. Backout
`backout/currency_readings_057.sql` (tested: functions equal to a database at 056).

### 058_bank_holding_companies.sql

`058_bank_holding_companies.sql` (owner's decision): adds Charles Schwab, American Express,
Synchrony and Ally to the banks by company id in `gold.not_applicable_metrics` (049's body
otherwise): bank holding companies the SEC files under brokers (6211) or finance services (6199),
SICs whose other filers aren't banks (Ally's 6022 already counted; listed so a SIC change can't drop
it). That function is the only list of banks: readings, the screener's `not_applicable`, the bank
template and peers' bank columns all read it. Their next gold refresh drops the fallback debt tag
(`LongTermDebt`) at a bank (039's rule), which is the only debt tag American Express and Synchrony
use: long-term debt leaves their gold rows, as JPMorgan's did. Catalog only (one function, nothing
locked on a table); backout `backout/banks_058.sql`.

### 059_fetch_outbox_insider_queue.sql

`059_fetch_outbox_insider_queue.sql` is doc-fetcher's: `ops.document_outbox` (a live document's
`documents.fetched` event is owed until Kafka confirms it; written in the document's own
transaction; the crawl sends no `documents.backfill` for a document the live side announces, sent or
owed), `ops.insider_queue` (feed Forms 3/4/5 waiting for an idle consumer, or kept as `failed` with
the error: the live queue leaves those, the daily catch-up retakes them once a run; a transient
error never fails one) and `ops.insider_findings` (what parsing one noticed: `bad_date`,
`several_values`, `not_well_formed`, `parse_failed`). `silver.xml_date` returns NULL for a date that
doesn't exist (it raised, and one bad Form 4 crash-looped the live consumer);
`silver.refresh_insider_filings` (054's body, edits marked "059") takes the first node of every
column path (a repeated element raised) and records both as findings. No FK to
`bronze.filing_documents` (the nightly slim dump). Locks: three new tables and two replaced
functions only (054's, used by doc-fetcher alone). Backout `backout/insider_queue_059.sql`.

### 060_annual_filer_currency.sql

`060_annual_filer_currency.sql`: `gold.amount_currency` also reads a company's latest TTM revenue,
then net income, in `gold.derived_financials` when it has no quarterly one: a 20-F or 40-F filer's
revenue and net income are only its stated years there, so 057 read Toyota's yen or a euro filer as
dollars and gave its readings the dollar bases. `gold.latest_reading` takes its base's currency from
`gold.amount_currency` (057's body otherwise). Locks: two functions replaced, a catalog change only
(no table locked or read, no rebuild); backout `backout/annual_currency_060.sql`.

### 061_screener_currency_inline.sql

`061_screener_currency_inline.sql`: the same currency rule, run so the screener can afford it. 060's
`gold.amount_currency` is a scalar SQL function of sub-selects, which Postgres never inlines, so
`gold.latest_reading` ran it as a separate query per company and column, and as `query_api` each run
rebuilt the RLS policy's subplan over the catalog's ids (1.5 ms a call, against 0.02 ms as the
owner). Production's margin and growth sorts hit the 5 s timeout; in a generic plan (the pool
prepares a statement after five runs) the metric test ran after the call, so every value sort paid
it. Now the rule is `gold.amount_currency_of(company)`, a one-row set-returning function the planner
inlines; `gold.amount_currency` returns its row and `latest_reading`'s `cu` reads it in FROM behind
`LIMIT 1` (the metric test stays a one-time filter). Measured as `query_api` with the catalog open
on a catalog-sized copy (production's 8,081 companies, each of the 6,594 with gold given one of 529
sampled companies' full gold rows: 6.62M quarterly, 7.24M derived), 126 sorts of 50 rows with the
default columns: margin and growth sorts 9.5-18 s -> 0.33-0.37 s, every other sort 0.4-0.9 s ->
0.17-0.40 s, the same with generic plans (060: 10.5 s for any value sort); rows, values, sources,
readings and order identical for all 126. Locks: one function created, two replaced, catalog only
(no rebuild); backout `backout/currency_speed_061.sql`.

### 062_segment_refresh_speed.sql

`062_segment_refresh_speed.sql`: `gold.refresh_segment_financials` (053's body, edits marked "062")
in time linear in the companies of a call. Its CTEs have no index, and hermes plans `keyed` at one
row, so 018's `matches` self-join and `members` sub-select and 053's first-reported and same-cycle
lookups were nested loops over the whole call's rows: one company of 3,865 facts 4.0 s, 25 companies
83 s, 50 over 11 minutes (the hourly segments job ran one call for 2.5+ hours on 2026-11-09). Now one
company at a time, the canonical member and the first reported value as windows, `matches` from
fact counts per value, the merged QNames from one GROUP BY, and the same-cycle row as a LATERAL that
only a restated value runs. Measured on production's segment facts of 222 companies (262,473 facts):
053 95 s one call per company, 062 5.8 s in one call (6.0 s with nested loops only), gold identical
(135,229 rows, every column). Locks: one function replaced, catalog only; no refresh needed; backout
`backout/segments_speed_062.sql` (053's body), before 053's.

### 063_rebuild_speed.sql

`063_rebuild_speed.sql`: two slow parts of the financials rebuild (2 h 05 min on 2026-11-09 against 36
minutes before 038-060). Measured on four of production's rebuild batches (200 CIKs, 73 companies,
483,143 silver facts), rebuilt step by step: 037's functions 10.7 s, 060's 32.6 s (silver 14.3,
quarters 6.6, derived 7.7, checks 4.0), 063's 24.6 s. `silver.refresh_financial_facts` read
`payload->'facts'->...` once per metric tag, detoasting each payload 174 times since 056's IFRS tags;
it now reads the facts object once per CIK (silver 14.3 -> 7.1 s). `gold.refresh_derived_financials`:
040's "of two stated years within 7 days, one" was a NOT EXISTS over the `years` CTE, a nested loop
over the batch's years; now a window over ends within 7 days (derived 7.7 -> 6.8 s). Silver, gold
quarters, derived rows and findings identical. Left, measured: `gold.best_facts` (039) runs three
times a batch (quarters, derived's stated years, the checks' annual comparison), about 1 s each for
JPMorgan's batch, so the checks went 0.24 s -> 1.5 s and derived 1.05 s -> 2.7 s; 031's overlap test
pairs every quarter of a metric within a company (1.1 s of that batch's 2.8 s of quarters); and the
planner estimates usable_facts' `tagged` at one row, so CTE joins over it are nested loops and a
plan can flip after a mass delete and insert (one batch went 0.6 s -> 7 s until ANALYZE). Locks:
two functions replaced, catalog only; no rebuild; backout `backout/rebuild_speed_063.sql`.

### 064_revenue_net_of_interest_part.sql

`064_revenue_net_of_interest_part.sql`: two revenue rules in `gold.best_facts` (039's body, edits
marked "064"). A net revenue part: `RevenuesNetOfInterestExpense` under 20% of the period's largest
`Revenues` / contract revenue is a part, sorted after the other tags like 039's Revenues part.
Revenue's first tag had no such rule, so a small line filed under it beside the total was believed:
Rectitude Holdings' FY2025 S$74,328 beside Revenues of S$43.8M put its revenue growth at the top of
the screener (+68,870%). 20% as in 039: a broker's or lender's net revenue really is a fraction of its
gross (Nomura 36-49%, Atlanticus 27-48%, Oportun 23-50%, Jefferies 42%). A scale slip: two revenue
tags of a period 1,000 or 1,000,000 times apart (within 0.5%) are one figure in two scales; the larger
is the slip when it is over 100 times every other money figure of that period, else the smaller is.
A slip is no evidence for the zero and part rules and sorts after every other tag. Either side can be
wrong (Tigo Energy's contract tag 1,000x too large; Plexus', FactSet's, CHS' second tag in thousands),
so the rest of the period decides; without it the net revenue rule handed Tigo's 2023 to its $50B
tag. Measured on hermes over the 128 companies with the net tag and the 41 with such a pair: 184
picks change at 24 companies, nothing else (StoneX 109: its net operating revenues, 0.2-4.8% of the
total revenues data vendors show, give way to them, TTM to June 2026 $2.86B -> $157.7B; Plexus 15:
quarters read in thousands since late 2023, TTM $4.03B -> $4.60B; Tigo 14: every 2022-2026 quarter its
$9-69M Revenues, TTM $103.5B -> $104.7M). Rebuilt from those companies' silver on a throwaway database
(gold at 063 equal to hermes'): only revenue rows and the margins and growth read from them change;
Rectitude's latest growth +17.1%. Left, in the header: Oportun's Q1 2023 (a real $4.8M net revenue)
now reads total revenue. best_facts' cost on hermes unchanged within noise (one more window sort).
Locks: one function replaced, catalog only; the `financials-rebuild` job for those companies' CIKs;
backout `backout/revenue_part_064.sql` (039's body).

### 065_search_exact_phrase.sql

`065_search_exact_phrase.sql`: a quoted phrase matches its exact words (analyst test 4: "liquid
cooling" in quotes matched "liquid-cooled", the english stemmer's 'liquid' <-> 'cool'). A ten-argument
`gold.search_page` (052's + `p_exact`, a 'simple' tsquery of the query's quoted phrases the Query API
builds, `store.exact_phrases`): a passage matches only when its own text, read with 'simple' (no
stemming; a hyphenated "liquid-cooling" still is the same words), matches too. NULL is 052's search;
the clause is a fixed string. Tested before DISTINCT ON and the counts, so `total` stays exact.
Cost on hermes: "liquid cooling" over every company 1,313 stemmed matches, 765 exact, 23 ms -> 651
ms (about 0.4 ms a passage, mostly to_tsvector); a common phrase over 30 days ("supply chain", 8,152
matches) adds about 4.4 s to a search already past SEARCH_TIMEOUT. Same SECURITY DEFINER read rule
and grants; the nine-argument form stays for an API pod from before it. Catalog only, no rebuild.

### 066_company_search_classified_first.sql

`066_company_search_classified_first.sql`: `silver.search_companies` = 044's body with one key before
the name's length: a company with a SIC code first (analyst test 4: "Goldman Sachs" listed Goldman
Sachs BDC, no code, above The Goldman Sachs Group, 6211, on the shorter name). Public identity only,
never a value. Measured on hermes over 2,285 queries (the first two words of every large accelerated
filer's name and the report's cases): the first result changes for "Goldman Sachs" and "Blue Owl"
(Blue Owl Capital Inc., the manager, above its BDC) only. Catalog only.

### 067_filing_tables.sql

`067_filing_tables.sql` (parser v7, rule 11): `silver.filing_tables`, one row per HTML table of a
parsed section (`rows` jsonb: each row's [start, end) in the section's text, its cells in order,
`spans` when a cell spans columns, `head` on a `<thead>`/`<th>` row), written by both parsers with
the sections; FK to `silver.filing_sections` ON DELETE CASCADE (a re-parse that changes a section
drops its tables with it); RLS as `silver.filing_sections` (030's form), GRANT to query_api; in
pg-backup.sh's slim excludes. The text is unchanged: no offset moves. About 15 GB of JSON for every
stored filing before TOAST (a sample of 335 files: 37% of 10-K/10-Q section text). Locks: the FK takes
SHARE ROW EXCLUSIVE on `silver.filing_sections` for an instant (lock_timeout 5 s).

### 068_search_page_furniture.sql

`068_search_page_furniture.sql`: search without page furniture (analyst test 4; parser README "Page
furniture" and "Search without page furniture"). `gold.page_furniture_lines(text)` (the rule, the
web's `reader/furniture.ts` copy checked on the same cases), `gold.page_furniture(text)` (spans),
`gold.blank(text, spans)` (spaces one for one: offsets stay); `gold.refresh_section_search` blanks a
section's furniture before `to_tsvector` and stores each passage's spans
(`gold.section_search.furniture`; NULL before 068), so a phrase across a page break matches; the
ten-argument `gold.search_page` checks exact phrases on the blanked passage; the Query API blanks
snippets. `ops.section_index.passages_rule` marks passages built so; `parser-index --passages`
rebuilds the others without touching diffs. Locks: two ADD COLUMNs without default (catalog only)
take ACCESS EXCLUSIVE on `gold.section_search` and `ops.section_index` for an instant (lock_timeout
3 s: deploy when no `section-index` Job runs). Nothing stored changes.

### 069_readings_not_computed_stale.sql

`069_readings_not_computed_stale.sql` (Riley, test 6): two readings beside 035's. `gold.not_computed(metric,
sic)` says why a blank is blank where Tributary computes none for the company's industry (039's
computed gross profit leaves out 5331, 48xx, 58xx, 5912, 13xx/2911, 4911/493x, 60-67 and no SIC;
test_readings_db keeps the two lists equal); `gold.latest_reading` reads `not_computed` (no value, or an
old one) and `stale`: the company's latest period more than 270 days before today for a quarterly
filer, 500 for an annual one (a 20-F, 40-F or 6-K reading, or no quarterly revenue or net income within
200 days of its latest period; owner decision 4). On hermes (2026-11-15) 501 of 6,617 companies read
stale (209 quarterly, 292 annual: most are 20-F filers whose 2025 annual report hasn't reached
companyfacts' payload, TSMC among them). The test runs only past 270 days (a one-time filter): the
same 198 ms as without it for every company's latest period on hermes. "Today" is `current_date`
unless the session sets `tributary.today` (the DB tests pin it). Function-only, no rebuild; backout
`backout/readings_069.sql`.

### 070_check_segments_overlap.sql

`070_check_segments_overlap.sql` (Riley, test 6, finding 8): `ops.check_segments` = 037's body + one edit:
a quarter whose members don't add up is flagged only when no set of them makes the consolidated figure
within 0.5% (`ops.segment_cover(parts, target, tolerance)`, a pruned depth-first search with a 200,000-step
budget; positive parts only, else NULL and 037's rule). Overlap (Microsoft's two complete cuts, 100.0%
more; NVIDIA's Data Center beside Hyperscale and AI Clouds; Costco's Product beside two categories) is
not a deviation; the web's `memberTree` (chart/segments.ts) shows it as children and other cuts. Over
hermes' 38,461 stored findings (2026-11-15): 11,430 go, 15,764 stay, 11,267 have a negative member and
keep 037's rule. Stored findings change when the check runs again (`SELECT ops.check_segments(array_agg(
DISTINCT company_id)) FROM gold.segment_financials;`). No backout file (037's body undoes it).

### 071_auto_catchup.sql

`071_auto_catchup.sql`: stored data upgrades itself after a rule change, so a deploy needs no reparse,
rebuild, passages or check Job by hand. Each kind's current version lives with its rule
(`PARSER_VERSION`, `SEARCH_RULE`, `diff.ALGORITHM` in the parser image; `gold.rules_version()` 64,
`ops.check_financials_version()` 57, `ops.check_segments_version()` 70 here) and each item records the
version that built it (`silver.filing_sections.parser_version`, `ops.section_index.passages_rule` /
`algorithm`, and the new `ops.company_builds`, stamped by `gold.refresh_financials`,
`gold.refresh_segments` and `ops.recheck`). Gold's version is a number a migration bumps, not a hash of
the function bodies: a speed-only replace (063) must not rebuild everything, and a backout's guards
already pin the bodies. A company without a stamp counts as built with `ops.catchup.baseline` (this
migration's versions), so the first deploy needs no backfill and finds nothing behind: the last full
rebuild ran 2026-11-09, 064's canary covered its companies, and 070's segment re-check had run (27,168
segment_sum findings on 2026-11-16, 070's expected ~27,000). The parser and passages kinds are behind
from the start: 595,775 documents and 433,157 filings on 2026-11-16. The parser kind ships paused
(`ops.catchup.paused`): eight days of re-parsing is the owner's call; it is measured hourly meanwhile,
shows `paused` in the status view, and the alert ignores it.

`gold.lock_companies`: the catch-up rebuilds companies while the nightly `financials`, `financials-bulk`
and `filing-facts` refresh theirs, and 014's refresh is a DELETE then INSERT per company: two
transactions refreshing one company would collide on the primary key, or leave findings computed from
the old rows. Each refresh takes an exclusive transaction lock per company (sorted, so no deadlock),
under a shared lock that a call of over 1,000 companies takes exclusively instead (the lock table holds
64 x 100 entries on hermes). ops.recheck('check_financials') takes the same locks.

`ops.parser_catchup` and `ops.section_index_pending`'s new order: services/parser DESIGN.md, "Catch-up".
The catch-up runs in the hourly `section-index` and `segments` jobs (deploy/helm/tributary DESIGN.md),
records a row per kind and run in `ops.catchup_runs` (30 days), and `ops.catchup_status` reads the
latest row, the last day's pace and an ETA; the `CatchupStalled` alert fires when a kind with work left
hasn't moved in a day. Locks: new tables and functions; three functions replaced (catalog only).

### 072_data_trust_checks.sql

`072_data_trust_checks.sql` (data trust, part 1): nightly checks that find wrong numbers before readers
do, and check results that say what they mean. Its header has the rules and the production counts.

- `tag_disagreement` (`ops.check_financials`, warning): the filing gold reads a figure from tags the same
  line another way and the two can't both be right: gold's tag a total under a fifth of a part of it
  (`ops.tag_relations` 'part'), two names of one line 5x apart ('same'), or any two tags 1,000 or
  1,000,000 times apart (revenue's are 064's). A bare "two tags 5x apart" found 84,000 pairs, almost all
  normal sub-totals; the relations keep only lines that can't exceed gold's. Detail names both tags and
  the factor, never the values (014's rule).
- Tiers (`ops.quality_tier`): error, warning, kept_out (`mistagged_fact`, now info), coverage
  (`series_gap`, `net_debt_gap`, now info), info (restatements; outliers on lumpy lines, rule `lumpy`;
  annual sums whose year or quarter was restated, `restated`). `ops.quality_companies.figures` counts
  each company's gold rows at its check, for findings per 1,000 figures in the new view
  `ops.quality_tiers` (`ops.public_quality` reads it; `ops.quality_summary` keeps its six columns, which
  the 038-041 backout replaces, one row per check and severity now).
- `ops.check_segments`: two cuts beside a zero or a negative member (NVIDIA's 2019 quarters) overlap
  too: zeros drop out and the negative members count once in every set (070's search over the
  positive members for the figure less the negative ones). 3,125 of 27,170 stored findings go; NVIDIA
  keeps 13 of 19, real (two bases mixed in derived Q4s, missing geography members, unallocated costs).
- Parser-output checks (`ops.check_sections`, `ops.section_findings`, `ops.section_checks`): keys the
  form has, core items, empty and repeated sections, stored documents with none; twins of
  services/parser `invariants.py`. Run by section-index for what it indexes and by the catch-up (kind
  `check_sections`) for every stored document once per `ops.check_sections_version()`. First counts:
  6,973 of 634,653 documents with an invalid key, 17,591 missing a core item (mostly 10-Q Part I Item 1).
- Extremes (`ops.refresh_extremes`, `ops.extremes`, `ops.extremes_report` for ops_reader): the screener's
  top and bottom 20 per column with what explains each (a finding, a derived quarter, or `in_line`: the
  company's own previous figure within 3x); 1,036 of 4,720 rows to review on hermes. The nightly
  financials-bulk job refreshes it.

Both company checks' versions go to 72, so the hourly catch-up re-checks every company; nothing runs by
hand. No backout file: 057's `ops.check_financials`, 070's `ops.check_segments` and versions 57/70 undo
the checks (the chain's guards pin 072's bodies).

### 073_member_picks_cet1.sql

Member picks: a company files a line only with dimensions where others file it undimensioned.
`silver.member_picks` names, per company, a concept and the exact set of dimensions to read (every axis
listed, no other); the filing-facts job projects those facts and `silver.refresh_filing_facts` maps them
(`filing_extra`, silver tag spelling out the members, 055's convention). First use: CET1 for JPMorgan
(its own concept; standardized, parent; the 2018-19 transitional and fully phased-in names after it),
Bank of America (10-K only) and Wells Fargo (10-Qs). A pick reaches hermes by itself: the job's extras
pass reads every stored document of a company with a pick, and again any projected before its newest
pick (`added_at`); no rules_version bump (055's precedent: only these companies' facts change).

### 074_statement_reconciliation_confidence.sql

Data trust, part 2. An independent source and a mark per company.

- Statement lines: parser-index reads each 10-K's and 10-Q's v7 tables with `filing_parser/statements.py`
  (its docstring is the spec: columns by spans and head rows, the current period, the scale words,
  four headline lines by label) into `silver.statement_reads` / `statement_lines`. Every candidate is
  kept (a line prints in the statement and in MD&A); agreement with any counts.
- `statement_mismatch` (warning, "to double-check"): gold's figure, one the filing itself states, isn't
  what its own statement prints, and the print is unambiguous (titled, in the statements' home, alone
  in its row). A later filing's restated figure is never compared with the original. On hermes'
  300 parsed 10-K/10-Qs: 4 findings in 367 comparisons, two gold's side (a ProfitLoss TTM, an XBRL net
  income unlike the print) and two the reader's; the rest agree or were restated since.
- `gold.company_confidence`: high / medium / low and its reasons in words, read time (staleness moves
  with today), from inputs the check stores in `ops.quality_companies` (statement counts, core figures,
  entity kind) and 072's errors, warnings and figures. Low for shells, commodity pools, royalty trusts,
  BDCs, errors at 2+ per 1,000 figures, warnings at 25+, no report for 500 days, at most two core
  figures; medium for any error, warnings at 10+, a statement mismatch (not low: half are the
  reader's), another currency, figures over 270 days old, three or four core figures. On hermes:
  59% high, 25% medium, 16% low.
- Catch-up: `ops.check_financials_version()` 74; a filing read after a company's last check makes the
  check due (`ops.company_backlog`); parser-index's catch-up kind `statements` reads every stored
  10-K/10-Q with tables once per `statements.READER`.

### 075_disagreeing_figures.sql

`gold.disagreeing_figures`: the two figures behind a `tag_disagreement` note, read from silver when the
note is read, as the user (DEFINER, `authz.readable_companies('financials')` its only rule:
`figure_facts`' pattern). Findings, ops views and the status page stay figure-free (014); a figure silver
no longer holds reads as "corrected since".

### 076_coverage.sql

The coverage sweep: no more unknown gaps. Every exchange-listed company (Nasdaq, NYSE, CBOE, TXSE) is
measured each night, after the financials-bulk job's extremes report, for each kind of gap; each row says
why: `fixable` (Tributary's: a link, a mapping, a pipeline step), `not_in_sec_data` (no XBRL, no reports,
a bank that files with its regulator) or `out_of_scope` (funds, asset-backed issuers, a subsidiary filed
in its parent's combined report). Kinds: no figures, figures behind the latest report (domestic and
foreign), history cut short (re-registration, revenue under a tag gold doesn't read, companyfacts
starting late), a core metric missing lately, an unlinked re-registration, documents missing, 10-K
statements in an unstored Exhibit 13. `ops.coverage_found()` is the read-only rule (the whole sweep 36 s
on hermes), `ops.refresh_coverage()` applies it (first_seen kept while a gap lasts). Readers: the
company's data notes (`coverage`, RLS financials), the status page (counts per kind and reason via
`ops.public_coverage_gaps()`), `make coverage` (ops). `ops.rebuild_soon` drops a company's gold build
stamp when the company-loader links a CIK to it, so the catch-up rebuilds it first.

### 077_net_income_one_line.sql

Net income from one line. `NetIncomeLossAvailableToCommonStockholdersBasic` is the last fallback
(Booking's 10-Ks and 10-Qs since 2016; the same figure without preferred stock), and inside a year whose
own filings tag net income ProfitLoss only (NetIncomeLoss only in later filings' comparatives), ProfitLoss
reads first, so the year's quarters, year-to-date figures and derived Q4 never mix the two (PNC's 2025:
7,015 against the printed 6,997, now 6,997; on hermes 417 periods at 129 companies hold both tags in such
years, 69 more than 10% apart). A coverage-majority rule was tried first and dropped: Kroger, Prologis and Duke tag
ProfitLoss in more periods than NetIncomeLoss and print the attributable figure. `gold.rules_version()`
77: the catch-up rebuilds every company.

### 078_screener_figures.sql

The screener's latest figures, stored by the gold build (Riley, test 6: a cold first request 2.6 s,
filter changes 5-10 s). A screen sorted or filtered on a value read, per company, its latest period
(three probes), whether it has any value (hide_blank, two or three probes), financials coverage (one
probe) and the column's latest reading (two probes into 12 GB of gold, plus currency and equity): tens
of thousands of pages from disk when cold. Measured on hermes (2026-11-17, owner login, jit off): the
default screen 4.8 s on its first run, 0.46 s the second (47,775 pages read); two filters and a growth
sort 1.7 s / 0.72 s (63,664).

`gold.screen_companies` (per company: `gold.latest_period`, the latest equity, the amounts' currency,
the recent-quarters flag, which metrics have rows, `until`) and `gold.screen_figures` (per company,
span and metric: `gold.latest_row`, with the inputs) hold only data. The readings are read time:
`gold.reading` (069's two CASEs over inputs) is now what `gold.latest_reading` calls too, so the rules
are written once; stale uses today's date when read; the SIC is the reader's; RLS is 030's policy.
What depends on the date (the `<= current_date` filters of latest period and equity) ends at
`until`, the first future period end of those metrics: from that day the company is read live until
the catch-up refreshes it. `gold.screen_company` and `gold.screen_reading` read stored inputs, or
live (`gold.latest_period`, `gold.latest_reading`) for a company without a row; both are inlined.
`screen_figures`' primary key INCLUDEs every column a screen reads: a sort over one column is an
index-only range of its ~6,600 entries, whatever the heap's order. The screener's coverage test reads
the stored metric lists first; `gold.financials_coverage` itself is unchanged (the public catalog's
membership reads it: access never depends on a cache).

Kept current: `gold.refresh_financials` calls `gold.refresh_screen` for the companies it builds, under
the same locks, in the same transaction. The tables ship empty (a build of the previous body running
at the deploy writes nothing, so nothing stored can be older than gold); the `segments` job's
catch-up fills them first (`gold.refresh_screen_backlog`, 200 companies a transaction; 6,600 synthetic
companies took 20 s). Proved equal: test_screen_figures_db.py (every column of every fixture company
at four dates, whole screens stored and live); on hermes, read-only, the stored path inlined against
`gold.latest_reading` for 564 companies x 118 columns: 0 differences in 66,552 readings (every status
present). On a catalog-sized copy (6,600 companies, 805,200 stored figures) as query_api: a cold
default screen reads 2,521 pages instead of 21,066 (149 ms against 337 ms); warm 137 ms against
242 ms. A change to what is stored (latest_row, latest_equity, quarterly_recent, amount_currency_of,
metric kinds) TRUNCATEs both tables in its migration.

### 079_catchup_status_cleanups.sql

Catch-up status: `ops.catchup_runs.note` and `ops.catchup_skipped`: a run locked out by a hand-run job
records `skipped: lock held` with the last count (measured when that count is of another version); a
skip counts as progress. The first run of a new version records its backlog at its start (both
catch-ups, Python). The parser-output checks' backlog was a LEFT JOIN with an OR, estimated at one row
from stale statistics: a nested loop over company_ciks per document, 149 s per batch on hermes, so
4,000 documents a run (6.5 days for 630,653); as NOT EXISTS 0.17 s; `ops.statement_backlog` likewise.
`gold.search_passages` and the nine-argument `gold.search_page` dropped: nothing calls them since 065.

### 080_revenue_industry_tags.sql

Revenue under industry tags (coverage, part 2). Before 2018 many filers tagged their total revenue with
an industry line: `SalesRevenueGoodsNet` (manufacturers' net sales), `SalesRevenueServicesNet`,
`RealEstateRevenueNet` (REITs); 639 listed companies' revenue started a year or more after their net
income because gold read none of them. Each candidate was measured by 038's method over the raw
companyfacts of four samples (the 639, 500 drawn at random, the 124 with a metric missing lately, 91
partnerships): where a filing without the tag gives the same period under a tag gold trusts, is the
tag that total? Goods 92.5% of 1,242 periods (a part 0.9%), services 85.9% of 562 (2.7%), real estate
90.9% of 274 (2.9%), against 89.4% of 963 (1.2%) for `SalesRevenueNet`, trusted since 003; the
printed statements read so far agree (goods 79 of 83). Contracts, licenses, oil and gas, health care,
royalties, hotels and REIT lease income measured worse or too thinly and stay out. The three are
priority 11-13, after every revenue tag, and `gold.best_facts` keeps them from being read as a part:
never at a company whose filings give one under 80% of another revenue tag of the same period, never
two in one period, never at or below zero; and another revenue tag under 20% of one is a part (039's
rule): Conagra's FY2012-2016 revenue was a $1.6-1.7B line beside $13.5B of net sales, Materion's
2012-2016 quarters $1.5-21M beside $250-300M, Camden's Q2 2018 $1.8M of contract revenue. Total
assets falls back to `LiabilitiesAndStockholdersEquity` (equal to Assets in 99.96% of 59,863 filings'
dates; SEI Investments stopped tagging Assets in 2025). Rebuilt on a throwaway database from the
samples' stored companyfacts (gold at 077 equal to hermes' for the 639): 240 of the 639 get their revenue
history back (5,979 quarters; Consumer Staples 31 of 37, Consumer Discretionary 45 of 60, Industrials 53
of 93, Real Estate 33 of 56, Health Care 16 of 191, mostly biotechs with no revenue yet); 95 existing
quarters change at 10 companies, each a zero or a part replaced (Churchill Downs' 2012-2016 quarters of
0); 2 move series (Aytu); the random 500 gain 70 quarters, none changed. `silver.industry_total_exclusions`
(the owner's call) keeps three companies out: Brown-Forman's and Constellation's pre-2017 goods tag is
sales before excise taxes (tag less ExciseAndSalesTaxes equals the later net sales; the only excise cases
among the 25 whose industry total exceeds the later total by 5%+), and Skyline Champion's is legacy
Skyline's, the registrant, not Champion, the accounting acquirer.
`gold.rules_version()` 80: the catch-up rebuilds every company.

### 081_coverage_notes.sql

The coverage sweep's notes say what is left after 080. history_short `revenue_yearly` (not in the
SEC's data): gold reads the company's revenue for years, from the 10-Ks, 400+ days before its first
quarter, because its 10-Qs tag none (70 listed companies on hermes before 080). metric_missing's
`gold_dropped` counts only silver facts under a tag gold reads as the line (a bank sum's parts aren't:
41 -> 5); `trust` (SIC 6221 commodity pools: net assets, no balance sheet totals or cash flow
statement; out of scope, 13); `partners_capital` (a partnership's or LLC's equity, 8; 080 measured
partners' capital as not safe to read as stockholders' equity). Words: web `company/coverage.ts`.

### 082_sector_hvac_controls.sql

Trane read as Technology (Riley 43): its SIC is 3822 (HVAC and building controls), inside 046's
3820-3825 instruments range; it joins 3585's Industrials, Building & office products (the one
registrant at 3822). Thermo Fisher stays in Technology: its SIC, 3829, also holds Rockwell Automation,
Trimble, Onto, Mirion, Geospace and two more instrument makers; 3826 (lab instruments) was already
Health Care. A SIC range can't hold one company apart.

### 083_gross_sales_revenue.sql

A trading venue's gross sales as revenue (Riley 42: Bullish, $244.81B against $244.73B of cost, ranked
among the largest US companies). Revenue reads `not_meaningful` when its cost of revenue at the same
period end is over 97% of it and at most 100% (a gross loss is revenue earned: 15 miners and micro-caps
at 6199), at SIC 6199 or 62xx (`gold.gross_sales_sic`; real estate's home sellers and insurance agents
measured and left out). On hermes: Bullish, SOS, eToro, The9 and Coincheck (the last two in another
currency, which reads first); 97%, the owner's, spares Cango, a miner at 95.9%. gold.reading takes inputs, not probes, so
gold.latest_reading and gold.screen_reading add the cost (live, or the stored figure of the same span)
to revenue's inputs (`gold.with_cost`) behind a one-time filter; nothing stored changes. Peers count it
as an unknown size; the statements show the revenue as filed.

### 084_net_sales_gross_margin.sql

Walmart's and Costco's gross margin on net sales (Riley 3, the owner's decision of 2026-11-15): their
revenue includes membership fees and other income that cost of sales doesn't cover (039 leaves 5331's
gross profit out). A `net_sales` metric read only for them: Walmart's own metric_tags rows of its
standard tags (RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet before FY2017),
Costco's member pick (073) of the same concept by `us-gaap:ProductMember`. Gross margin is (net sales -
cost of revenue) / net sales for every period with both, its inputs saying so; gross profit stays not
computed. Walmart's quarter to Jul 2026 reads 25.4%, Costco's FY2026 11.1%. Reaches hermes like 073: the
filing-facts job's extras pass reads both companies' stored documents and rebuilds their gold; no
rules_version bump.

### 085_point_in_time_pipeline.sql

Point in time, part 1 (B1): gold's pipeline as pure functions of a knowledge date, so the rows gold
believed on any past day can be computed by the very rules that build gold today. `gold.usable_facts`
and `gold.best_facts` take `p_as_of` (facts filed by the end of that day; NULL: every fact): the
filter sits in usable_facts' `facts` CTE, so the currency, borrowing, balance and debt rules see only
what was filed by then. `filed` is the same for every fact of an accession (no exception on the
golden set), so a filing is all in or all out. The bodies of 043's quarters and 084's derived rows
became `gold.quarterly_rows(ids, p_as_of)` and `gold.derived_rows(ids, p_as_of, p_quarters)`; the
writers are DELETE + INSERT from them, and refresh_quarterly_financials still returns the series' rows.

Where a statement read back rows an earlier one had inserted, it reads a CTE of them, and the reads by
key that had used gold's primary keys became sort passes (a CTE has no index and the planner
estimates it at one row, so a join on it can turn into a scan per row: the first draft's derived step
was 2.3x slower on the golden set and quadratic in a batch):
- 043's gross profit: the quarter's revenue, cost, operating income, SG&A and R&D side by side by one
  GROUP BY; its "no gross profit line overlapping by more than 7 days" by one window: a series' lines
  overlap by 7 days at most and last 79-120 days, so none lies inside another and only the first
  ending on or after the quarter's start + 7 can overlap it.
- 040's sum of a stated year's quarters: those ending by its end + 7 less those starting before its
  start - 7 (a quarter of at most 120 days starting that early ends before the year does), two
  running sums; `in_series` a window over the same rows.
- 063's two stated years within 7 days and "no TTM sum within 7 days": one window over the years and
  the sums.
- 040's latest short-term investments in the two years before a period and the TTM interest expense
  at it: windows over the inputs `periods` is built from (end and value as text: the latest sorts last).

Equal row for row. Locally: the golden reference set (97 companies) built at 084 and at 085:
gold.quarterly_financials (24,252 rows), gold.derived_financials (22,384), ops.quality_findings (342)
and gold.screen_figures (7,811) identical, EXCEPT both ways, every column but refreshed_at. Cost: the
golden set's batches of 25 build in 0.42 s (quarters) and 0.45 s (derived) against 0.42 s and 0.42 s at
084. 077's two correlated subqueries stay: per company they scan a handful of rows (its ProfitLoss
years), and a replay runs per company (086).

On hermes, read-only (2026-11-16, before the 080 catch-up reached them): these bodies inlined as one
SELECT over hermes' own gold.best_facts, pure (the derived step over the quarters just computed, as a
replay reads them), for 500 companies drawn at random among those the current functions built (stamped
77: 080's new tags have no silver facts until a company is re-projected, so 080's best_facts reads them
as 077's did), 50 at a time outside the hourly rebuild, EXCEPT ALL both ways against
gold.quarterly_financials and gold.derived_financials: 891,221 quarterly rows and 1,016,679 derived
rows, 0 differences either way. `filed` is one date per accession there too (0 of the 29,845 accessions
of those companies).

### 086_financials_known.sql

Point in time, part 2: gold's history of itself. `gold.financials_known` holds every version of every
gold row (quarters as span `quarter`, derived rows with their span; a balance's TTM row is read from
its quarter, as in gold.financials): a run of the same (value, unit, period start, value source,
source tag, inputs' values) across the days the company's filings were filed, `known_from` the first
such day (filed by its end), `known_to` the day a filing changed it or took it away (exclusive), the
citation the one gold gave on `known_from` (the first filing to state it: the owner's call, so a
later filing restating the same value doesn't move it). Inputs are compared without their quarters'
accession numbers (`gold.input_values`). Owner's decisions (2026-11-16): a day is the filing date,
acceptance times wait for the backtests (B3).

Written only by gold's own pipeline: `gold.replay_known(company, from)` deletes the versions
starting on or after `from`, reopens those open then, and for each filing date from there runs
`gold.financial_rows(company, day)` (085's quarterly_rows, then derived_rows over those very
quarters) and diffs it against the open versions in one statement (a FULL JOIN on the key: closed,
written). The dates come from `gold.knowledge_facts`: per filing date, the count and sum of fact ids
of the company's own CIKs' facts and of filings whose XBRL names it (a superset of usable_facts' facts:
a date that changes nothing writes nothing). `gold.known_companies` keeps that list from the last
replay; `gold.refresh_known`, called by `gold.refresh_financials` under the same locks and in the same
transaction, replays a company from the first date whose pair changed (a new filing: one view; facts
that arrived late for an older filing: from that filing; a superseded fact: its date). The facts are
read before the views, so a fact committed meanwhile is found next time; ingested_at isn't used (a
concurrent load can commit an older timestamp after a build). Only companies stamped 'known' at the
current gold.rules_version() are kept this way; any other is the catch-up's.

Catch-up kind `known` (baseline 0: every company is behind at the deploy; not paused, the owner's
call): `gold.rebuild_known` replays whole histories under the gold locks, 5 companies a transaction,
after the gold kind and only for companies whose gold is current (ops.company_backlog, as
check_financials). A rules_version bump replays every company again; ops.rebuild_soon marks 'known'
with 'gold'. Not detected (until the next replay): a change of a company's SIC or a filing's XBRL
attribution with no fact added or removed.

Measured. Locally, the golden reference set (97 companies, two fiscal years each, 9-14 filing dates):
49,314 versions for 46,636 rows of gold (1.06x), 2,678 closed; 45 s for all (0.47 s a company, ~39 ms
a view); the open versions equal gold row for row, and at every filing date and the day before it
the history equals gold.financial_rows (tests/golden/test_point_in_time.py).

On hermes, read-only (2026-11-16): gold holds 6.62M quarterly and 7.23M derived rows (7.8 GB), so the
history at the golden set's 1.06x is about 14.7M versions, 8-10 GB with its covering key. Filing dates
per company over the 500 sampled above: mean 59, median 64, p90 70, max 76 (a sample of long-listed
companies; the catalog-wide count is about 225,000 company-dates, measured for the design). One view as of
2024-08-15, the bodies inlined (usable_facts computed once; a replay computes it twice, about a third
more): Apple 418 ms, JPMorgan 322 ms, CrowdStrike 127 ms. So a replay is a few seconds to about half a
minute a company, and the whole catalog about 16 hours of one core: the 'known' catch-up's 20 minutes an
hour finish it in about two days, after the gold kind's own (rules 80: 7,202 companies behind at about
900 an hour on 2026-11-16). Until a company's history is built, its as-of reads replay at read time
(087). Faster replays, later if needed: one best_facts per view (derived_rows reading the view's own
best facts), or set-based view keys.

### 087_point_in_time_reads.sql

Point in time, part 3: reading gold as of a day. `gold.financials_at(day)` is gold.financials over
the versions believed then (the same labels; inlined, RLS). `gold.company_financials_at(company,
day, span, metrics)` (SECURITY DEFINER, `authz.readable_companies('financials')` its only rule,
figure_facts' pattern) serves the single-company routes: the history when today's rules built it,
else gold's pipeline replayed at read time (the same values, citing the latest filing to state a
figure by then). The readings' probes take `p_as_of DEFAULT NULL` (gold.latest_row, latest_equity,
quarterly_recent, amount_currency_of, latest_period, latest_reading): NULL is the old probes, a day
reads the history's primary key behind one-time filters (UNION ALL / coalesce), so each stays one
inlined SELECT and gold.reading stays the one definition of the rules; stale is against
tributary.today, which the Query API sets to the day. The stored screener figures (078) are the NULL
path's, unchanged: no TRUNCATE. `gold.annual_per_share(company, years, day)`: 10-K facts filed by the
day. `gold.restatements(company, day)`: consecutive versions whose value differs (quarters and TTM
sums, not computed ratios), each side's citation and first day, and whether the tag stayed the same;
the kinds (split, another line, same line) are query_api/point_in_time.py's.

Measured on a catalog-sized local copy (the golden set's histories under 6,693 company ids, 3.40M
versions, 1.7 GB; hermes' would be about four times as deep per company), as query_api with the catalog
open: the default screen as of 2025-06-30 0.52 s after a Postgres restart (the OS cache warm: no root to
drop it), 0.36 s warm, a growth sort 0.34 s, against 0.15 s for today's stored figures; one company's
page as of the day (two statements with their originals, ratios, per-share) 0.06 s from its history,
0.20 s replayed at read time (on hermes a replay costs about a view, 0.1-0.6 s, per call, while the
company's history is behind the rules). gold.company_confidence (074) read tributary.today with a bare
cast: an as-of session sets it locally, and a pooled connection then reads '' (never NULL again), so the
next screen on that connection failed; it reads it through nullif now, as gold.is_stale does.

### 100_userdata.sql

Bring your own data (B2c): the schema `userdata` beside gold, a user's own datasets (`datasets`,
`rows`: one row per company, day and `known_at`, values as a `numeric[]` in the fields' order) and
the hourly upload log (`uploads`). Forced row-level security for `query_api` keyed on `app.user_id`
(USING and WITH CHECK): a session that hasn't named the user reads and writes nothing, the owner's
included; the composite foreign key keeps a row under its own user's dataset. `field_points` labels a
day with gold.financials' fiscal and calendar rules (014) for formulas; `series` is one value a row
for a later SQL workspace. Wide rows: the densest 20 MB file allowed (180,000 rows of 48 fields) is
~100 MB stored against ~1 GB one value a row, and stores in ~7 s (measured locally). In every
backup (nothing here can be rebuilt). Query API side: services/query-api DESIGN.md "Your data".

### 110_figures_landed.sql

Saved screens that alert (app migration 024) are checked once after each night's figures, not every
ten minutes. `ops.figures_landed_at()` answers when that was: the newest `ops.coverage_runs` row, the
financials-bulk job's last step (076's coverage report runs once gold is current for the night, changed
zip or not). DEFINER because `query_api` has no grant on the table; one timestamp, nothing else
(aggregate-only like `ops.public_coverage_gaps`), search_path pinned, EXECUTE only `query_api`; `max()`
of the primary key is one index probe. NULL before any run: the alerts job then checks a watch once a
day (query-api `watches.FALLBACK`). CREATE FUNCTION only. Query API side: services/query-api DESIGN.md
"Saved screens that alert".
