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
| [`currency_readings_057.sql`](currency_readings_057.sql) | 057: `gold.not_meaningful` back to 035's four arguments (the five-argument one dropped), `gold.latest_reading` to 056's, `gold.filing_figures` to 051's (no `unit`), `ops.check_financials` to 050's; `gold.amount_currency` dropped. Deploy the previous query-api image with it. Tested: 057 then this, those functions equal to a database at 056 | none (then `ops.check_financials` over every company for 050's debt findings) |
| [`filing_xbrl_facts_055.sql`](filing_xbrl_facts_055.sql) | 055: every silver fact read from a filing's own XBRL deleted, the company tags and member sums removed, `gold.usable_facts` back to 050's body; the tables and functions stay, unused. Suspend the `filing-facts` CronJob first | `financials-rebuild` job |
| [`banks_058.sql`](banks_058.sql) | 058: `gold.not_applicable_metrics` back to 049's body (Schwab, American Express and Synchrony no longer banks; Ally stays one by its SIC) | none for the readings; the `financials-rebuild` job puts their fallback debt tags back into gold |
| [`insider_queue_059.sql`](insider_queue_059.sql) | 059: `silver.xml_date` and `silver.refresh_insider_filings` back to 054's bodies, `ops.document_outbox`, `ops.insider_queue` and `ops.insider_findings` dropped. Deploy the previous doc-fetcher image first, once the outbox is empty | none |
| [`segments_same_basis_053.sql`](segments_same_basis_053.sql) | 053: `gold.refresh_segment_financials` back to 018's body (derived quarters from the latest filings again). Independent of the others. Tested on JPM, NVDA, DECK and VRT: gold equal to 018's row for row | the segments refresh (db/README "053") |

## Order

Newest first, always. To undo a migration, run every file above it in this list first, then it, and
the rebuild its row names once, after the last file:

1. `banks_058.sql`
2. `currency_readings_057.sql`
3. `ifrs_currencies_056.sql`
4. `filing_xbrl_facts_055.sql`
5. `filers_debt_050.sql`
6. `kpis_049.sql`
7. `bank_metrics_048.sql`
8. `interest_expense_043.sql`
9. `gold_rules_038_041.sql`

Two stand apart, each replacing functions no other file touches, so each runs at any point, once:
`segments_same_basis_053.sql` (`gold.refresh_segment_financials`) and `insider_queue_059.sql`
(`silver.xml_date`, `silver.refresh_insider_filings`; it also refuses while `ops.document_outbox` still
owes an event).

Each file starts with a guard that refuses, changing nothing, unless the database is exactly where that
file belongs: every function any file above replaces or drops must have the body (md5 of its source)
it has once the files above have run and this one hasn't. Run out of order, a file would put back an
older function over a newer rule: 048's, 049's or 050's alone on a database at 056 or 057 brought back
pre-056 functions and dropped the currency rule. Run twice, it refuses too. Why each pair is ordered:

- 058 before 049: both put back `gold.not_applicable_metrics` (049's body, then 035's).
- 057 before 056: 056's file puts back 035's `gold.latest_reading`, which calls the four-argument
  `gold.not_meaningful` 057's file restores.
- 056 before 055, and both before 050 and 049: each puts back the body the migration before it left
  (055's also refuses while 056's IFRS tags are still there).
- 050 before 043 and 038-041: they restore older bodies of `gold.usable_facts` and
  `ops.check_financials`, and 050's would then bring back 043's and 041's.
- 049 before 048: 048's file puts back 040's `gold.refresh_derived_financials`, and 049's would then
  bring back 048's (with the efficiency ratio its rows no longer have).
- 043 before 038-041: run after it, 043's would put back 039's fact selection with
  `silver.metric_sums` emptied and 038's tags gone, a state no migration ever built (it also checks
  that `silver.metric_sums` still has rows). 038-041's file restores 031's quarters, so after it 043 is
  undone too.

## Who runs them

The migration owner: the migrate login `tributary` on hermes, through `SET ROLE` as the owner's own
shell can't log in as it:

```bash
sudo -u postgres psql -d tributary -v ON_ERROR_STOP=1 -1 -c 'SET ROLE tributary' -f db/backout/<file>
```

Run as `postgres` itself, a file that drops and re-creates a function (056's `gold.latest_reading`,
057's `gold.filing_figures`) left it owned by postgres, and the next migration from the non-superuser
migrate login couldn't replace it. The guard refuses any login that doesn't own the functions.

## Adding one, or a migration that replaces a function a file here touches

Add the file to `CHAIN` in `scripts/backout_guards.py` (newest first) and to the list above, then
rewrite every guard from a database migrated to the head, as its owner or a superuser (nothing is
changed: it runs the chain in a transaction it rolls back):

```bash
python scripts/backout_guards.py "postgresql:///tributary?host=..."
```

`tests/integration/test_backouts.py` (TEST_DATABASE_URL, rolled back) runs the chain as the owner and
checks that each file puts back exactly the bodies the older migration created (read from the migration
files), that each refuses out of order, twice and as another login, that nothing is left owned by
anyone but the migrate login, and that this list matches `CHAIN`.
