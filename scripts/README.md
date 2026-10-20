# scripts

Helper scripts called by the Makefile, CI and the migrate image. Run them through `make` where
a target exists; they read connection settings from `.env` that way.

| Script | Used by | Does |
|---|---|---|
| `migrate.sh [dir] [database-url]` | `make migrate`, CI, `db/Dockerfile` | applies `dir/*.sql` in name order, each once, recorded in `public.schema_migrations`; defaults `db/migrations` and `$DATABASE_URL` |
| `create-databases.sh` | `make up`, `make migrate`, CI | creates the `app` and `openfga` databases next to `$DATABASE_URL`'s if missing |
| `authz-bootstrap.sh` | `make authz` | finds or creates the `tributary` OpenFGA store and writes `authz/model.fga` (uses the compose `fga` tool container) |
| `register-connector.sh` | `make cdc` | registers or updates the Debezium connector from `streaming/debezium/app-connector.json` on `$CONNECT_URL` (default `http://localhost:8083`) and waits until it runs |
| `check-secrets-encrypted.py` | CI, pre-commit hook | fails if a file in `deploy/secrets/` has a plaintext value or no SOPS metadata |
