# db

All SQL for Tributary. Two Postgres databases on one server:

| Database | Migrations | Contents |
|---|---|---|
| `tributary` | [`migrations/`](migrations) | the bronze / silver / gold medallion, access table, ops views |
| `app` | [`app/migrations/`](app) | users, organizations, plans, grants, watchlists ([app/README.md](app/README.md)) |

OpenFGA keeps its own `openfga` database, migrated by OpenFGA itself.

## Schemas in `tributary`

| Schema | Holds | Written by |
|---|---|---|
| `bronze` | data as received: `filings` (filings.raw sink), `company_facts` (XBRL payloads), `filing_documents`, `filing_exhibits` (gzip), `filing_xbrl` (facts extracted from the documents' inline XBRL), `submissions`, `submission_filings`, load checkpoints | bronze-loader, facts-fetcher, doc-fetcher, company-loader |
| `silver` | typed and resolved: `companies`, `company_ciks`, `company_aliases`, `metrics`, `metric_tags`, `financial_facts`, `filing_sections`, `filings` (view), `segment_axes`, `segment_metrics`, `segment_metric_tags`, `segment_facts` | company-loader, facts-fetcher (via SQL functions), parser |
| `gold` | what the Query API serves, with provenance (FR6): `quarterly_financials`, `filing_sections` (view), `segment_financials` (view `segments`), `section_search`, `section_diffs`, `section_changes` | `gold.refresh_quarterly_financials`, `gold.refresh_segments`, `gold.refresh_section_search`, parser-index |
| `authz` | `user_access` (private grants: user, company, tier), `user_tiers` (each user's plan tiers), `public_access` (public catalog: company, tier); `readable_companies(tier)`, the read rule every policy uses | authz-sync |
| `ops` | freshness and pipeline-health views for Grafana and the ops exporter | views only |

Transforms are SQL functions (`silver.refresh_financial_facts`, `gold.refresh_quarterly_financials`,
`silver.refresh_company_metadata`); Python services only fetch data and call them. Company
lookup: `SELECT * FROM silver.resolve_company('FB');`.

Row-level security: `007_query_api_rls.sql` creates the `query_api` role and policies on
`gold.quarterly_financials` and `silver.filing_sections` (behind `gold.filing_sections`) that
return only rows the transaction's `app.user_id` may read; since `030_public_access.sql` that is
`authz.readable_companies(tier)`: private grants (`authz.user_access`) plus the public catalog
(`authz.public_access`, within the user's `authz.user_tiers`). No `app.user_id`, no rows
([rls/](rls/README.md)). `017_ui_data.sql` adds what the
research UI reads: `gold.latest_financials` (security_invoker, RLS applies),
`gold.financials_coverage` (company ids with financials only; runs as its owner, so public),
`silver.filings` granted as the public SEC filing index, and the functions
`gold.expected_filings`, `gold.filing_recap`, `gold.filing_flags`, `gold.peer_candidates`. `010_ops.sql` creates the `ops_reader` group role (SELECT on `ops` views only).
`018_segments.sql` adds segment and KPI data from the stored 10-K/10-Q documents' inline XBRL
(facts-fetcher's `facts-fetcher-segments`): `bronze.filing_xbrl`, `silver.segment_facts`,
`gold.segment_financials` (RLS `granted_financials`, like quarterly financials) and the view
`gold.segments` (fiscal/calendar labels), the `segment_sum_*` quality checks
(`ops.check_segments`) and `ops.segment_load_status` for `ops_reader`.
`020_doc_crawl.sql` adds the document crawl's queue and checkpoint (`ops.doc_crawl_targets`,
planned by `ops.doc_crawl_plan`), its pause switch (`ops.doc_crawl_control`) and the
`ops.doc_crawl_progress` / `ops.doc_crawl_status` views for `ops_reader`.
`021_section_diffs_search.sql` adds full-text search and "what changed" over parsed sections,
both built by the parser's `parser-index` job: `gold.section_search` (one row per ~2,000-character
passage: offsets + tsvector, GIN index, no text; `gold.refresh_section_search`),
`gold.section_diffs` / `gold.section_changes` (paragraph-level Risk Factors and MD&A diffs vs the
comparable earlier filing: offsets and word-diff opcodes, no text), all three under RLS
`granted_filings`; the checkpoint `ops.section_index` with `ops.section_index_priors` /
`ops.section_index_pending`, and `ops.section_change_summary` / `ops.section_index_status` (counts
only) for `ops_reader`. All three gold tables are derived: rebuild them by running the job.
`022_sec_limiter_role.sql` adds the `sec_limiter` group role: EXECUTE on `ops.sec_take_token` /
`ops.sec_report_throttle` only (now SECURITY DEFINER with a fixed `search_path`, revoked from
PUBLIC), no table access. The login is created on the host
([deploy/helm/tributary](../deploy/helm/tributary/README.md), "SEC limiter login").
`023_data_notes.sql` adds the `restatement` check (severity `info`) to `ops.check_financials` and
`ops.quality_summary`, and lets the Query API read `ops.quality_findings` / `ops.quality_companies`
(USAGE on `ops`, RLS `granted_financials`) for the product's data notes.
`024_gold_ignores_proxy_statements.sql` (found by the [golden 10-K figures](../tests/golden/README.md)):
`gold.refresh_quarterly_financials` skips facts from proxy and information statements (forms
starting DEF or PRE; silver keeps them), and adds Costco's 10-Q pre-tax tag
(`IncomeLossAttributableToParent`, third `pretax_income` tag). Existing data changes on
`make financials-rebuild`.
`025_filing_sections_company_pushdown.sql` puts `company_id` first in `gold.filing_sections`'
DISTINCT ON key (same rows), so a company filter reaches the base tables' indexes instead of
de-duplicating every section first.
`033_filing_sections_accession_pushdown.sql` (listed here with its sibling) makes the view output
`s.accession_no`, the key column: a filter on accession now reaches the base tables too. Before
it, `gold.filing_flags` and so `/v1/filings/{acc}/recap` sorted all 1.8M sections and timed out,
and `gold.refresh_section_search` sorted them once per batch (locally 3.3 s → 12 ms). Replacing the
view takes ACCESS EXCLUSIVE on it, so its `lock_timeout` is 3 s: if the hourly section-index job holds
the view, the deploy hook fails fast (re-run it) instead of queueing every filing page behind it.
`034_company_search.sql` adds `silver.search_companies(q)`, what a search box does with what
people type: exact ticker first (`BRK.B` = `BRK-B`), then CIK, exact name, retired ticker or former
name, then prefix, a later word, and contained (3+ characters), with spaces ignored for `JP Morgan`
→ JPMorgan Chase. `silver.resolve_company` keeps its exact-match meaning for formulas and the
sandbox. Reads all 33,000 aliases per call (25-38 ms on hermes); no index was needed.
`044_company_search_order.sql` breaks ties within a rank by whether the company has figures here
(`gold.financials_coverage`, public ids) and the normalized name's length, so "bank of" lists Bank
of America before Bank of Chile (a 20-F filer) and isn't pushed down by an SEC suffix (`/DE/`).
`046_sectors.sql` (F4) adds `silver.sic_sectors`, Tributary's own open grouping of the SEC's SIC
codes into 12 sectors and 88 industries (ranges, never overlapping; the judgment calls are in its
header), and the view `silver.company_sectors`. `047_business_peers.sql` adds what the parser image's
weekly `parser-peers` job derives from 10-K Item 1 text: `gold.business_profiles`, `gold.business_peers`
(cosine similarity, shared terms) and `gold.business_mentions` (competitors named, with the sentence's
offsets), all under RLS at `filings` (a peer row needs both companies readable), and
`silver.co_registrants()` (SECURITY DEFINER: query_api still reads no `bronze.filings`), companies filing
one combined 10-K (PG&E Corp and Pacific Gas & Electric).
`048_bank_metrics.sql` adds a bank's own lines: `net_interest_income`, `noninterest_income`,
`noninterest_expense`, `provision_for_credit_losses`, `loans` (net of the allowance), `deposits`,
`cet1_ratio` (where tagged) and the derived `efficiency_ratio`; its header has the tags measured on
every bank's companyfacts and why net interest margin, tangible book value and ROTCE are left out.
Needs `make financials-rebuild` (production: the `financials-rebuild` job) to fill gold.
`049_kpis_cash_flow.sql` (F5) maps what the analysts asked for that filings tag but gold didn't read:
the cash flow statement's `investing_cash_flow`, `financing_cash_flow`, `change_in_cash` and its ending
`cash_and_restricted_cash` (what the change reconciles to; `cash` stays the balance sheet's line),
`contract_liabilities_current` (customer advances and billings ahead of revenue), `contract_liabilities`
(the total, or current + noncurrent as a 038 sum), `remaining_performance_obligations` (revenue under
contract not yet recognised: the nearest us-gaap element to a backlog) and `accounts_payable`; plus the
derived `cash_and_investments` (cash and short-term investments by net debt's own reading, so net debt =
debt - cash and investments; n/a for a bank, like net debt) and `gold.figure_facts`, the facts behind
one figure for the Query API's `/components` (SECURITY DEFINER under `authz.readable_companies`, like
036). Tags and coverage measured on 599 random companies are in its header; on the 613-company sample
rebuilt before and after it, every existing gold row was identical. Needs `financials-rebuild`;
backout `backout/kpis_049.sql`.
`050_filers_debt_signs.sql` (F7a) reads a filing's facts as the company's whose CIK the filing's own
XBRL names (`bronze.filing_xbrl.entity_cik`) when that CIK filed it: since July 2026 the SEC's
companyfacts files some multi-registrant 10-K/10-Qs only under a co-registrant (DTE Energy's under DTE
Electric, Southern's under Mississippi Power; 91 filings in a year), so their gold had stopped and nine
subsidiaries with gold of their own showed the parent's figures. `gold.usable_facts` takes those facts
from the CIK's company (`lent`) and gives them to the named one (`borrowed`); `gold.companies_for_ciks`
tells the facts loaders which companies a changed payload reaches; `gold.filed_by` is the filer test.
Debt balances read as `abs(value)` like 043's interest expense; debt parts at 1,000 or 1,000,000 times
the same filing's own total are left out (`mistagged_fact`, rule `debt_parts_scale`: Bank First's 2019
10-K). Stock-based compensation keeps its sign (its negative quarters reconcile with the filings' year
to date: forfeitures, remeasured liability awards); its `sign_flip` is severity `info`, rule `benefit`,
unless another filing reports the quarter positive. Measurements in its header. Needs
`financials-rebuild`; backout `backout/filers_debt_050.sql`.
`056_ifrs_currencies.sql` (F8a, H12) reads foreign filers: 984 of the 6,066 listed companies had no
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
Catalog changes and small inserts only.
`057_currency_readings.sql` (F8b) reads the ~700 companies in their own currency where 056 still read
dollars. `gold.amount_currency(company)`: its amounts' currency (the latest revenue row's unit, else
net income's, else USD). 035's not-meaningful rules gain a currency (`gold.not_meaningful`'s fifth
argument): the absolute bases (revenue under $100K, growth over 1,000% from under $10M) apply only to
dollar amounts, the ratio bounds to every currency; the four-argument form reads as dollars (an older
Query API). Why not per-currency equivalents: there are no exchange rates here, a fixed table would
drift unnoticed, and the bases exist for shells and pre-revenue filers, which file in dollars.
`gold.latest_reading` passes the base's currency (one PK probe, six metrics only; still inlinable).
`gold.filing_figures` (051) returns each figure's `unit` (DROP and CREATE, 051's grants).
`ops.check_financials` (050's body) compares a debt fact with liabilities and assets in its own unit (a
20-F's convenience dollars beside yen debt read as a scale slip). Catalog only, no rebuild; findings
change at each company's next check. Backout `backout/currency_readings_057.sql` (tested: functions
equal to a database at 056).
`059_fetch_outbox_insider_queue.sql` (pre-ship review B2, B3) is doc-fetcher's: `ops.document_outbox`
(a live document's `documents.fetched` event is owed until Kafka confirms it; written in the
document's own transaction, read by the crawl so it never counts an unannounced document as
published), `ops.insider_queue` (feed Forms 3/4/5 waiting for an idle consumer, or kept as `failed`
with the error so nothing fetches them again) and `ops.insider_findings` (what parsing one noticed:
`bad_date`, `several_values`, `not_well_formed`, `parse_failed`). `silver.xml_date` returns NULL for
a date that doesn't exist (it raised, and one bad Form 4 crash-looped the live consumer);
`silver.refresh_insider_filings` (054's body, edits marked "059") takes the first node of every
column path (a repeated element raised) and records both as findings. No FK to
`bronze.filing_documents` (the nightly slim dump). Locks: three new tables and two replaced
functions only (054's, used by doc-fetcher alone).
`055_filing_xbrl_facts.sql` (F8a) reads a filing's own XBRL where SEC companyfacts doesn't carry it:
the filings companyfacts never received (52 of 12,006 10-K/10-Qs in 200 days: Citigroup's 2026 10-Qs,
Clorox's FY2026 10-K), a company's own tags proven to be a standard line (NVIDIA's 10-K capex tag,
FY2016-22) and balance sheet lines filed only by segment member (Ford's and GM's debt, summed where
the members are lines of the consolidated balance sheet). `silver.financial_facts` gains `taxonomy` and
`source` (`companyfacts`, `filing`: the filing's own XBRL while companyfacts lacks it, `filing_extra`:
what companyfacts never carries); `silver.metric_tags` gains `company_id` (a company's own tag) and a
primary key with the taxonomy; `silver.member_sums` names each sum. `bronze.filing_instances` keeps
instance documents fetched from EDGAR, `bronze.filing_facts` each filing's facts as read (with where
from), `silver.refresh_filing_facts` maps them into silver, `silver.filing_facts_caught_up` and
`silver.supersede_filing_facts` hand a filing back to companyfacts once it has it, so a filing's
facts are never in silver twice. `gold.usable_facts` joins tags by taxonomy and company. Filled by
facts-fetcher's `facts-fetcher-filings` ([README](../services/facts-fetcher/README.md), "Filings
companyfacts lacks"); measurements and the proofs in its header. Locks: two ADD COLUMNs with constant
defaults and a NOT VALID CHECK on `silver.financial_facts` in one statement (no rewrite, an instant of
ACCESS EXCLUSIVE; lock_timeout 3 s), the primary key of the 122-row `silver.metric_tags` rebuilt.
`054_insider_transactions.sql` adds insider filings (#117): `bronze.ownership_documents` (each Form
3/4/5's ownershipDocument XML as filed, from doc-fetcher), `silver.insider_filings` (issuer company,
form, period, reporting owners with their relationship as jsonb) and `silver.insider_transactions`
(each line of the non-derivative and derivative tables in document order), filled by
`silver.refresh_insider_filings(accessions)`: XMLTABLE in Postgres, delete then insert, idempotent on
the accession; a document that isn't well formed XML, or whose issuer isn't a company here, gets no
silver rows. Both silver tables: GRANT SELECT to query_api, RLS `granted_filings` on company_id (030's
form). New tables and functions only: nothing existing is locked. Volume and cost in the doc-fetcher
README ("Insider filings").
`053_segment_same_basis.sql` replaces `gold.refresh_segment_financials` (018's body + edits marked
"053"): a segment quarter derived as a year-to-date difference (Q4 = FY - 9 months) whose later value
was restated after it was first reported takes that value from the first filing on or after the
earlier value's filing, so both come from one reporting cycle and one segment basis (Riley #88:
JPMorgan's FY2022 Q4 CIB was its FY2024 10-K's recast year, with Commercial Banking, less the old
nine months: $22.13B beside $12B quarters, and the TTM fell 10% when it left the window). Measured on
production's segment facts of JPM, NVDA, DECK and VRT, both bodies run on a throwaway copy: 13 of 1,034
quarters change (and the TTMs over them), each now in line with its neighbours and the reported
totals: JPM CIB FY2022 Q4 $22.13B -> $10.60B; NVIDIA's FY2024 geography TTMs now equal its FY2024
10-K (US $26.966B, Taiwan $13.405B, China $10.306B) and its FY2024/FY2025 Q4 "Corporate" operating
income -$1.35B/-$1.84B instead of $41.4B/$92.6B; Vertiv's FY2021 Q4 regions sum to $1,411M against
$1,410M of revenue (was $1,580M) and its FY2020 Q4 products/services to $1,020M/$285M (was $780M/$525M);
Deckers unchanged. A year reported once differently but later as first reported (Vertiv's FY2023
10-K on FY2022) isn't "restated": the latest value, as before. Needs the segments refresh after the
deploy, then `ops.check_segments`; backout `backout/segments_same_basis_053.sql`. Catalog only.
`052_search_sort_total.sql` adds `gold.search_page`: 032's `search_passages` with an order (`relevance`
or `date`) and a `total` of the matching sections, for the search page's count and "Newest first"
(#145). Same SECURITY DEFINER read rule; `search_passages` stays for an API pod from before it.
Catalog only: no table is locked.
`051_filing_figures.sql` adds `gold.filing_figures(company, accession)`: one filing's facts as gold
reads them and the net debt, cash and investments and free cash flow computed from that filing alone
(040's rules), for the thesis check's H2 guard (Query API `/v1/companies/{id}/filings/{accession}/figures`).
SECURITY DEFINER under `authz.readable_companies('financials')`, like 036 and 049. No rebuild.
`045_filing_events.sql` lets the Query API read 8-K item numbers: a column grant on
`bronze.submission_filings (accession_no, cik, items)` (filing-index metadata, public like
`silver.filings`) and `silver.company_8ks(company, from)`, one company's 8-K and 8-K/A with their
items as an array (NULL until the nightly submissions load has the filing). The earnings and
events routes read it ([query-api](../services/query-api/README.md), "Earnings and events").
Catalog changes only: the GRANT takes no lock on the table.
`035_value_readings.sql` is the one definition of how a figure may be read: `gold.not_meaningful`
(thresholds from the data, in its header), `gold.not_applicable_metrics` (banks),
`gold.period_status` ("stale", once: over 300 days behind the company's latest period is old, over
45 is behind), `gold.latest_period` and `gold.latest_reading` (032's `gold.latest_value` plus the
reading, inlinable, under RLS). New functions only; no table changes and nothing to rebuild.
`036_annual_per_share.sql` adds `gold.annual_per_share`: the 10-K's own EPS and weighted shares
per fiscal year, SECURITY DEFINER because `query_api` has no grant on the facts table's values, with
`authz.readable_companies` as its only rule.
`037_check_segments_subtotal.sql` stops `ops.check_segments` adding a reconciliation member that just
restates the segments' sum (NVIDIA's "109.0% more" is 4.5%). Stored findings keep their old figure
until the check runs again (`make segments-rebuild`, or `SELECT ops.check_segments(array_agg(DISTINCT
company_id)) FROM gold.segment_financials`).
`038_metric_tags_moved.sql` to `041_quality_findings.sql` change which facts gold believes and what it
computes from them; they need one `facts-fetcher --rebuild` (the `financials-rebuild` job) to reach
existing rows, and `backout/` undoes them ([backout/README.md](backout/README.md): newest first, as the
migrate login; each file's guard refuses any other order or login and changes nothing). Each file's
header has the measurements behind its thresholds.
- 038: tags a line moved to (Nike's investments to
  `DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent`, DTE's and Xcel's revenue to
  `RegulatedAndUnregulatedOperatingRevenue`, Tesla's, Adobe's, Lennar's and Oracle's debt to
  `LongTermDebt` / `LongTermNotesPayable` / `LongTermNotesAndLoans` / `DebtCurrent` /
  `NotesPayableCurrent`, small filers' `Cash`, VF's `PaymentsForCapitalImprovements`) and
  `silver.metric_sums`: a line filed as two tags, both from one filing for one date (a bank's
  `InterestIncomeExpenseNet + NoninterestIncome`; NVIDIA's `DebtSecuritiesCurrent + EquitySecuritiesFvNi`).
  A sum's parts are `silver.metric_tags` rows with `gold_line = false`: stored, never a line alone.
- 039: `gold.usable_facts(ids)` (silver joined to the tags by tag, so a tag two metrics map serves
  both: `EarningsPerShareBasicAndDiluted` gave no diluted EPS at 173 of the sample's companies; the sums;
  a loss quarter's basic EPS as diluted; without proxy statements, periods ending over 7 days after
  their filing, debt over five times both liabilities and assets, or a fallback debt tag at a bank;
  a fallback debt total less its current portion, where it is one: equal to the current portion, or
  above it and above all noncurrent liabilities; below the current portion it is the noncurrent line)
  and `gold.best_facts(ids)` (each tag's latest filing,
  then for revenue: a zero loses to another tag's later or same-filing value of $1M or more; Revenues
  under 20% of contract revenue is a part; then tag priority). `gold.refresh_quarterly_financials`
  reads `best_facts` (031's series rule unchanged), drops a reported quarter rounded for a sentence
  for the exact year-to-date difference (NVIDIA's dividends 6,000 → 6,047), and adds gross profit as
  revenue less cost where none is printed, behind the guard in the header (SIC, cost ≥ 15% of
  revenue, margin ≥ operating margin, no large unexplained cost, within 8 points of the company's
  median, quarters since 2019). Validated on hermes where gross profit is reported: 98.5% within a
  point; fills 7,675 quarters at 559 companies.
- 040: `gold.refresh_derived_financials` adds a TTM from the stated year where four quarters don't
  exist (not when a must-be-positive year is negative or below its own quarters; ends within 7 days
  are one year, 031's rule, so ATI's FY2021 tagged ending Dec 31 and Jan 2 is in gold once), and net debt's
  rules: investments inside cash aren't subtracted twice; a recent, material investments series with
  no value at the date, where the balance sheet has room for it, makes net debt absent; so does debt
  that can't carry its interest (Ford). Net debt counts short-term investments only.
- 041: `ops.check_financials` reads `best_facts` for the annual comparison, flags negative debt as an
  `impossible_value`, and adds `mistagged_fact`
  (error: a period after its filing, debt over five times liabilities and assets),
  `net_debt_gap` (warning) and a repeated `series_gap` (the same quarter missing three or more
  years); `ops.quality_summary` lists them.
- `042_fiscal_year_end_month.sql`: `silver.fiscal_year_end_month(mmdd)`, the month a 52/53-week year
  belongs to (a day of 1-7 is the month before). The screener's filter and sort; labels unchanged.
- 043: interest expense is a cost, so `gold.usable_facts` reads it as `abs(value)` under every tag
  (filings flip its sign between filings and in comparatives: Disney's FY2025 10-K -1,812M against
  +1,396M nine months in its 10-Qs made Q4 -3,208M; it's 416M), and a derived interest expense
  quarter that is still negative is left out, like 029's other costs. On hermes before: 2,951
  negative quarters at 986 companies, 1,096 `sign_flip` findings. Silver keeps the filing's figure.
  Needs `financials-rebuild`; backout `backout/interest_expense_043.sql`.
`026_feed_status.sql` adds `ops.feed_status()`: when the newest filing was accepted by EDGAR and
when it reached bronze, two timestamps and nothing else. It is SECURITY DEFINER (query_api still
can't read `bronze.filings`) and backs the Query API's public `/v1/status`, which the
[outside uptime check](../deploy/uptime/README.md) reads.
`027_filing_completeness.sql` adds `ops.filing_completeness` (per day of EDGAR's daily index:
accessions indexed, accessions missing from bronze at the last check, rows republished), written
by the nightly completeness check ([poller-backfill](../services/poller/README.md#backfill-fr1b)),
and `ops.filing_completeness_status` for ops_reader. Counts only.
`028_public_status.sql` backs the public status page (Query API `/v1/status/data`): four SECURITY
DEFINER functions (search_path pinned, EXECUTE only for query_api, like 026) that return
aggregates only: `ops.public_ingest()` (last 7 days of `bronze.filings`: filings, how many the live
feed caught, p50/p95 seconds from acceptance to bronze), `ops.public_completeness()` (the last 30
days of `ops.filing_completeness`), `ops.public_coverage()` (companies with gold financials by a
loose index scan of its primary key, stored documents, search-indexed filings and sections) and
`ops.public_quality()` (`ops.quality_summary`). It also indexes `bronze.filings (filed_at)` so the
7-day window is a range scan; the index is built inside the migration, so writes to
`bronze.filings` wait for it.
`029_gold_quality_fixes.sql` fixes two causes of the quality checks' errors in
`gold.quarterly_financials`: flow metrics tagged at an instant (UnitedHealth's dividend payment
dates) no longer become extra quarter rows (1,838 rows on hermes), and a derived quarter that
comes out negative for a metric that can't be (revenue, costs, capex, D&A, SBC, buybacks,
dividends) is left missing instead of published (11,320 rows; e.g. Cytek's Q4 2022 revenue at
-$112.7M after a later 10-K re-tagged the year). Needs `make financials-rebuild`.
`030_public_access.sql` (open access, layer 2): `authz.public_access` and `authz.user_tiers`
(written by authz-sync), the read rule `authz.readable_companies(tier)` / `authz.can_read`,
`authz.catalog_companies()` (catalog membership: companies with gold financials or a stored
document, read by authz-sync) and the nine `granted_*` policies re-pointed at the rule. Ships
with `public_access` empty: no one's access changes until the catalog is opened
([app/README.md](app/README.md), "Open access").
`032_open_access_speed.sql` keeps the cross-company routes fast with the catalog open, without
changing who reads what: `gold.latest_value(company, span, metric)` (`gold.latest_financials`' rule
as one index lookup per table, security invoker, inlined), a last column `filed_on` on
`silver.filings` (the date from the row's own table, so a window reaches both UNION branches), and
`gold.search_passages` (search's first stage: `@@` isn't leakproof, so under RLS the GIN index on
`gold.section_search` is unusable; SECURITY DEFINER with `authz.readable_companies('filings')`
applied inside, search_path pinned, `query_api` only, returns offsets and ranks, no text), and
`gold.expected_filings_recent` (017's estimates for every readable company, from the last 1,500 days
of reports). No index is built; the view takes a brief lock (`lock_timeout` 30 s, the migration fails rather than queueing
readers).
`031_gold_one_quarter_series.sql` gives `gold.quarterly_financials` one non-overlapping series of
quarters per company and metric. Gold kept one row per period end, so a quarter reported with two
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

[`rls/`](rls) and [`transforms/`](transforms) hold no SQL; their READMEs point to the
migrations that define those objects.
