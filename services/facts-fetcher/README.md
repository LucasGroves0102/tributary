# facts-fetcher

FR2a. XBRL company facts → `bronze.company_facts` → `silver.financial_facts` →
`gold.quarterly_financials`. Two jobs share the bronze format and the set-based SQL refreshes
(`silver.refresh_financial_facts(text[])`, `gold.refresh_quarterly_financials(text[])`,
`db/migrations/011_bulk_financials.sql`; gold ties broken deterministically since
`015_gold_tiebreaks.sql`). The tracked-company list and CIK mapping come from
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
or the gold rules.

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
  80-120 days, so 16-week quarters fit), then TTM over four contiguous quarters. `gold.segments`
  adds fiscal and calendar labels with 014's rules.
- **Quality** (`ops.check_segments`): the members of each axis vs `gold.quarterly_financials`
  for the same period end, within 1% (one overlapping subtotal allowed); warnings
  `segment_sum_<axis>`.
- `--rebuild` re-projects `raw_gzip` with the current axes and labels and re-derives silver
  and gold, without reading a document (after changing `segment_axes`, `segment_metric_tags`
  or label rules; label rule changes need it).

## Run

```bash
make financials                                    # facts-fetcher for tracked companies
make financials-rebuild                            # facts-fetcher --rebuild
make financials-bulk ZIP=/path/to/companyfacts.zip LIMIT=500   # facts-fetcher-bulk, local zip
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
quality check, rebuild) with `TEST_DATABASE_URL`. Also the full bronze → silver → gold path against trimmed real
companyfacts payloads in `tests/fixtures/` (`test_pipeline.py`; shared `conn` / `loaded`
fixtures in `conftest.py`), and the bulk loader on small zips. `test_golden_10k.py` checks gold
against 149 figures printed in five companies' 10-Ks, loading their companyfacts from
`tests/fixtures/golden/` ([tests/golden](../../tests/golden/README.md)). Database tests
need `TEST_DATABASE_URL`.
