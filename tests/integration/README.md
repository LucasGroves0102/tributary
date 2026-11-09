# integration

Cross-component tests against real databases with migrations applied. Skipped unless the
matching URL is set; CI sets both after running `scripts/migrate.sh` on a fresh Postgres.

| Test | Database | Checks |
|---|---|---|
| `test_app_schema.py` | `APP_TEST_DATABASE_URL` (app) | app schema constraints and CDC prerequisites |
| `test_ops_views.py` | `TEST_DATABASE_URL` (tributary) | the `ops` freshness views (`db/migrations/010_ops.sql`) |
| `test_backouts.py` | `TEST_DATABASE_URL` (tributary, as a superuser; the functions' owner is the migrate login) | `db/backout`: the chain runs newest first as the owner and puts back each older migration's bodies; each file refuses out of order, twice, or as another login |

```bash
TEST_DATABASE_URL=... APP_TEST_DATABASE_URL=... pytest tests/integration
```
