# backout

SQL to undo a migration's effect by hand, for when a rebuild shows its figures are wrong. Never a
migration: `scripts/migrate.sh` doesn't read this folder and `db/Dockerfile` doesn't copy it, so it
can't run by accident. Migrations stay applied; a file here restores the functions they replaced and
removes the rows they added, and the rebuild job then rebuilds gold the old way. The table lists them
in the order they run ([Order](#order)), the two independent ones last.

| File | Undoes | Then |
|---|---|---|
| [`revenue_part_064.sql`](revenue_part_064.sql) | 064: `gold.best_facts` back to 039's body (`RevenuesNetOfInterestExpense` under 20% of the period's gross revenue is believed again: Rectitude's FY2025 S$74,328, StoneX's net operating revenues; and revenue figures 1,000x apart in one period are both believed again: Tigo's $50B contract tag, Plexus' quarters in thousands). Tested: 064 then this, the function equal to 039's | `financials-rebuild` job for the CIKs 064's runbook rebuilt (or all) |
| [`rebuild_speed_063.sql`](rebuild_speed_063.sql) | 063: `silver.refresh_financial_facts` and `gold.refresh_derived_financials` back to 056's bodies (the same rows; the rebuild slower again). Tested: 063 then this, both functions equal to a database at 062 | none |
| [`currency_speed_061.sql`](currency_speed_061.sql) | 061: `gold.amount_currency` and `gold.latest_reading` back to 060's bodies, `gold.amount_currency_of` dropped (the same readings; the screener's margin and growth sorts slow again). Same signatures: no other image needed. Tested: 061 then this, both functions equal to a database at 060 | none |
| [`annual_currency_060.sql`](annual_currency_060.sql) | 060: `gold.amount_currency` and `gold.latest_reading` back to 057's bodies (an annual-only 20-F/40-F filer's currency reads USD again). Same signatures: no other image needed. Tested: 060 then this, both functions equal to a database at 057 | none |
| [`banks_058.sql`](banks_058.sql) | 058: `gold.not_applicable_metrics` back to 049's body (Schwab, American Express and Synchrony no longer banks; Ally stays one by its SIC) | none for the readings; the `financials-rebuild` job puts their fallback debt tags back into gold |
| [`currency_readings_057.sql`](currency_readings_057.sql) | 057: `gold.not_meaningful` back to 035's four arguments (the five-argument one dropped), `gold.latest_reading` to 056's, `gold.filing_figures` to 051's (no `unit`), `ops.check_financials` to 050's; `gold.amount_currency` dropped. Deploy the previous query-api image with it. Tested: 057 then this, those functions equal to a database at 056 | none (then `ops.check_financials` over every company for 050's debt findings) |
| [`ifrs_currencies_056.sql`](ifrs_currencies_056.sql) | 056: the IFRS tags and their sum removed, every silver fact in an ifrs-full tag or a currency other than the dollar deleted, and the seven functions it replaced back to 011's, 055's, 049's, 035's and 036's bodies (`gold.latest_reading` loses `unit`: deploy the previous query-api image with it). Tested: 056 then this, every function equal to a database at 055 | `financials-rebuild` job |
| [`filing_xbrl_facts_055.sql`](filing_xbrl_facts_055.sql) | 055: every silver fact read from a filing's own XBRL deleted, the company tags and member sums removed, `gold.usable_facts` back to 050's body; the tables and functions stay, unused. Suspend the `filing-facts` CronJob first | `financials-rebuild` job |
| [`filers_debt_050.sql`](filers_debt_050.sql) | 050: `gold.usable_facts` back to 043's body (no co-registrant attribution, debt with the filing's sign, slipped debt parts kept), `ops.check_financials` to 041's; `gold.companies_for_ciks` stays but returns only the CIKs' own companies | `financials-rebuild` job |
| [`kpis_049.sql`](kpis_049.sql) | 049: the cash flow subtotals, contract balances, RPO, payables and cash and investments removed, `gold.refresh_derived_financials` back to 048's body and `gold.not_applicable_metrics` to 035's (`gold.figure_facts` stays: see its header). Tested on 60 companies of the sample: gold equal to before 049 row for row | `financials-rebuild` job |
| [`bank_metrics_048.sql`](bank_metrics_048.sql) | 048: a bank's metrics, tags and rows removed, `gold.refresh_derived_financials` back to 040's body | `financials-rebuild` job |
| [`interest_expense_043.sql`](interest_expense_043.sql) | 043 alone: interest expense read with the filing's sign again, a negative derived quarter kept (back to 039's two functions) | `financials-rebuild` job |
| [`gold_rules_038_041.sql`](gold_rules_038_041.sql) | 038's tags and sums, 039's fact selection and computed gross profit, 040's net debt and annual TTM, 041's checks (back to 031, 014 and 023; with 043 applied, it undoes 043 too) | `financials-rebuild` job ([helm chart](../../deploy/helm/tributary/README.md)) |
| [`segments_speed_062.sql`](segments_speed_062.sql) | 062: `gold.refresh_segment_financials` back to 053's body (the same rows; refreshes quadratic in the companies of a call again). Before 053's, never after. Tested: 062 then this, the function equal to 053's | none |
| [`segments_same_basis_053.sql`](segments_same_basis_053.sql) | 053: `gold.refresh_segment_financials` back to 018's body (derived quarters from the latest filings again). After 062's; independent of the chain. Tested on JPM, NVDA, DECK and VRT: gold equal to 018's row for row | the segments refresh (db/README "053") |
| [`insider_queue_059.sql`](insider_queue_059.sql) | 059: `silver.xml_date` and `silver.refresh_insider_filings` back to 054's bodies, `ops.document_outbox`, `ops.insider_queue` and `ops.insider_findings` dropped. Deploy the previous doc-fetcher image first, once the outbox is empty | none |

## Order

Newest first, always. To undo a migration, run every file above it in this list first, then it, and
the rebuild its row names once, after the last file:

1. `revenue_part_064.sql`
2. `rebuild_speed_063.sql`
3. `currency_speed_061.sql`
4. `annual_currency_060.sql`
5. `banks_058.sql`
6. `currency_readings_057.sql`
7. `ifrs_currencies_056.sql`
8. `filing_xbrl_facts_055.sql`
9. `filers_debt_050.sql`
10. `kpis_049.sql`
11. `bank_metrics_048.sql`
12. `interest_expense_043.sql`
13. `gold_rules_038_041.sql`

Two groups stand apart, replacing functions the list above never touches, so each runs at any point,
once, newest first within it: `segments_speed_062.sql` then `segments_same_basis_053.sql` (both
`gold.refresh_segment_financials`: 062's file puts back 053's body, 053's then 018's), and
`insider_queue_059.sql` (`silver.xml_date`, `silver.refresh_insider_filings`; it also refuses while
`ops.document_outbox` still owes an event).

Each file starts with a guard that refuses, changing nothing, unless the database is exactly where that
file belongs: every function any file above replaces or drops must have the body (md5 of its source)
it has once the files above have run and this one hasn't. Run out of order, a file would put back an
older function over a newer rule: 048's, 049's or 050's alone on a database at 056 or 057 brought back
pre-056 functions and dropped the currency rule. Run twice, it refuses too. Why each pair is ordered:

- 064 first: no other file touches `gold.best_facts`, but every guard checks it, so 064's file runs
  before the rest like any newest backout (each later file's guard expects 039's body there).
- 063 before 056: 063's file puts back 056's `silver.refresh_financial_facts` and
  `gold.refresh_derived_financials`; 056's then puts back 011's and 049's.
- 061 before 060: 061's file puts back 060's `gold.amount_currency` and `gold.latest_reading`;
  060's then puts back 057's.
- 060 before 057: 060's file puts back 057's `gold.amount_currency` and `gold.latest_reading`;
  057's then drops the one and puts back 056's other.
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
