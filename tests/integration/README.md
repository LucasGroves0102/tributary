# integration

Cross-component tests against real databases with migrations applied. Skipped unless the
matching URL is set; CI sets both after running `scripts/migrate.sh` on a fresh Postgres.

| Test | Database | Checks |
|---|---|---|
| `test_app_schema.py` | `APP_TEST_DATABASE_URL` (app) | app schema constraints and CDC prerequisites |
| `test_ops_views.py` | `TEST_DATABASE_URL` (tributary) | the `ops` freshness views (`db/migrations/010_ops.sql`) |
| `test_gold_calendar.py` | `TEST_DATABASE_URL` (tributary) | fiscal and calendar labels, TTM, derived metrics and the data-quality checks over gold (`014_gold_calendar.sql` and later) |
| `test_data_notes.py` | `TEST_DATABASE_URL` (tributary) | the restatement check and that `query_api` reads findings only under RLS (`023_data_notes.sql`) |
| `test_backouts.py` | `TEST_DATABASE_URL` (tributary, as a superuser; the functions' owner is the migrate login) | `db/backout`: every file is guarded and listed (`CHAIN` and `INDEPENDENT_CHAINS` in `scripts/backout_guards.py`, `CHAIN` in the README's order); the chain runs newest first as the owner and puts back each older migration's bodies; each file refuses out of order, twice, or as another login |

```bash
TEST_DATABASE_URL=... APP_TEST_DATABASE_URL=... pytest tests/integration
```
