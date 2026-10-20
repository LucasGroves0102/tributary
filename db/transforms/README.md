# transforms

FR4–5, week 1. This folder holds no SQL: the bronze → silver → gold transforms are SQL
functions defined in the migrations, latest definitions first:

| Function | Defined in |
|---|---|
| `gold.refresh_quarterly_financials(text[])` | `015_gold_tiebreaks.sql` (deterministic tie-breaks; before: `011_bulk_financials.sql`, `003_financials.sql`) |
| `silver.refresh_financial_facts(text[])` | `011_bulk_financials.sql` (first in `003_financials.sql`); metric tags in `012_more_metrics.sql` |
| `gold.refresh_derived_financials(text[])`, `gold.refresh_financials(text[])` | `014_gold_calendar.sql` |
| `silver.refresh_company_metadata` | `013_submissions.sql` |
| `silver.resolve_company`, `silver.normalize_name` (entity resolution) | `002_companies.sql` |

See [`../migrations/`](../migrations).
