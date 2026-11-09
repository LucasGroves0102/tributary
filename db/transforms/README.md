# transforms

FR4–5, week 1. This folder holds no SQL: the bronze → silver → gold transforms are SQL
functions defined in the migrations, latest definitions first:

| Function | Defined in |
|---|---|
| `gold.usable_facts(text[])` (which facts gold may read: tags, sums, dropped facts; interest expense without its sign) | `043_interest_expense_sign.sql` (before: `039_gold_fact_selection.sql`) |
| `gold.best_facts(text[])` (which fact gold believes: revenue's zero and part rules, then tag priority) | `039_gold_fact_selection.sql` |
| `gold.refresh_quarterly_financials(text[])` | `043_interest_expense_sign.sql` (a negative derived interest expense quarter is left out; before: `039_gold_fact_selection.sql` reads `gold.best_facts`; drops a quarter rounded for a sentence; gross profit as revenue less cost behind a guard; before: `031_gold_one_quarter_series.sql` one non-overlapping series of quarters per company and metric, chosen by calendar votes; before: `029_gold_quality_fixes.sql` instants only for balance-sheet metrics, no negative derived quarter for metrics that can't be negative; `024_gold_ignores_proxy_statements.sql` skips proxy-statement facts, `015_gold_tiebreaks.sql` deterministic tie-breaks, `011_bulk_financials.sql`, `003_financials.sql`) |
| `silver.refresh_financial_facts(text[])` | `011_bulk_financials.sql` (first in `003_financials.sql`); metric tags in `012_more_metrics.sql` (+ one in `024`) |
| `gold.refresh_derived_financials(text[])` | `040_gold_derived_rules.sql` (TTM from a stated year; net debt absent when investments or debt can't be read; before: `014_gold_calendar.sql`) |
| `gold.refresh_financials(text[])` | `014_gold_calendar.sql` |
| `silver.refresh_company_metadata` | `013_submissions.sql` |
| `silver.refresh_segment_facts(text[])`, `gold.refresh_segment_financials(text[])`, `gold.refresh_segments(text[])`, `ops.check_segments(text[])` | `018_segments.sql` |
| `gold.refresh_section_search(text[])` (search passages); `ops.section_index_priors(text[])`, `ops.section_index_pending(text, int)` (parser-index work); the diffs themselves are Python (`filing_parser/diff.py`) | `021_section_diffs_search.sql` |
| `ops.check_financials(text[])` (adds `mistagged_fact`, `net_debt_gap`, a repeated `series_gap`; annual comparison through `gold.best_facts`) | `041_quality_findings.sql` (before: `023_data_notes.sql` restatement check, `018_segments.sql`, `014_gold_calendar.sql`) |
| `silver.fiscal_year_end_month(text)` | `042_fiscal_year_end_month.sql` |
| `silver.resolve_company`, `silver.normalize_name` (entity resolution) | `002_companies.sql` |
| `silver.search_companies` (what a search box finds, ranked) | `034_company_search.sql` |
| `ops.check_segments(text[])` (latest: a reconciliation member restating the segments' sum isn't added) | `037_check_segments_subtotal.sql` (before: `018_segments.sql`) |
| `gold.not_meaningful`, `gold.not_applicable_metrics`, `gold.period_status`, `gold.latest_period`, `gold.latest_reading` (how a figure may be read: read, not refreshed) | `035_value_readings.sql` |

See [`../migrations/`](../migrations).
