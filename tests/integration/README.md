# integration

Cross-component tests against real databases with migrations applied. Skipped unless the
matching URL is set; CI sets both after running `scripts/migrate.sh` on a fresh Postgres.

| Test | Database | Checks |
|---|---|---|
| `test_app_schema.py` | `APP_TEST_DATABASE_URL` (app) | app schema constraints and CDC prerequisites |
| `test_ops_views.py` | `TEST_DATABASE_URL` (tributary) | the `ops` freshness views (`db/migrations/010_ops.sql`) |

```bash
TEST_DATABASE_URL=... APP_TEST_DATABASE_URL=... pytest tests/integration
```
