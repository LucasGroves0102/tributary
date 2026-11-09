# backout

SQL to undo a migration's effect by hand, for when a rebuild shows its figures are wrong. Never a
migration: `scripts/migrate.sh` doesn't read this folder and `db/Dockerfile` doesn't copy it, so it
can't run by accident. Migrations stay applied; a file here restores the functions they replaced and
removes the rows they added, and the rebuild job then rebuilds gold the old way.

| File | Undoes | Then |
|---|---|---|
| [`gold_rules_038_041.sql`](gold_rules_038_041.sql) | 038's tags and sums, 039's fact selection and computed gross profit, 040's net debt and annual TTM, 041's checks (back to 031, 014 and 023; with 043 applied, it undoes 043 too) | `financials-rebuild` job ([helm chart](../../deploy/helm/tributary/README.md)) |
| [`interest_expense_043.sql`](interest_expense_043.sql) | 043 alone: interest expense read with the filing's sign again, a negative derived quarter kept (back to 039's two functions) | `financials-rebuild` job |
