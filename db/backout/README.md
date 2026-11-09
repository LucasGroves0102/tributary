# backout

SQL to undo a migration's effect by hand, for when a rebuild shows its figures are wrong. Never a
migration: `scripts/migrate.sh` doesn't read this folder and `db/Dockerfile` doesn't copy it, so it
can't run by accident. Migrations stay applied; a file here restores the functions they replaced and
removes the rows they added, and the rebuild job then rebuilds gold the old way.

| File | Undoes | Then |
|---|---|---|
| [`gold_rules_038_041.sql`](gold_rules_038_041.sql) | 038's tags and sums, 039's fact selection and computed gross profit, 040's net debt and annual TTM, 041's checks (back to 031, 014 and 023; with 043 applied, it undoes 043 too) | `financials-rebuild` job ([helm chart](../../deploy/helm/tributary/README.md)) |
| [`interest_expense_043.sql`](interest_expense_043.sql) | 043 alone: interest expense read with the filing's sign again, a negative derived quarter kept (back to 039's two functions) | `financials-rebuild` job |
| [`bank_metrics_048.sql`](bank_metrics_048.sql) | 048: a bank's metrics, tags and rows removed, `gold.refresh_derived_financials` back to 040's body. Independent of the two above | `financials-rebuild` job |
| [`filers_debt_050.sql`](filers_debt_050.sql) | 050: `gold.usable_facts` back to 043's body (no co-registrant attribution, debt with the filing's sign, slipped debt parts kept), `ops.check_financials` to 041's; `gold.companies_for_ciks` stays but returns only the CIKs' own companies | `financials-rebuild` job |
| [`kpis_049.sql`](kpis_049.sql) | 049: the cash flow subtotals, contract balances, RPO, payables and cash and investments removed, `gold.refresh_derived_financials` back to 048's body and `gold.not_applicable_metrics` to 035's (`gold.figure_facts` stays: see its header). Tested on 60 companies of the sample: gold equal to before 049 row for row | `financials-rebuild` job |
| [`ifrs_currencies_056.sql`](ifrs_currencies_056.sql) | 056: the IFRS tags and their sum removed, every silver fact in an ifrs-full tag or a currency other than the dollar deleted, and the seven functions it replaced back to 011's, 055's, 049's, 035's and 036's bodies (`gold.latest_reading` loses `unit`: deploy the previous query-api image with it). Tested: 056 then this, every function equal to a database at 055 | `financials-rebuild` job |
| [`filing_xbrl_facts_055.sql`](filing_xbrl_facts_055.sql) | 055: every silver fact read from a filing's own XBRL deleted, the company tags and member sums removed, `gold.usable_facts` back to 050's body; the tables and functions stay, unused. Suspend the `filing-facts` CronJob first | `financials-rebuild` job |
| [`segments_same_basis_053.sql`](segments_same_basis_053.sql) | 053: `gold.refresh_segment_financials` back to 018's body (derived quarters from the latest filings again). Independent of the others. Tested on JPM, NVDA, DECK and VRT: gold equal to 018's row for row | the segments refresh (db/README "053") |

## Order

`interest_expense_043.sql` runs before `gold_rules_038_041.sql`, never after. To undo 043 alone, run
only `interest_expense_043.sql`. To undo everything, `gold_rules_038_041.sql` alone is enough (it
restores 031's quarters, which undoes 043 too); running `interest_expense_043.sql` first is harmless.
Run after it, `interest_expense_043.sql` would put back 039's fact selection with `silver.metric_sums`
emptied and 038's tags gone, a state no migration ever built: so it refuses (it checks that
`silver.metric_sums` still has rows and changes nothing otherwise). Then run the rebuild job once,
after the last file.

`filers_debt_050.sql` runs before `interest_expense_043.sql` and `gold_rules_038_041.sql`, never after:
they restore older bodies of the same two functions, and 050's would then bring back 043's and 041's.

`ifrs_currencies_056.sql` runs before `filing_xbrl_facts_055.sql`, and both before `kpis_049.sql` and
`filers_debt_050.sql`, never after: each puts back the body the migration before it left (055's refuses
while 056's IFRS tags are still there).

`kpis_049.sql` runs before `bank_metrics_048.sql`, never after: 048's file puts back 040's
`gold.refresh_derived_financials`, and 049's would then bring back 048's (with the efficiency ratio its rows
no longer have).
