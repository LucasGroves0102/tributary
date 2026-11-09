# facts-fetcher

FR2a. XBRL company facts → `bronze.company_facts` → `silver.financial_facts` →
`gold.quarterly_financials`. Two jobs share the bronze format and the set-based SQL refreshes
(`silver.refresh_financial_facts(text[])`, `gold.refresh_quarterly_financials(text[])`,
`db/migrations/011_bulk_financials.sql`; gold ties broken deterministically since
`015_gold_tiebreaks.sql`, latest gold rules in `031_gold_one_quarter_series.sql`: one
non-overlapping series of quarters per company and metric, see [db](../../db/README.md)). The tracked-company list and CIK mapping come from
[company-loader](../company-loader); the [Query API](../query-api) serves the gold rows.

**`facts-fetcher`**: the companyfacts API for every CIK of every tracked company, one
transaction per company. Unchanged payloads (same SHA-256) only bump `last_checked_at`.

**`facts-fetcher-bulk`**: every SEC filer, from the nightly `companyfacts.zip` (one request,
streamed to `$BULK_DIR`; `If-Modified-Since` skips an unchanged file). Entries are read one at
a time; only those whose hash differs from the stored payload are parsed and written, in
transactions of 50 that also advance the checkpoint in `bronze.bulk_loads`. A run stops after
`--limit` changed entries (`$BULK_LIMIT`, default 500) and the next run resumes after the last
committed entry, so the first load is a series of short runs and a crash loses one batch at
most. When a run reaches the end it also builds gold for companies that have silver facts but
no gold rows (a CIK mapped after its facts were loaded). `--zip PATH` loads a local file;
`--dir` sets the download directory. `--rounds N --pause-seconds S` (`$BULK_ROUNDS`,
`$BULK_PAUSE_SECONDS`) runs up to N batches from one download with a pause between them,
stopping once the file is fully loaded.

Bronze keeps, per distinct payload, only the facts of tags in `silver.metric_tags`
(`payload`, what SQL reads) plus the full payload gzip-compressed (`raw_gzip`) for the latest
payload of each CIK. Measured on a 400-company sample of the bulk file, extrapolated to all
~20k entries: full payloads as jsonb ≈ 4 GB, gzip ≈ 1.4 GB, mapped facts ≈ 0.26 GB.

`facts-fetcher --rebuild` skips the SEC: it re-projects each CIK's stored raw payload with the
current `silver.metric_tags`, then re-derives silver and gold. Use it after changing the tags
or the gold rules. `--ciks 0001045810,0000320187` limits it to those CIKs, to look at a few
companies first. On k3s it's the suspended `financials-rebuild` CronJob
([helm chart](../../deploy/helm/tributary/README.md)).

Which fact gold believes is one SQL function, `gold.best_facts` (migration 039, over
`gold.usable_facts`): the quarters, the annual fallback for TTM (040) and the checks' annual
comparison (041) all read it. A line a filing reports as two tags (a bank's net interest plus
noninterest income; NVIDIA's debt plus equity securities) is a `silver.metric_sums` row whose parts
are `silver.metric_tags` rows with `gold_line = false` (038).

A filing's facts are the company's whose CIK the filing's own XBRL names (migration 050): since July
2026 the SEC files some multi-registrant 10-K/10-Qs only under a co-registrant's CIK (DTE Energy's under
DTE Electric), so `gold.usable_facts` moves them to the named filer when that CIK filed it, and after a
payload changes both jobs refresh `gold.companies_for_ciks` of the changed CIKs (`db.company_ids`): the
CIKs' own companies plus any whose filing the SEC put under them. The named entity comes from the stored
document's extract (`bronze.filing_xbrl.entity_cik`, the segments job), so it applies to filings the
document crawl and doc-fetcher store.

## Foreign filers and currencies

Migration 056 maps the core IFRS tags (`ifrs-full:Revenue`, `ProfitLossFromOperatingActivities`,
`ProfitLoss`, `Assets`, `Equity`, `CashAndCashEquivalents`, `LongtermBorrowings`,
`CashFlowsFromUsedInOperatingActivities`, `PurchaseOfPropertyPlantAndEquipment...`, EPS and the rest)
and lets silver keep amounts in any currency, so a 20-F or 40-F filer's companyfacts reach gold: the
`financials-rebuild` job re-projects every stored payload with the new tags (`payload.project` keeps
the (taxonomy, tag) pairs of `silver.metric_tags`; no SEC request). Each company is read in its
reporting currency only (`gold.usable_facts`), and gold never splits a year or a half into quarters,
so a filer of annual reports has fiscal years and year-end balances, and one whose 6-Ks tag quarters
has those. Rebuilt from production's payloads of the 517 listed 20-F/40-F filers without gold: 490
get figures (74 s locally), Birkenstock's FY2025 revenue €2,097.4M and its December 2025 quarter's
€401.9M among them.

## Filings companyfacts lacks

**`facts-fetcher-filings`** (`filings.py`, `instance.py`; migration `055_filing_xbrl_facts.sql`):
reads a filing's own XBRL where SEC companyfacts doesn't carry it. companyfacts never received 52 of
the 12,006 10-K/10-Qs filed in the 200 days to 2026-11-02 (Citigroup's 2026 10-Qs, Clorox's FY2026
10-K, Cardinal Health's, Molson Coors'...), and it never carries a company's own tags or dimensional
facts. A run:

1. **Caught up**: filings read here that companyfacts now has (a filer's latest payload carries one
   of their facts) leave silver: `silver.supersede_filing_facts`, then those CIKs' companyfacts
   payloads are refreshed into silver again and the companies' gold rebuilt. A filing's facts are in
   silver once, from one source.
2. **Gaps**: 10-K and 10-Q filings with XBRL, filed from `--since` (default 400 days back) to
   `--after-days` ago (default 3; companyfacts normally has a filing within a day), at a company
   here, with no fact in silver under any CIK. Amendments only when their stored document has been
   extracted (most are Part III alone). The stored document's extract (`bronze.filing_xbrl.raw_gzip`,
   every numeric fact, from the segments job) when it has at least 10 undimensioned facts of mapped
   tags, else the filing's **instance**: the folder's `index.json`, then `<document>_htm.xml` (EDGAR's
   extract of an inline filing, which holds every document's facts: Clorox's statements are in its
   annual-report exhibit) or, before inline XBRL, the dated `.xml`. Two SEC requests, through
   `libs/sec`'s shared limit (`SEC_CALLER_CLASS`, batch); the instance is stored gzip-compressed in
   `bronze.filing_instances` and never fetched again.
3. **Extras**: the stored documents of companies with their own tags (`silver.metric_tags.company_id`:
   NVIDIA's capex extension tag) or member sums (`silver.member_sums`: Ford's and GM's debt filed only
   by segment), not read yet.
4. `--accessions A,B`: these filings whatever they are, fetching the instance when no document is
   stored (history before the document crawl: NVIDIA's FY2018 and FY2020 10-Ks).

Each filing becomes one `bronze.filing_facts` row: its undimensioned facts of mapped tags (only the
company's own tags when companyfacts has the filing) and the member facts a sum needs, with `source`
(`ixbrl` or `instance`) and `document` (where they were read). `silver.refresh_filing_facts` maps them
into `silver.financial_facts` with `source = 'filing'` (a filing companyfacts lacks) or `'filing_extra'`
(what companyfacts never carries), and `gold.refresh_financials` rebuilds the companies; one
transaction per batch of 25 filings. Re-running changes nothing (delete-then-insert on the accession).
`--rebuild` re-projects every stored row from its source after a change to the tags or sums, without
the SEC.

What the extract reads is what companyfacts carries: on 705 filings both have (the 46 companies the
gaps touch, 2019-2026), 59,054 of 59,097 shared values are identical; the 43 others are a figure
stated twice, rounded in the text and exact in a table, where `ixbrl.py` keeps the first and
companyfacts the more precise (`instance.py` keeps the more precise). companyfacts has 146 facts the
extracts lack (0.25%, mostly EPS), the extracts 15 it lacks.

A member sum is made only when every listed member is in the filing for the date, no other member of
the axis is, and the filing has no undimensioned fact of the concept for that date (that would be the
consolidated figure itself, which companyfacts carries). Why each sum is the consolidated figure is in
migration 055's header (each member is itself a line of the consolidated balance sheet: current
liabilities add up with them in all 42 of Ford's and GM's stored 10-K/10-Qs).

On k3s: the `filing-facts` CronJob (daily 09:30 New York, after the nightly companyfacts loads; ships
suspended). The first run, by hand after the deploy, goes back to 2019 and reads NVIDIA's two
pre-crawl 10-Ks:

```bash
kubectl -n tributary create job --from=cronjob/filing-facts filing-facts-1 \
  --dry-run=client -o json \
  | jq '.spec.template.spec.containers[0].command += ["--since", "2019-01-01",
        "--accessions", "0001045810-18-000010,0001045810-20-000010"]' \
  | kubectl apply -f -
```

Measured for that run on hermes (2026-11-09): 217 10-K/10-Qs since 2019 at companies here with no
companyfacts fact, 143 with a usable extract; about 75 instance fetches (≤ 150 requests) plus
NVIDIA's 2 (4 requests). Then a day's run reads a handful of new gaps. Config (env):
`FILING_FACTS_SINCE`, `FILING_FACTS_AFTER_DAYS`, `FILING_FACTS_LIMIT` (500 gaps and 500 extras per
run), `FILING_FACTS_ACCESSIONS`.

## Segments and KPIs

**`facts-fetcher-segments`** (`segments.py`, `ixbrl.py`, `labels.py`; migration
`018_segments.sql`): revenue, operating income, gross profit, D&A and capex per business
segment, geography and product/service. companyfacts drops every dimensional fact, so these
come from the inline XBRL (iXBRL) of the 10-K/10-Q primary documents that doc-fetcher and the
document crawl already store in `bronze.filing_documents`. **No SEC requests**: the job reads
Postgres only.

| | |
|---|---|
| Input | `bronze.filing_documents` (10-K, 10-K/A, 10-Q, 10-Q/A) |
| Output | `bronze.filing_xbrl` → `silver.segment_facts` → `gold.segment_financials` (view `gold.segments`), `ops.quality_findings` (`segment_sum_*`) |
| Ops | `ops.segment_load_status` (per form: documents, extracted, pending, ok / no_ixbrl / errors) |

Why the documents and not the SEC's Financial Statement and Notes data sets (checked
2026-10-20 against the data set page and its PDF documentation): both carry the dimensional
facts, but the data sets round `ddate` to the month end and `qtrs` to whole quarters (exact dates
only via float offsets `durp`/`datp`, which breaks our start/end and 80-120-day quarter rules),
strip prefixes and the `Member`/`Axis` suffixes from members (`ProductOrService=DataCenter;`)
and truncate them at 1,024 characters, round values to 4 decimals, and arrive monthly (a filing
shows up 1-5 weeks later). Their size: September 2026, a light month, is a 53 MB zip with
`num.tsv` 108 MB and `txt.tsv` 142 MB uncompressed; quarterly zips run to 630 MB, ~35 GB for
the history. The documents give exact dates, full QNames and exact decimals per filing as soon
as the document is stored, at zero extra SEC traffic. Their limit: inline XBRL started in
2019-2021 (phase-in), and the crawl covers `CRAWL_YEARS` (5); deeper history would need the data
sets (or each older filing's separate instance file) later.

How it runs (module docstrings hold the exact rules):

- **Extraction** (`ixbrl.py`, `EXTRACTOR_VERSION`): the gzip body is decompressed and fed to
  an HTML tokenizer in 1 MB chunks; contexts (period, explicit/typed members), units and every
  `ix:nonFraction` are collected and resolved at the end (header anywhere). Values: `format`
  (num-dot-decimal, num-comma-decimal, zero dash), `scale`, `sign`, kept as exact decimals.
  Duplicates (statement + note) keep the first value. Documents without `ix:header` are stored
  as `no_ixbrl`, broken ones as `error` (not retried until the version changes).
- **Bronze** (`bronze.filing_xbrl`, one row per document): `raw_gzip` holds every numeric
  fact; `facts` (what SQL reads) only those dimensioned by `silver.segment_axes` (at most two
  dimensions) plus the undimensioned facts of the same concepts; `labels` maps each member to a
  readable label. NVIDIA's 10-K: 1,028 facts, 13 KB gzip raw, ~110 KB projected JSON.
- **Batches and checkpoint**: pending = documents with no extract for their current
  `content_sha256` and extractor version, in (CIK, accession) order. Batches of
  `SEGMENTS_BATCH` (25) commit the extracts with their silver rows, gold rows and quality
  check; a run stops after `SEGMENTS_LIMIT` (2000) documents; `SEGMENTS_ROUNDS` /
  `SEGMENTS_PAUSE_SECONDS` run several per process. One document is in memory at a time.
- **Labels** (`labels.py`): curated `data/member_labels.csv` first (standard members,
  `aapl:WearablesHomeandAccessoriesMember`), then `country:`/`stpr:` codes, then the local name
  without `Member` and a trailing `Segment`, CamelCase split with acronyms kept
  (`nvda:OEMAndOtherMember` → "OEM and Other", `aapl:IPhoneMember` → "iPhone").
- **Silver** (`silver.refresh_segment_facts`): one row per fact on one axis (a second dimension
  only when it is `ConsolidationItemsAxis = OperatingSegmentsMember`, which filers add to segment
  figures), axis `total` for the consolidated value; metrics from `silver.segment_metric_tags`.
- **Gold** (`gold.refresh_segment_financials`): members matched across filings (same folded
  label, or a member that disappears before another appears with the same restated value),
  then the main pipeline's quarter rules (tag priority, latest filing, Q4 = FY - 9 months when
  80-120 days, so 16-week quarters fit; since 053 a year restated after it was first reported is
  paired with the nine months of its own reporting cycle, so a recast never makes one inflated
  quarter: JPMorgan's FY2022 Q4 CIB), then TTM over four contiguous quarters. `gold.segments`
  adds fiscal and calendar labels with 014's rules.
- **Quality** (`ops.check_segments`): the members of each axis vs `gold.quarterly_financials`
  for the same period end, within 1% (one overlapping subtotal allowed); warnings
  `segment_sum_<axis>`.
- `--rebuild` re-projects `raw_gzip` with the current axes and labels and re-derives silver
  and gold, without reading a document (after changing `segment_axes`, `segment_metric_tags`
  or label rules; label rule changes need it). The web app repairs labels stored before the
  run-on rule (`Assetand Wealth Management`) as it displays them (`displayNames.ts`
  `segmentLabel`, the same rule: change both), so a rebuild for labels alone can wait.

## Run

```bash
make financials                                    # facts-fetcher for tracked companies
make financials-rebuild                            # facts-fetcher --rebuild
make financials-bulk ZIP=/path/to/companyfacts.zip LIMIT=500   # facts-fetcher-bulk, local zip
.venv/bin/facts-fetcher-filings --since 2019-01-01  # filings companyfacts lacks (SEC for instances)
make segments LIMIT=2000                           # facts-fetcher-segments
make segments-rebuild                              # facts-fetcher-segments --rebuild
```

On k3s: `financials` CronJob nightly (04:00 New York) and `financials-bulk` (05:00, 12 rounds
of 500 with 10 minute pauses; ships suspended); `segments` hourly at :20 (ships suspended, running on hermes;
1 Gi memory limit, no SEC access).

Config (env): `DATABASE_URL`, `SEC_USER_AGENT` (both required; the bulk job needs no
`SEC_USER_AGENT` with `--zip`), `BULK_DIR` (default the system temp dir), `BULK_LIMIT`
(default 500), `BULK_ROUNDS` (default 1), `BULK_PAUSE_SECONDS` (default 0), `LOG_LEVEL`.
SEC requests share one rate limit with every other service (`libs/sec`): `SEC_CALLER_CLASS`
(`batch` for both jobs) and `SEC_LIMITER_DATABASE_URL` (default `DATABASE_URL`).

## Tests

`pytest services/facts-fetcher`: `test_ixbrl.py` (extraction, labels, projection) on NVIDIA's
full 10-K/10-Q (the parser's fixtures) and trimmed Apple, Coca-Cola and PepsiCo documents in
`tests/fixtures/ixbrl/` (made with `tests/trim_ixbrl.py`, which streams a filing and keeps only
the segment metric facts and their contexts); `test_segments_db.py` (loader, renames, Q4, TTM,
quality check, rebuild) with `TEST_DATABASE_URL`. `test_gold_selection.py` holds one case per rule of
migrations 038-043 and 050, each shaped like the filing it came from (Flowserve's zero revenue, M&T's bank
revenue, NVIDIA's securities and dividends, Ford's debt, Legacy Housing's 2033 date, ...). Also the full bronze → silver → gold path against trimmed real
companyfacts payloads in `tests/fixtures/` (`test_pipeline.py`; shared `conn` / `loaded`
fixtures in `conftest.py`), and the bulk loader on small zips. `test_filings.py`: instances (trimmed real
ones in `tests/fixtures/instance/`, made with `tests/trim_instance.py`), projection, and with a database the
gap, catch-up, instance, extension-tag and member-sum paths; `test_ifrs.py`: IFRS filers, currencies, half years and
borrowings (056). `test_golden_10k.py` checks gold
against 149 figures printed in five companies' 10-Ks, loading their companyfacts from
`tests/fixtures/golden/` ([tests/golden](../../tests/golden/README.md)). Database tests
need `TEST_DATABASE_URL`.
