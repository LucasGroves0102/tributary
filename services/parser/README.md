# parser

FR3, week 1. Consumes `documents.fetched` (from [doc-fetcher](../doc-fetcher)), reads the
document from `bronze.filing_documents` and its 8-K exhibits from `bronze.filing_exhibits`,
splits it into Part/Item sections and writes `silver.filing_sections` (`parser = python`,
`parser_version` = `PARSER_VERSION`). The [Query API](../query-api) reads sections with
citations from the `gold.filing_sections` view.

| | |
|---|---|
| Input | `documents.fetched` (consumer group `parser-python`), `bronze.filing_documents`, `bronze.filing_exhibits` |
| Output | `silver.filing_sections`: key, part, item, title, text, character offsets into the document text; `silver.filing_tables`: each section's HTML tables, rows and cells with offsets into its text (rule 11, migration 067) |
| Metrics | `tributary_documents_parsed_total{form}`, `tributary_parse_failures_total{form}`, `tributary_parse_duration_seconds{form}` |

Section keys: 10-K `1A`, 10-Q `II-1A`, 8-K `2.02`, 8-K exhibits `EX-99.1`. Re-parsing an
accession replaces its rows from either parser, so a document always has one parser's sections
([parser-rs](../parser-rs) is the Rust build of this service). Production runs the Rust build
(`services.parser.image: parser-rs` in the Helm values, since 2026-10-20); this service is the
reference it must match, the image one value away for a rollback, and what docker-compose runs.
The same image holds the batch jobs below (`parser-index`, `parser-peers`).

The algorithm is specified in the docstring of `src/filing_parser/sections.py` so the Rust
rewrite ([parser-rs](../parser-rs), week 4) can produce identical output for benchmarking. It
handles tables of contents, running page headers, 10-Qs without a "Part I" heading, MD&A
incorporated by reference (JPMorgan, Exxon: rule 7), SGML exhibit envelopes, and 10-K/10-Qs
that title their items without numbering them (rule 9: Molina, Edison International, TriNet
write "RISK FACTORS", never "Item 1A"). Rule 9 only runs when a 10-K or 10-Q has no "Item N"
heading at all, so every filing with numbered items parses exactly as before; it matches whole
lines against the official Form 10-K / 10-Q item titles (`TITLES_10K`, `TITLES_10Q`), takes the
item and Part from the title, and skips a title outside its Part ("Legal Proceedings" inside
Part I's financial statements). About 150 of 94,000 stored 10-K/10-Q documents had no sections
before it.

## Run

```bash
make up-all     # consumer, in docker-compose
make reparse    # parser --reparse-all: re-parse every stored document without Kafka, then exit
```

Use `make reparse` after changing the parsing rules. A document the parser can't split (no
headings) is logged (`skipping <accession>: ...`) and passed, as the consumer does; any other
error stops the run (since v7: a production run over every document must not end at the first
one). In production the Helm CronJob `parser-reparse` runs it with parser-rs.

### Re-parsing only some documents (production)

`--reparse-all` reads every stored document. When a rule change only affects documents that
currently have no sections (rule 9), re-queue just those through the document crawl's outbox
instead: `doc-crawler` publishes `documents.backfill` for every `done` target whose
`published_at` is NULL, 500 per round (every 15 minutes even when idle), and `parser-backfill`
re-parses them. After the new parser image is deployed (both `parser` and `parser-backfill`):

```sql
-- 1. what will be re-parsed: 10-K/10-Q documents without sections (incl. ones that errored)
SELECT d.form, count(*), count(t.accession_no) AS in_crawl_queue
FROM bronze.filing_documents d
LEFT JOIN ops.doc_crawl_targets t ON t.accession_no = d.accession_no AND t.status = 'done'
WHERE d.form ~ '^10-[KQ]'
  AND NOT EXISTS (SELECT 1 FROM silver.filing_sections s WHERE s.accession_no = d.accession_no)
GROUP BY d.form;

-- 2. republish them (the crawler sends them on its next round)
UPDATE ops.doc_crawl_targets t SET published_at = NULL
FROM bronze.filing_documents d
WHERE d.accession_no = t.accession_no AND t.status = 'done' AND d.form ~ '^10-[KQ]'
  AND NOT EXISTS (SELECT 1 FROM silver.filing_sections s WHERE s.accession_no = d.accession_no);

-- 3. progress: unpublished goes to 0, then the parser-backfill lag drains
SELECT unpublished FROM ops.doc_crawl_status;
SELECT parser_version, count(DISTINCT accession_no) FROM silver.filing_sections
WHERE parser_version IN ('python-7', 'rust-7') GROUP BY 1;   -- the current PARSER_VERSION
```

Re-running step 1 afterwards lists the documents rule 9 could not split (no Item headings and no
standard titles). Documents outside the crawl queue (`in_crawl_queue` lower than `count`:
stored by the live doc-fetcher outside the crawl's plan) are only reached by `--reparse-all`.
The hourly `section-index` job picks the new sections up by itself (`ops.section_index_pending`
sees re-parsed filings). Nothing here calls the SEC.

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`METRICS_PORT`, `LOG_LEVEL`, the Kafka authentication variables of [libs/kafka](../../libs/kafka)
(SASL/SCRAM on k3s), `PARSER_TOPIC` / `PARSER_GROUP` (default `documents.fetched` /
`parser-python`). The chart's `parser-backfill` sets them to `documents.backfill` /
`parser-backfill` for the [document crawl](../doc-fetcher/README.md#document-crawl), so crawled
documents are parsed by a separate consumer and never queue ahead of live filings.

## A decimal item run into its title (rule 2, `python-6` / `rust-6`)

An 8-K heading written without a space after its number ("Item 7.01Regulation FD Disclosure") failed
the word boundary after "7.01", so the heading fell back to item 7 with the title "01Regulation FD
Disclosure". A decimal item now ends before anything but a digit ("Item 7.012" is still item 7). On
production (2026-11-10) the rule had keyed 6,233 8-K sections by their first digit instead: 9.01 as 9
(2,497), 2.02 as 2 (1,340), 7.01 as 7 (899), 8.01 as 8 (623), 5.0x as 5 (451), 1.01 as 1 (275), 3.0x
as 3 (120), 4.0x as 4 (28), nearly all of documents in the crawl queue. Stored rows change only when
re-parsed: re-queue just those 8-Ks through the crawl outbox (the runbook has the SQL; about 13 rounds
of 500, a few hours of trickle, then `section-index` re-indexes them) rather than `make reparse`, which
reads all ~396,000 documents and ~217,000 exhibits, rewrites ~2.2M section rows and makes `parser-index`
rebuild every passage and diff (hours on hermes). Both parsers must be deployed first: production
serves parser-rs rows.

## Section titles (rule 10)

`python-5` / `rust-5` clean the stored `title` and nothing else (text, offsets, keys and which
heading is kept are rules 1-9's; the audit's M4): leading separators and invisible marks go ("‑
Exhibits", "| Risk Factors", an LRM, ", “Regulation FD Disclosure”"), a quoted or cross-referencing
title keeps what's between its quotes (Deckers' Item 7, ', “Management’s Discussion …,” of our
Annual' → the title), dot leaders go, and on a 10-K or 10-Q a title cut where its line wrapped
("MANAGEMENT’S") takes the following lines while they continue one of the standard item titles of
`TITLES_10K` / `TITLES_10Q` (at most 3 lines of at most 120 characters). Measured on production's
stored titles (2026-11-09; the rule re-derived from each section's own first lines): a 0.5%
random sample changes 181 of 8,106 titles (2.2%; about 44,000 of 1.97M), all wrapped titles
completed; of the 1,980 titles starting with punctuation other than "[", "(" or "{", 1,968 change
and 109 that were only punctuation become empty (the web app then names the section from its key).
"[Reserved]" (13,878 titles) is a real title and stays. An 8-K whose kept heading is a sentence
(", including Exhibit 99.1, is furnished …") keeps the sentence without its comma: the heading
itself is in the text, which rule 10 doesn't change.

Existing rows change only when re-parsed (`make reparse`, or the crawl outbox for some documents);
a reparse rewrites the sections' rows and so their `parsed_at`, which makes `parser-index` recompute
their search passages and diffs. New filings get clean titles as soon as both parsers are deployed.

## Tables in the text

`html_to_text` (rule 1) ends every block element, `<tr>` included, with a newline and every cell with
a space, so a table row is one line ("Total assets $ 206,803 $ 111,601"; a label whose `<td>` holds a
`<p>` puts its figures on the next line). The web reader reads tables back from that
(`services/web/src/reader/blocks.ts`), and leaves out page numbers and "Table of Contents" lines,
without a reparse. Changing how cells or rows are written changes every stored offset (citations,
the search index, diffs, peers' mentions, thesis pins' `?at=`): don't, unless all of them are rebuilt.
Parser v7 keeps the text exactly and records the tables beside it (next section).

## Tables beside the text (rule 11, `python-7` / `rust-7`)

Beside each section the parser records the HTML tables whose rows lie in it, in
`silver.filing_tables` (migration 067): one row per table with its `[char_start, char_end)` in the
section's text and `rows` as jsonb, each row with its own offsets, its cells' text in order (empty
spacer cells kept, so columns line up: a row's columns are its cells' colspans summed), `spans` when a
cell spans columns, `head` on a `<thead>` row or a row of `<th>` cells. A cell's text is the text's
own characters (rule 11b maps each cell of the HTML tree onto rule 1's text: collapsed spaces,
stripped lines and dropped empty lines included), so `text[row.start:row.end]` is that row's line(s)
and a reader can find the table a quoted passage comes from by offsets. A table needs two rows with
text in the section (single-row tables are bullets and layout: 42% of tables in the sample below);
a table that straddles an Item heading gives each section its own rows.

The text, keys, titles and offsets are rule 1-10's, unchanged (`test_tables_on_real_filings` checks
it on the fixtures), so nothing that reads stored text or offsets changes. A document parsed before
v7 has no table rows until it is parsed again; readers fall back to the text (the Query API's
earnings releases do). The writer (`db.write_sections`, parser-rs `db.rs`) keeps a document's
section rows and their `parsed_at` when a re-parse produces exactly the stored sections (it updates
`parser_version` and replaces the tables only): `parser-index` keys on `parsed_at`, so a re-parse
for tables re-indexes and re-diffs nothing, and What changed never goes stale during it. A section
row deleted (a re-parse with other text, the other parser's rows) deletes its tables (the foreign
key cascades).

Measured on 205 documents and 130 exhibits sampled from hermes' `bronze.filing_documents` and
`filing_exhibits` (2026-11-15): 4,030 tables (42% of them single-row), 29,575 rows; the tables' JSON
is 37% of 10-K and 10-Q section text, 28% of 8-K items' and 54% of exhibits', about 15 GB for every
stored filing before TOAST compression. The walk rebuilt `text_content()` exactly on every sampled
document and on 20,000 fuzzed ones. Python parse time went from 46 ms to 100 ms on NVIDIA's 10-K
(parser-rs does the same walk in the pass that already builds the text).

## Page furniture: left out where the text is read, not stripped (parser v7 decision)

The printed filing's page furniture stays in the stored text: page numbers, "Table of Contents"
back-links and running headers ("Alphabet Inc.", "Notes to Consolidated Financial Statements
(Continued)", "NVIDIA Corporation | 2026 Form 10-K | 29") sit between two halves of a sentence
wherever the page turned. Analyst test 4 found it in a pinned quote ("6 Table of contents Backlog
—", finding 19) and in search (debris in snippets; a phrase across a page break not found).

Measured on hermes (2026-11-15, read-only): a 0.2% sample of stored sections (6,406 sections,
74M characters): page-number lines in 56% of 10-K and 53% of 10-Q sections, "Table of Contents"
lines in 25% and 22% (0.4% of their characters); running headers (rule 3 below) in 6.5% of all
sections. A full reparse means 634,653 documents (433 GB of HTML, 27.7 GB stored gzip) and
346,952 exhibits (84 GB, 4.3 GB) into 3.57M section rows (20 GB with TOAST).

Two ways to fix it: (a) strip the furniture at parse time (rule 1): every section with a page break
after its first one moves its offsets, so every citation, thesis pin `?at=`, search passage
(~12M), What changed diff (14.6M rows) and peers' mention would have to be rebuilt, and pins
already saved in theses would point a few hundred characters off; (b) leave the text and its
offsets alone and leave furniture out where text is read. Chosen: (b). It fixes what the tester saw
with no reparse, no offset moves and no stale diffs: the reader already dropped page numbers and
contents links (web `reader/blocks.ts`), and now one rule, in two copies checked against the same
cases (`services/web/src/reader/furniture.cases.json`), runs where text is read:

- web `reader/furniture.ts`: the reader (`blocks.ts` rule 1), the Business step's paragraphs and
  every pinned quote (`quoteBlock`, `quoteLines`, `quoteTable`);
- SQL `gold.page_furniture_lines` (migration 068): search passages and snippets (next section).

What Changed already skips furniture its own way (`diff.py` rule 1, the Query API's `changes.py`),
and so do the earnings release rules (`releases.py` rule 1, page numbers).

The rule, over a text's non-blank lines: 1. a contents link ("Table of Contents", "Contents",
"Index", optionally "Return to"/"Back to" and "the", optionally a final "."; any case); 2. a page
number ("12", "- 12 -", "Page 12", "F-12", or i-xxxxx in lower case) when the next line is a contents
link, it is the first line, or the line before ends a sentence; 3. a running header: a line of
3-100 characters with a letter whose digits-as-"#" form occurs at least 3 times, at least twice and at
least half the time within two lines of a kind 1-2 line. Without the page-break condition a repeat rule
would take table labels ("Total", "Three Months Ended", "December 31,") six times as often as headers
(same sample). A statement's footer on every page ("See accompanying notes ...") counts as a
running footer.

## Section diffs and search index (`parser-index`)

A batch job in the same image (`filing_parser/index.py`, migration 021) that turns parsed
sections into:

- **Search passages** (`gold.section_search`): every served section cut at line boundaries into
  ~2,000-character passages (`gold.refresh_section_search`), each with its `tsvector` and offsets
  but no text. The [Query API](../query-api/README.md#search)'s `/v1/search` ranks them and cuts
  snippets from `silver.filing_sections`.
- **What changed** (`gold.section_diffs`, `gold.section_changes`): Risk Factors and MD&A (10-K
  `1A`, `7`; 10-Q `II-1A`, `I-2`) compared with the previous 10-K / 10-Q and, for a 10-Q, the
  same quarter a year earlier. Paragraph-level, focused on substance: new, removed and rewritten
  paragraphs (with word-diff opcodes) are stored; unchanged ones, minor edits and number-only
  updates are counted. The algorithm and thresholds are specified in `filing_parser/diff.py`'s
  docstring (bump `ALGORITHM`, now `diff-1`, with any change: every diff is recomputed, oldest
  first, for hours). Diffs are Python, not SQL like the other transforms, because alignment needs
  fuzzy matching and difflib. The Query API reads the stored diff a second time on each request
  ([What changed](../query-api/README.md#what-changed): page furniture, split paragraphs, calendar
  rollovers, periods, ranking), so a noise or ranking rule belongs there and needs no recompute.

Incremental: `ops.section_index` is the checkpoint (one row per filing: when its sections were
parsed, which filings its diffs used). `ops.section_index_pending` returns filings whose sections
are new or re-parsed, and filings whose comparable filing changed (it arrived later, since the
crawl goes newest first, or was re-indexed) or whose diffs come from another algorithm version.
Each batch of filings commits with its passages, diffs and checkpoint rows, so a stopped run
resumes and a re-run is a no-op. One run at a time (advisory lock).

```bash
make section-index LIMIT=5000   # parser-index --limit 5000 [--batch 25]
parser-index --passages         # only the passages built before migration 068 (below)
```

### Search without page furniture (migration 068)

`gold.refresh_section_search` blanks each section's page furniture (the rule above, over the whole
section, so a running header counts all its pages) before `to_tsvector`: the lines become spaces,
one for one, so offsets stay and "procure sufficient" / "14" / "Table of Contents" / "foundry
capacity" is the phrase "sufficient foundry capacity" again. Each passage stores its blanked lines
(`gold.section_search.furniture`, [start, end) pairs relative to the passage); the snippet and 065's
exact-phrase check read the passage through `gold.blank` with them, or, for a passage built before
068 (`furniture` NULL), with the rule run on the passage alone, so snippets are clean at once.
`ops.section_index.passages_rule` = `furniture-1` marks filings whose passages were built this way.
Cost, NVIDIA's five fixture filings (1.07M characters, 568 passages, 647 furniture lines): 0.19 s
against 021's 0.22-0.27 s (fewer tokens to index).

Old passages keep their debris tokens (and miss phrases across page breaks) until rebuilt:
`parser-index --passages` (Helm CronJob `section-passages`, by hand) rebuilds the passages of filings
whose `passages_rule` isn't the current one, `--limit` filings per run in `--batch` transactions,
under the same advisory lock as `section-index`. It leaves `indexed_at`, `diffed_at` and the diffs
alone, so nothing is diffed again and What changed never goes stale. Then `REINDEX INDEX
CONCURRENTLY gold.section_search_tsv` (the bulk rewrite bloats the GIN index).

Config: `DATABASE_URL`, `SECTION_INDEX_LIMIT` (default 5000; 20,000 in the Helm values),
`SECTION_INDEX_BATCH` (25). Helm: CronJob `section-index` (hourly at :40; ships suspended, the Flux
release turns it on). Progress: `ops.section_index_status`; counts per compared section:
`ops.section_change_summary`.

Cost on a sample of 78 real filings (10 companies, 12.8 MB of section text): 3 s of
`to_tsvector` for passages, ~35 ms per section diff. Search table + indexes: ~1.3x the raw
section text when the GIN index is freshly built (tsvectors ~1x, GIN 0.25x, b-trees 0.04x); the
GIN index grows to ~0.5-0.7x under incremental inserts, so REINDEX it after a bulk load.

## Peers by business

`parser-peers`, a weekly batch job in the same image (`filing_parser/business.py`, its docstring is
the spec; `ALGORITHM` stored with each profile; migration 047), compares what companies say they
do. For every company with financials it
reads Item 1 (Business) of its latest 10-K from the last three years that has a usable one (at
least 3,000 characters and not mis-parsed: Intel's, GE's and McDonald's are cross-references, so no profile),
turns the first 80,000 characters into words and two-word phrases (stop words, places and
human-capital words removed, simple singulars), and weights them TF-IDF: terms in 20 to 25% of the
documents, the company's own name and corporate-structure phrases (SPAC mergers, share classes)
dropped, the 400 heaviest kept and normalised. Then:

- `gold.business_profiles`: the filing and section each profile came from, its most distinctive terms;
- `gold.business_peers`: each company's 60 most similar companies (cosine), with the shared terms
  that add the most, so the [Query API](../query-api/README.md#peers) can say why;
- `gold.business_mentions`: other companies its Item 1 names in a sentence about competition (SEC
  names and former names, normalised like `silver.normalize_name`; a one-word name counts only
  capitalised and when it isn't an ordinary word), with the sentence's offsets to cite. Vertiv's
  names Eaton and Johnson Controls.

Everything is replaced in one transaction per run (readers see old or new); one run at a time
(advisory lock). Cost on hermes' 4,888 usable Item 1s of 2026-11-09, run locally: 79 s, 406 MB
peak, 293,280 peer rows and 4,166 mentions, 60 MB with indexes; the read itself took 6 s on hermes.
No TF-IDF library: the standard library only, no paid embeddings, no external calls.

How the settings were chosen: precision at 10 against hand-written peer lists for nine companies
(VRT, DECK, NVDA, JPM, RCKY, KO, UNH, HD, XOM), on the full corpus, through the Query API's ranking.
A minimum document frequency of 20 scored 47 of 90, 10 scored 45, 5 scored 44; 250 vs 400 terms and
40,000 vs 80,000 characters moved it by at most one. XOM scores 1: Exxon's Item 1 is 6,600
characters, mostly pointers to the rest of its 10-K.

```bash
parser-peers   # DATABASE_URL; no options
```

Helm: CronJob `business-peers` (Sundays 05:30 ET, not suspended, 1 GiB limit); run it once by hand
after the deploy that adds migration 047. Until a run has stored peers, the Query API ranks them by
SIC and size.

## Parser v7 rollout (runbook)

For the owner (`sudo kubectl` on hermes, SQL as the DB owner); measured on hermes 2026-11-15, read-only.

### What ships

| Piece | Change | Needs |
|---|---|---|
| migration 067 | `silver.filing_tables` (+ RLS, GRANT, FK to `silver.filing_sections` ON DELETE CASCADE) | nothing; additive |
| migration 068 | `gold.section_search.furniture`, `ops.section_index.passages_rule`, `gold.page_furniture_lines` / `page_furniture` / `blank`, `gold.refresh_section_search` and the 10-argument `gold.search_page` redefined | a moment's ACCESS EXCLUSIVE on `gold.section_search` and `ops.section_index` (lock_timeout 3 s) |
| parser-rs `rust-7` (`parser`, `parser-backfill`, new `parser-reparse`) | records tables; keeps unchanged section rows and their `parsed_at` on a re-parse | both Deployments roll with the image |
| parser image `python-7` (`section-index`, `business-peers`, new `section-passages`) | passages built without furniture; `--passages` | nothing else |
| query-api | `/earnings?tables=true` → `parts` (grids); search snippets blanked | after the migrations (the deploy hook runs them first) |
| web | Earnings tab tables; pins and Business paragraphs without furniture | the new query-api (an older one ignores `tables`) |

The stored text, keys, titles and offsets do not change: no pin, citation, passage offset or diff
moves, and a re-parse that changes nothing keeps `parsed_at`, so `section-index` re-indexes
nothing for it.

### 0. Before (read-only)

```sql
SELECT parser_version, count(DISTINCT accession_no) FROM silver.filing_sections GROUP BY 1 ORDER BY 1;
-- 2026-11-15: rust-3 267,972 documents, rust-4 87,983, rust-5 58,691, rust-6 219,246
SELECT pg_size_pretty(pg_database_size('tributary'));   -- 155 GB; / had 168 GB free
SELECT * FROM ops.section_index_status;                 -- section-index caught up?
```

### 1. Deploy (merge, Flux) - minutes

Merge when no `section-index` Job is running (it runs hourly at :40; its long transactions hold
`gold.section_search`): `sudo kubectl -n tributary get jobs | grep section-index`. If 068's hook
fails on `lock_timeout`, re-run the release (`flux reconcile helmrelease tributary -n flux-system`
or the usual way); nothing is half-applied (each migration is one transaction).

Check:

```sql
SELECT version FROM public.schema_migrations ORDER BY 1 DESC LIMIT 2;  -- 068, 067
SELECT parser_version, count(*) FROM silver.filing_sections
WHERE parsed_at > now() - interval '1 hour' GROUP BY 1;            -- rust-7 appears as filings arrive
SELECT count(*) AS tables, count(DISTINCT accession_no) AS documents FROM silver.filing_tables;
```

From here every new filing has its tables; the Earnings tab shows a release's outlook and
reconciliation tables as tables once its release is parsed with v7, and the text as before until
then. Pinned quotes and the Business step lose page furniture at once (web only). Search snippets
lose it at once (068 blanks old passages at query time); phrases across page breaks need step 4.

### 2. Earnings releases since July 2024 (recommended) - ~20 h of background trickle

Re-queues the 2.02 8-Ks through the crawl outbox (`doc-crawler` publishes `documents.backfill` 500
per round, about every 15 minutes once the crawl is idle; `parser-backfill` parses them). 38,851 such
8-Ks were stored on 2026-11-15, 38,154 of them in the crawl queue (1.4 GB documents + 18.7 GB
exhibits): ~78 rounds, ~20 hours. Unchanged sections keep their rows (no re-index); every one gains
its tables.

```sql
-- 1. how many (expect ~38,000)
SELECT count(*) FROM ops.doc_crawl_targets
WHERE status = 'done' AND form = '8-K' AND filing_date >= '2024-07-01'
  AND '2.02' = ANY(string_to_array(items, ','));
-- 2. queue them
UPDATE ops.doc_crawl_targets SET published_at = NULL
WHERE status = 'done' AND form = '8-K' AND filing_date >= '2024-07-01'
  AND '2.02' = ANY(string_to_array(items, ','));
-- 3. progress: unpublished goes to 0, then parser-backfill's lag drains
SELECT unpublished FROM ops.doc_crawl_status;
SELECT count(DISTINCT t.accession_no) FROM silver.filing_tables t
JOIN ops.doc_crawl_targets c USING (accession_no)
WHERE c.form = '8-K' AND '2.02' = ANY(string_to_array(c.items, ','));
```

Same route, same SQL shape, for the 8-Ks still keyed by their item's first digit ("Item
7.01Regulation": fixed in python-6/rust-6, stored rows still old): 6,064 sections in 3,465 documents
on 2026-11-15 (rust-3 3,779, rust-4 1,257, rust-5 1,028), ~7 rounds, ~2 hours. These do change, so
`section-index` re-indexes them by itself.

```sql
UPDATE ops.doc_crawl_targets t SET published_at = NULL
WHERE t.status = 'done' AND t.accession_no IN (
    SELECT s.accession_no FROM silver.filing_sections s
    JOIN bronze.filing_documents d USING (accession_no)
    WHERE d.form LIKE '8-K%' AND s.section_key ~ '^[1-9]$'
      AND s.text ~* '^item [0-9]\.[0-9]{2}[a-z]');          -- the SELECT takes ~1-2 min
```

Skip step 2 if step 3 is run soon: it covers both.

### 3. Everything (optional) - roughly 15 hours to 2.5 days

`parser-reparse` (`parser --reparse-all`, parser-rs, one process, oldest filing first, no Kafka)
re-parses all 634,653 documents and their 346,952 exhibits (517 GB of HTML). Measured throughput of
the Rust service on hermes: its busiest minute 273 documents and 602 MB of HTML (parser-backfill,
2026-11-09 21:08 UTC), ~11,000 documents an hour sustained while the crawl fed it: 517 GB at ~10
MB/s is ~15 hours, 634,653 documents at ~11,000 an hour ~58 hours. It does more per document than
before (it reads back the stored sections to compare). Plan on a weekend.

```bash
sudo kubectl -n tributary create job --from=cronjob/parser-reparse parser-reparse-1
sudo kubectl -n tributary logs job/parser-reparse-1 -f | head   # one JSON line per document
```

```sql
SELECT parser_version, count(DISTINCT accession_no) FROM silver.filing_sections GROUP BY 1 ORDER BY 1;
-- rust-7 grows towards ~634,000; documents whose sections change (rules 2, 9, 10 since their last
-- parse: most rust-3/4/5 rows' titles) get a new parsed_at and section-index re-indexes them
SELECT count(*) FILTER (WHERE parsed_at < '<deploy time>') AS kept,
       count(*) FILTER (WHERE parsed_at >= '<deploy time>') AS rewritten
FROM silver.filing_sections WHERE parser_version = 'rust-7';
```

Effects while it runs: the DB is busy (reads 32 GB of gzip, writes ~15 GB of table JSON before TOAST
compression, ~4-6 GB stored; WAL); readers are unaffected. Rewritten documents are re-indexed by the
hourly `section-index` (their What changed diffs show `stale` until then: only those). The weekly full
backup grows by the tables (the nightly slim one dumps `silver.filing_tables` schema-only). Stop it
any time (`sudo kubectl -n tributary delete job parser-reparse-1`): a document is written in one
transaction, and a re-run redoes everything from the oldest (it has no resume point).

### 4. Search passages without page furniture - ~3-5 hours

```bash
sudo kubectl -n tributary create job --from=cronjob/section-passages section-passages-1
# repeat (section-passages-2, ...) until its last line says "complete"; 100,000 filings per run
```

```sql
SELECT passages_rule, count(*) FROM ops.section_index GROUP BY 1;   -- NULL goes to 0
-- then, as the owner, outside the :40 run
REINDEX INDEX CONCURRENTLY gold.section_search_tsv;
```

Estimate: 021's first load indexed ~265,000 filings in about an hour; 068's refresh costs the same
per passage (measured locally: 0.19 s vs 0.22-0.27 s for NVIDIA's five filings), for ~634,000
filings: ~3 hours, plus the REINDEX (~1 hour). It never touches diffs or the checkpoint's
`indexed_at`/`diffed_at`. It shares `section-index`'s advisory lock: one waits for the other.

### 5. Verify

```sql
-- tables for an earnings release (Vertiv Q3 2026: 20 tables)
SELECT table_no, char_start, char_end, jsonb_array_length(rows) AS rows
FROM silver.filing_tables WHERE accession_no = '0001628280-26-067707' AND section_key = 'EX-99.1'
ORDER BY 1 LIMIT 5;
-- What changed not stale beyond what is waiting for section-index
SELECT count(*) FROM gold.section_diffs d
JOIN silver.filing_sections s ON s.accession_no = d.accession_no
 AND s.section_key = d.section_key AND s.parser = d.parser
WHERE s.parsed_at <> d.parsed_at;
-- a phrase across a page break (NVIDIA FY2026 10-K Item 1A), once step 4 has rebuilt it: 1
SELECT count(*) FROM gold.section_search
WHERE accession_no = '0001045810-26-000021' AND section_key = '1A'
  AND tsv @@ phraseto_tsquery('english', 'procure sufficient foundry capacity');
-- the decimal items: 0 once step 2's second part (or step 3) is through
SELECT count(*) FROM silver.filing_sections s JOIN bronze.filing_documents d USING (accession_no)
WHERE d.form LIKE '8-K%' AND s.section_key ~ '^[1-9]$' AND s.text ~* '^item [0-9]\.[0-9]{2}[a-z]';
```

In the app: Vertiv's Earnings tab (Q3 2026): the Q4 and full-year guidance and the reconciliations
are tables; search `"sufficient foundry capacity"` (NVIDIA) finds Item 1A; pin a paragraph from
Vertiv's Business step: no "6 Table of contents".

### Rollback

Images: set the parser images back (`parser-rs` at the previous tag, or `parser`) in the Flux release.
An older parser deletes and rewrites a document's sections when it parses it (its tables go with them
by the cascade); stored rows are otherwise untouched. An older query-api ignores the new columns and
tables; an older web never sends `tables`. Migrations 067/068 are additive and can stay; to drop them
by hand: `DROP TABLE silver.filing_tables`; re-create 021's `gold.refresh_section_search` and 065's
`gold.search_page` from those files, then drop `gold.blank`, `gold.page_furniture`,
`gold.page_furniture_lines` and the two columns.

## Tests

`pytest services/parser`: `test_business.py` runs the peers rules on the openings of ten real 10-K
Item 1 sections (`tests/fixtures/business`, hermes 2026-11-09: Vertiv, Eaton, nVent, Deckers, Crocs,
Nike, JPMorgan, Bank of America, Intel's cross-reference, Microchip's mis-parse);
`test_business_db.py` runs `parser-peers` on them in Postgres (rerun replaces, stub skipped, RLS); `test_sections.py` parses real NVIDIA 10-K, 10-Q and 8-K documents
(`tests/fixtures/*.htm.gz`), three real Q3 2026 10-Qs with titled but unnumbered items for rule 9
(`moh-`, `eix-`, `tnet-10-Q.htm.gz`: Molina, Edison International, TriNet; the hidden inline-XBRL
header is emptied to save space, the extracted text is unchanged), plus small synthetic cases for
the quirks above; `test_db.py` needs
`TEST_DATABASE_URL`. `test_diff.py` runs the diff rules on real NVIDIA sections (fiscal 2026 vs
2025 10-K; Q2 FY2027 10-Q vs Q1 and vs Q2 FY2026); `test_index_db.py` runs `parser-index` on
them in Postgres (incremental runs, late priors, re-parses, algorithm bumps).
`test_rust_service.py` runs both parser services on the same stored documents and expects
identical section and table rows, and the Rust service keeping `parsed_at` on an unchanged re-parse
(`PARSER_RS_BIN`, `TEST_DATABASE_URL`); `test_sections.py`'s `test_tables_*` are rule 11 (synthetic
cases, and on the fixtures each row's text equals its cells and the walk equals `text_content()`;
`services/parser-rs/tests/sections.rs` copies them); `test_db.py` the table rows and the kept
`parsed_at`; `test_index_db.py` migration 068 (the furniture rule against the web's cases, a phrase
across NVIDIA's page break, `--passages` leaving diffs alone); `test_rust_tls.py` checks that the Rust
parser connects or fails for every `sslmode` exactly where libpq does, against a TLS-only server
(CI).
