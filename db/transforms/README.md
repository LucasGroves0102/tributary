# transforms

FR4–5, week 1. This folder holds no SQL: the bronze → silver → gold transforms are SQL
functions defined in the migrations. To change one, `CREATE OR REPLACE` it in a new migration
starting from the latest definition listed here (and regenerate the backout guards when a
[`../backout/`](../backout/README.md) file touches it).

## Silver

| Function | Latest definition |
|---|---|
| `silver.refresh_financial_facts(text[])` (companyfacts payloads into typed facts: the taxonomy recorded, any currency; each payload's facts read once) | `063_rebuild_speed.sql` (before: `056_ifrs_currencies.sql`, `011_bulk_financials.sql`, first in `003_financials.sql`); metric tags in `003_financials.sql` and `012_more_metrics.sql`, more in `024`, `038`, `048`, `049`, `077`, `080`, company tags in `055`, IFRS tags in `056` |
| `silver.refresh_filing_facts(text[])` (a filing's own XBRL into silver: a filing companyfacts lacks, a company's own tags, member sums, member picks; any currency) | `073_member_picks_cet1.sql` (before: `056_ifrs_currencies.sql`, `055_filing_xbrl_facts.sql`) |
| `silver.filing_facts_caught_up()`, `silver.supersede_filing_facts(text[])` (hand a filing back to companyfacts once it has it) | `055_filing_xbrl_facts.sql` |
| `silver.unit_fits(metric unit, fact unit)` (an amount in any ISO currency, or per share in one) | `056_ifrs_currencies.sql` |
| `silver.refresh_company_metadata` | `013_submissions.sql` |
| `silver.refresh_segment_facts(text[])` | `018_segments.sql` |
| `silver.refresh_insider_filings(text[])` (Forms 3/4/5 XML into insider filings and transactions; repeated elements and bad dates recorded as findings), with `silver.xml_date` | `059_fetch_outbox_insider_queue.sql` (before: `054_insider_transactions.sql`, which also has `silver.xml_flag` / `xml_number`) |
| `silver.resolve_company`, `silver.normalize_name` (entity resolution) | `002_companies.sql` |
| `silver.search_companies` (what a search box finds, ranked) | `066_company_search_classified_first.sql` (ties within a rank: figures here, filer, exchange, a SIC code, then the normalized name's length; before: `044_company_search_order.sql`, `034_company_search.sql`) |
| `silver.fiscal_year_end_month(text)` | `042_fiscal_year_end_month.sql` |

## Gold

| Function | Latest definition |
|---|---|
| `gold.refresh_financials(text[])` (quarters, then derived metrics, the screener's stored figures, then the checks, for a batch of companies; takes the companies' locks and stamps `ops.company_builds`) | `078_screener_figures.sql` (before: `071_auto_catchup.sql`, `014_gold_calendar.sql`) |
| `gold.rules_version()` (the gold rules' version: replace it with the number of a migration that changes them), `gold.lock_companies(text[])` | `080_revenue_industry_tags.sql` (80; before: 77; first: `071_auto_catchup.sql`) |
| `gold.usable_facts(text[], date)` (as of a date: only facts filed by its end; which facts gold may read: tags by taxonomy and company, each company in its reporting currency, sums, dropped facts; interest expense and debt without their sign; a filing's facts to the filer its XBRL names) | `085_point_in_time_pipeline.sql` (`p_as_of` only; before: `056_ifrs_currencies.sql`, `055_filing_xbrl_facts.sql`, `050_filers_debt_signs.sql`, `043_interest_expense_sign.sql`, `039_gold_fact_selection.sql`) |
| `gold.best_facts(text[], date)` (as of a date, passed to usable_facts; which fact gold believes: revenue's zero and part rules, then tag priority; a year tagged ProfitLoss reads it first for net income; revenue's industry totals only where no other tag gives the period) | `085_point_in_time_pipeline.sql` (`p_as_of` only; before: `080_revenue_industry_tags.sql`, an industry total never read at a company that gives it as a part, nor two in one period; another revenue tag under 20% of one is a part; before: `077_net_income_one_line.sql`, one net income line per year; before: `064_revenue_net_of_interest_part.sql`, net revenue under 20% of the gross line is a part; a revenue tag 1,000x another of its period is a scale slip, which one by the period's other figures; before: `039_gold_fact_selection.sql`) |
| `gold.quarterly_rows(text[], date)` (gold's quarters as a pure function, as of a date or NULL: 043's two INSERTs; the gross profit step's keyed reads as one pass) | `085_point_in_time_pipeline.sql` |
| `gold.refresh_quarterly_financials(text[])` (DELETE + INSERT from `gold.quarterly_rows`) | `085_point_in_time_pipeline.sql` (the rules: `043_interest_expense_sign.sql`, a negative derived interest expense quarter is left out; before: `039_gold_fact_selection.sql` reads `gold.best_facts`, drops a quarter rounded for a sentence, gross profit as revenue less cost behind a guard; `031_gold_one_quarter_series.sql` one non-overlapping series of quarters per company and metric, chosen by calendar votes; `029_gold_quality_fixes.sql` instants only for balance-sheet metrics, no negative derived quarter for metrics that can't be negative; `024_gold_ignores_proxy_statements.sql` skips proxy-statement facts; `015_gold_tiebreaks.sql` deterministic tie-breaks; `011_bulk_financials.sql`; `003_financials.sql`) |
| `gold.derived_rows(text[], date, gold.quarter_row[])` (TTM, stated years and computed metrics as a pure function, from gold's quarters, the quarters given, or as of a date; 040's keyed reads as window passes) | `085_point_in_time_pipeline.sql` |
| `gold.refresh_derived_financials(text[])` (DELETE + INSERT from `gold.derived_rows`) | `085_point_in_time_pipeline.sql` (the rules: `084_net_sales_gross_margin.sql`, gross margin on net sales where a company's are read; before: `063_rebuild_speed.sql`, 040's two-stated-years rule as a window; before: `056_ifrs_currencies.sql` computed amounts in the company's currency; `049_kpis_cash_flow.sql` `cash_and_investments`; `048_bank_metrics.sql` the efficiency ratio; `040_gold_derived_rules.sql` TTM from a stated year, net debt absent when investments or debt can't be read; `014_gold_calendar.sql`) |
| `gold.companies_for_ciks(text[])` (the companies a changed payload reaches, borrowers included), `gold.filed_by(accession, cik)` | `050_filers_debt_signs.sql` |
| `gold.refresh_segments(text[])` (stamps `check_segments`) | `071_auto_catchup.sql` (before: `018_segments.sql`) |
| `gold.refresh_segment_financials(text[])` (a restated year-to-date difference from one reporting cycle; one company at a time) | `062_segment_refresh_speed.sql` (before: `053_segment_same_basis.sql`, `018_segments.sql`) |
| `gold.refresh_section_search(text[])` (search passages, page furniture blanked) | `068_search_page_furniture.sql` (before: `021_section_diffs_search.sql`) |
| `ops.section_index_priors(text[])` (parser-index work); the diffs themselves are Python (`filing_parser/diff.py`) | `021_section_diffs_search.sql` |
| `ops.section_index_pending(text, int)` (filings never indexed or diffed first) | `071_auto_catchup.sql` (before: `021_section_diffs_search.sql`) |

Business profiles and peers (`gold.business_*`, `047_business_peers.sql`) are computed in Python by
the parser image's `parser-peers` job, not in SQL.

## Checks

| Function | Latest definition |
|---|---|
| `ops.check_financials(text[])` (statement_mismatch over `ops.statement_comparisons`; the confidence mark's inputs stored; tag_disagreement over `ops.tag_relations`; mistagged facts, series gaps and lumpy outliers info; annual sums against a restated year info; each company's figures counted) | `074_statement_reconciliation_confidence.sql` (before: `072_data_trust_checks.sql`; `057_currency_readings.sql` debt compared with liabilities and assets in its own currency; `050_filers_debt_signs.sql` SBC benefits as info, `debt_parts_scale`; `041_quality_findings.sql` `mistagged_fact`, `net_debt_gap`, a repeated `series_gap`, the annual comparison through `gold.best_facts`; `023_data_notes.sql` restatement; `018_segments.sql`; `014_gold_calendar.sql`) |
| `ops.check_segments(text[])` (a reconciliation member restating the segments' sum isn't added; members that overlap, some set of them making the figure, aren't a deviation: `ops.segment_cover`, zeros out and negative members in every set) | `072_data_trust_checks.sql` (before: `070_check_segments_overlap.sql`, `037_check_segments_subtotal.sql`, `018_segments.sql`) |
| `ops.check_financials_version()`, `ops.check_segments_version()` (bump with a check's replace) | `074_statement_reconciliation_confidence.sql`, `072_data_trust_checks.sql` (first: `071_auto_catchup.sql`) |
| `ops.statement_comparisons(text[])` (each filing's statement lines against gold: own, agree, clear), `ops.core_figures(company)`, `ops.entity_kind(company, sic)` | `074_statement_reconciliation_confidence.sql` |
| `ops.recheck(kind, text[])` | `071_auto_catchup.sql` |
| `ops.quality_tier(check, severity)` (the status page's groups), views `ops.quality_tiers` and `ops.quality_summary`, `ops.public_quality()` | `072_data_trust_checks.sql` (`ops.quality_tiers` with `statement_mismatch`: `074_statement_reconciliation_confidence.sql`) |
| `ops.check_sections(text[])` (parser-output checks per document), `ops.check_sections_version()`, `ops.section_key_valid(form, key)`, `ops.core_section_keys(form)`, `ops.section_check_backlog(int)`, `ops.public_parser_quality()` | `072_data_trust_checks.sql` |
| `ops.refresh_extremes(int)`, `ops.extreme_columns()` (the screener's columns), view `ops.extremes_report` | `072_data_trust_checks.sql` |
| `ops.coverage_found()` (the coverage sweep's rule: every listed company's gaps, cause and reason), `ops.refresh_coverage()`, views `ops.coverage_report` / `coverage_summary`, `ops.public_coverage_gaps()`, `ops.rebuild_soon(text[])` | `076_coverage.sql` |

## Catch-up

| Function | Latest definition |
|---|---|
| `ops.company_backlog(kind)` (a statement read after a company's check makes the check due) | `074_statement_reconciliation_confidence.sql` (before: `071_auto_catchup.sql`) |
| `ops.statement_backlog(reader)` (10-K/10-Qs with tables this statements.READER hasn't read) | `079_catchup_status_cleanups.sql` (before: `074_statement_reconciliation_confidence.sql`) |
| `ops.section_check_backlog(int)` (documents no check of this version has seen; an anti-join) | `079_catchup_status_cleanups.sql` (before: `072_data_trust_checks.sql`) |
| `ops.catchup_record` (nine arguments, with a note), `ops.catchup_skipped`, view `ops.catchup_status` (`note`; a skip counts as progress) | `079_catchup_status_cleanups.sql` (before: `071_auto_catchup.sql`) |
| `gold.latest_row`, `gold.latest_equity`, `gold.quarterly_recent`, `gold.is_stale`, `gold.reading` (the readings' parts and rules), `gold.refresh_screen`, `gold.screen_backlog`, `gold.refresh_screen_backlog`, `gold.screen_company` | `078_screener_figures.sql` |
| `gold.screen_reading` (the stored cost of revenue beside a gross-sales venue's revenue) | `083_gross_sales_revenue.sql` (before: `078_screener_figures.sql`) |
| `ops.stamp_builds`, `ops.build_failed`, `ops.catchup_begin`, `ops.catchup_record`, `ops.parser_catchup` (documents of an older parser back through the crawl's outbox), view `ops.catchup_status` | `071_auto_catchup.sql` |

## Readings (read, not refreshed)

How a stored figure may be read, called by the Query API under RLS or with
`authz.readable_companies` applied inside ([../rls/](../rls/README.md)):

| Function | Latest definition |
|---|---|
| `gold.not_meaningful` (five arguments: the dollar bases only for dollar amounts; the four-argument form reads as dollars; revenue that is gross sales, its cost of revenue in the inputs) | `083_gross_sales_revenue.sql` (before: `057_currency_readings.sql`, `035_value_readings.sql`) |
| `gold.gross_sales_sic(sic)` (where revenue can be gross sales: 6199, 6200-6299), `gold.with_cost(inputs, cost)` | `083_gross_sales_revenue.sql` |
| `gold.not_applicable_metrics` (the one list of banks: depository SICs, Goldman Sachs, Morgan Stanley, Schwab, American Express, Synchrony, Ally) | `058_bank_holding_companies.sql` (before: `049_kpis_cash_flow.sql` adds `cash_and_investments`; `035_value_readings.sql`) |
| `gold.period_status`, `gold.latest_period` | `035_value_readings.sql` |
| `gold.latest_reading` (`other_currency` and the unit; the base's currency from `gold.amount_currency_of`, inlined; `not_computed` and `stale`; the rules through `gold.reading`; a gross-sales venue's cost of revenue) | `083_gross_sales_revenue.sql` (before: `078_screener_figures.sql`, `069_readings_not_computed_stale.sql` (before: `061_screener_currency_inline.sql`, `060_annual_filer_currency.sql`, `057_currency_readings.sql`, `056_ifrs_currencies.sql`, `035_value_readings.sql`) |
| `gold.not_computed(metric, sic)` (why Tributary computes no gross profit for this kind of company: 039's industries, worded) | `069_readings_not_computed_stale.sql` |
| `gold.amount_currency_of(company)` (the rule: latest quarterly revenue's unit, else net income's, else the TTM rows', else USD; one row, set-returning so a caller's plan inlines it) and `gold.amount_currency(company)` (that row's unit, for scalar callers) | `061_screener_currency_inline.sql` (before: `060_annual_filer_currency.sql`, `057_currency_readings.sql`) |
| `gold.latest_value(company, span, metric)` | `032_open_access_speed.sql` |
| `gold.figure_facts(company, metric, period_end)` (the facts behind one figure, for `/components`) | `056_ifrs_currencies.sql` (taxonomy, company tags, the company's currency; before: `049_kpis_cash_flow.sql`) |
| `gold.filing_figures(company, accession)` (one filing's figures and what gold would compute from it alone, with their unit) | `057_currency_readings.sql` (before: `051_filing_figures.sql`) |
| `gold.annual_per_share(company, years)` (the 10-K's own EPS and weighted shares) | `056_ifrs_currencies.sql` (before: `036_annual_per_share.sql`) |
| `gold.company_confidence(company, sic, latest)` (high / medium / low and its reasons, from public signals; plain SQL, inlinable, the screener reads it per page row) | `074_statement_reconciliation_confidence.sql` |
| `gold.disagreeing_figures(companies)` (a tag disagreement's two figures, for the data notes; DEFINER, the access rule its only rule) | `075_disagreeing_figures.sql` |

See [`../migrations/`](../migrations).
