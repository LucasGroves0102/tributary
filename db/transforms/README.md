# transforms

FR4–5, week 1. This folder holds no SQL: the bronze → silver → gold transforms are SQL
functions defined in the migrations. To change one, `CREATE OR REPLACE` it in a new migration
starting from the latest definition listed here (and regenerate the backout guards when a
[`../backout/`](../backout/README.md) file touches it).

## Silver

| Function | Latest definition |
|---|---|
| `silver.refresh_financial_facts(text[])` (companyfacts payloads into typed facts: the taxonomy recorded, any currency) | `056_ifrs_currencies.sql` (before: `011_bulk_financials.sql`, first in `003_financials.sql`); metric tags in `003_financials.sql` and `012_more_metrics.sql`, more in `024`, `038`, `048`, `049`, company tags in `055`, IFRS tags in `056` |
| `silver.refresh_filing_facts(text[])` (a filing's own XBRL into silver: a filing companyfacts lacks, a company's own tags, member sums; any currency) | `056_ifrs_currencies.sql` (before: `055_filing_xbrl_facts.sql`) |
| `silver.filing_facts_caught_up()`, `silver.supersede_filing_facts(text[])` (hand a filing back to companyfacts once it has it) | `055_filing_xbrl_facts.sql` |
| `silver.unit_fits(metric unit, fact unit)` (an amount in any ISO currency, or per share in one) | `056_ifrs_currencies.sql` |
| `silver.refresh_company_metadata` | `013_submissions.sql` |
| `silver.refresh_segment_facts(text[])` | `018_segments.sql` |
| `silver.refresh_insider_filings(text[])` (Forms 3/4/5 XML into insider filings and transactions; repeated elements and bad dates recorded as findings), with `silver.xml_date` | `059_fetch_outbox_insider_queue.sql` (before: `054_insider_transactions.sql`, which also has `silver.xml_flag` / `xml_number`) |
| `silver.resolve_company`, `silver.normalize_name` (entity resolution) | `002_companies.sql` |
| `silver.search_companies` (what a search box finds, ranked) | `044_company_search_order.sql` (ties within a rank: figures here, then the normalized name's length; before: `034_company_search.sql`) |
| `silver.fiscal_year_end_month(text)` | `042_fiscal_year_end_month.sql` |

## Gold

| Function | Latest definition |
|---|---|
| `gold.refresh_financials(text[])` (quarters, then derived metrics, then the checks, for a batch of companies) | `014_gold_calendar.sql` |
| `gold.usable_facts(text[])` (which facts gold may read: tags by taxonomy and company, each company in its reporting currency, sums, dropped facts; interest expense and debt without their sign; a filing's facts to the filer its XBRL names) | `056_ifrs_currencies.sql` (before: `055_filing_xbrl_facts.sql`, `050_filers_debt_signs.sql`, `043_interest_expense_sign.sql`, `039_gold_fact_selection.sql`) |
| `gold.best_facts(text[])` (which fact gold believes: revenue's zero and part rules, then tag priority) | `039_gold_fact_selection.sql` |
| `gold.refresh_quarterly_financials(text[])` | `043_interest_expense_sign.sql` (a negative derived interest expense quarter is left out; before: `039_gold_fact_selection.sql` reads `gold.best_facts`, drops a quarter rounded for a sentence, gross profit as revenue less cost behind a guard; `031_gold_one_quarter_series.sql` one non-overlapping series of quarters per company and metric, chosen by calendar votes; `029_gold_quality_fixes.sql` instants only for balance-sheet metrics, no negative derived quarter for metrics that can't be negative; `024_gold_ignores_proxy_statements.sql` skips proxy-statement facts; `015_gold_tiebreaks.sql` deterministic tie-breaks; `011_bulk_financials.sql`; `003_financials.sql`) |
| `gold.refresh_derived_financials(text[])` | `056_ifrs_currencies.sql` (computed amounts in the company's currency; before: `049_kpis_cash_flow.sql` `cash_and_investments`; `048_bank_metrics.sql` the efficiency ratio; `040_gold_derived_rules.sql` TTM from a stated year, net debt absent when investments or debt can't be read; `014_gold_calendar.sql`) |
| `gold.companies_for_ciks(text[])` (the companies a changed payload reaches, borrowers included), `gold.filed_by(accession, cik)` | `050_filers_debt_signs.sql` |
| `gold.refresh_segments(text[])` | `018_segments.sql` |
| `gold.refresh_segment_financials(text[])` (a restated year-to-date difference from one reporting cycle; one company at a time) | `062_segment_refresh_speed.sql` (before: `053_segment_same_basis.sql`, `018_segments.sql`) |
| `gold.refresh_section_search(text[])` (search passages); `ops.section_index_priors(text[])`, `ops.section_index_pending(text, int)` (parser-index work); the diffs themselves are Python (`filing_parser/diff.py`) | `021_section_diffs_search.sql` |

Business profiles and peers (`gold.business_*`, `047_business_peers.sql`) are computed in Python by
the parser image's `parser-peers` job, not in SQL.

## Checks

| Function | Latest definition |
|---|---|
| `ops.check_financials(text[])` (debt compared with liabilities and assets in its own currency) | `057_currency_readings.sql` (before: `050_filers_debt_signs.sql` SBC benefits as info, `debt_parts_scale`; `041_quality_findings.sql` `mistagged_fact`, `net_debt_gap`, a repeated `series_gap`, the annual comparison through `gold.best_facts`; `023_data_notes.sql` restatement; `018_segments.sql`; `014_gold_calendar.sql`) |
| `ops.check_segments(text[])` (a reconciliation member restating the segments' sum isn't added) | `037_check_segments_subtotal.sql` (before: `018_segments.sql`) |

## Readings (read, not refreshed)

How a stored figure may be read, called by the Query API under RLS or with
`authz.readable_companies` applied inside ([../rls/](../rls/README.md)):

| Function | Latest definition |
|---|---|
| `gold.not_meaningful` (five arguments: the dollar bases only for dollar amounts; the four-argument form reads as dollars) | `057_currency_readings.sql` (before: `035_value_readings.sql`) |
| `gold.not_applicable_metrics` (the one list of banks: depository SICs, Goldman Sachs, Morgan Stanley, Schwab, American Express, Synchrony, Ally) | `058_bank_holding_companies.sql` (before: `049_kpis_cash_flow.sql` adds `cash_and_investments`; `035_value_readings.sql`) |
| `gold.period_status`, `gold.latest_period` | `035_value_readings.sql` |
| `gold.latest_reading` (`other_currency` and the unit; the base's currency from `gold.amount_currency_of`, inlined) | `061_screener_currency_inline.sql` (before: `060_annual_filer_currency.sql`, `057_currency_readings.sql`, `056_ifrs_currencies.sql`, `035_value_readings.sql`) |
| `gold.amount_currency_of(company)` (the rule: latest quarterly revenue's unit, else net income's, else the TTM rows', else USD; one row, set-returning so a caller's plan inlines it) and `gold.amount_currency(company)` (that row's unit, for scalar callers) | `061_screener_currency_inline.sql` (before: `060_annual_filer_currency.sql`, `057_currency_readings.sql`) |
| `gold.latest_value(company, span, metric)` | `032_open_access_speed.sql` |
| `gold.figure_facts(company, metric, period_end)` (the facts behind one figure, for `/components`) | `056_ifrs_currencies.sql` (taxonomy, company tags, the company's currency; before: `049_kpis_cash_flow.sql`) |
| `gold.filing_figures(company, accession)` (one filing's figures and what gold would compute from it alone, with their unit) | `057_currency_readings.sql` (before: `051_filing_figures.sql`) |
| `gold.annual_per_share(company, years)` (the 10-K's own EPS and weighted shares) | `056_ifrs_currencies.sql` (before: `036_annual_per_share.sql`) |

See [`../migrations/`](../migrations).
