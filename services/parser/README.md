# parser

FR3, week 1. Consumes `documents.fetched` (from [doc-fetcher](../doc-fetcher)), reads the
document from `bronze.filing_documents` and its exhibits from `bronze.filing_exhibits`,
splits it into Part/Item sections and writes `silver.filing_sections` (`parser = python`,
`parser_version` = `PARSER_VERSION`). The [Query API](../query-api) reads sections with
citations from the `gold.filing_sections` view.

| | |
|---|---|
| Input | `documents.fetched` (consumer group `parser-python`), `bronze.filing_documents`, `bronze.filing_exhibits` |
| Output | `silver.filing_sections`: key, part, item, title, text, character offsets into the document text; `silver.filing_tables`: each section's HTML tables, rows and cells with offsets into its text (rule 11, migration 067) |
| Metrics | `tributary_documents_parsed_total{form}`, `tributary_parse_failures_total{form}`, `tributary_parse_duration_seconds{form}` |

Section keys: 10-K `1A`, 10-Q `II-1A`, 8-K `2.02`, 20-F `3.D` (its items 1-19 like a 10-K's),
6-K and 40-F `6-K` / `40-F` (the whole primary document), exhibits `EX-99.1`. Re-parsing an
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
write "RISK FACTORS", never "Item 1A"). Rule 9 runs only when a document has no "Item N"
heading at all ([DESIGN.md](DESIGN.md#items-titled-but-not-numbered-rule-9)). Foreign filers
(migration 095, [below](#foreign-filers-rules-12-and-13)): a 20-F's items with Item 3.D, its risk
factors (rule 12), and a 6-K's or 40-F's primary document as one section beside its exhibits (rule 13).

Why each rule is what it is, what each version measured on production's stored filings, and the
runbooks: [DESIGN.md](DESIGN.md).

| Version | Change (stored rows change only when re-parsed) |
|---|---|
| `python-5` / `rust-5` | section titles cleaned (rule 10: separators, quotes, dot leaders, wrapped titles); [DESIGN.md](DESIGN.md#section-titles-rule-10) |
| `python-6` / `rust-6` | a decimal item run into its title ("Item 7.01Regulation") keeps its number (rule 2); [DESIGN.md](DESIGN.md#a-decimal-item-run-into-its-title-rule-2-python-6--rust-6) |
| `python-7` / `rust-7` | tables recorded beside the text (rule 11, migration 067); text, keys, titles and offsets unchanged |
| `python-8` / `rust-8` | Form 20-F items and 3.D (rule 12), 6-K and 40-F documents whole (rule 13); 10-K, 10-Q and 8-K output unchanged |

## Run

```bash
make up-all     # consumer, in docker-compose
make reparse    # parser --reparse-all: re-parse every stored document without Kafka, then exit
```

Use `make reparse` after changing the parsing rules locally. A document the parser can't split (no
headings) is logged (`skipping <accession>: ...`) and passed, as the consumer does, so a run over
every document doesn't end at the first one (since v7); any other error stops the run. In
production nothing is run by hand: [catch-up](#catch-up) re-queues every document an older
`PARSER_VERSION` split. The Helm CronJob `parser-reparse` (parser-rs, everything at once) is the
emergency tool.

To re-parse only some documents (those without sections, or one form and item) without
`--reparse-all`, re-queue them through the document crawl's outbox:
[DESIGN.md](DESIGN.md#re-parsing-only-some-documents-production) has the SQL.

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`METRICS_PORT`, `LOG_LEVEL`, the Kafka authentication variables of [libs/kafka](../../libs/kafka)
(SASL/SCRAM on k3s), `PARSER_TOPIC` / `PARSER_GROUP` (default `documents.fetched` /
`parser-python`). The chart's `parser-backfill` sets them to `documents.backfill` /
`parser-backfill` for the [document crawl](../doc-fetcher/README.md#document-crawl), so crawled
documents are parsed by a separate consumer and never queue ahead of live filings.

## Foreign filers (rules 12 and 13)

doc-fetcher stores covered foreign filers' 20-F and 40-F (+ /A) and their results-release 6-Ks
(migration 095). A 20-F numbers its items like a 10-K (1-19, 4A, 16A-16K, keys without a Part) and
letters sub-items under them: "Item 3.D" / "D. Risk Factors", "Item 5.A Operating Results".
Rule 12 drops sub-item headings ("Item N.X", or a lettered number other than 4A and 16A-16K) before
the table of contents is found, so a sub-item stays in its item and a contents table listing them
isn't read as the body; then Item 3 is split at its first 3.D line ("D. Risk Factors", "3.D RISK
FACTORS", "Item 3.D. Risk factors", a bare "Risk Factors"), giving the `3.D` section. A 20-F with
no Item heading (an annual report with a Form 20-F cross-reference index) is one `20-F` section.
Rule 13: a 6-K's or 40-F's primary document is one section keyed by the form (a 6-K's release is
often its EX-99.1; a 40-F's annual information form, MD&A and statements are its EX-99.x, rule 8).
The checks (072, replaced in 095) know their keys; an original 20-F's core items are 3.D, 4, 5 and
18 unless it is laid out by a cross-reference index. Not yet: Item 18 pointing to F-pages placed
after Item 19 (the statements then sit in section 19), What changed for 20-Fs, peers by business
from Item 4. The test documents are shaped like real 20-Fs, 6-Ks and 40-Fs, not copied: none was
stored on hermes when the rules were written; replace them with stored ones once the crawl has some.

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
section's text and `rows` as jsonb, each row with its own offsets and its cells' text in order
(spacer cells kept, `spans` for colspans, `head` for header rows). A table needs two rows with
text. The text, keys, titles and offsets are unchanged, so nothing that reads stored text moves;
a document parsed before v7 has no table rows until it is parsed again (readers fall back to the
text). A re-parse that produces exactly the stored sections keeps their rows and `parsed_at`
(only `parser_version` and the tables change), so `parser-index` re-indexes nothing for it.
Rule details and measurements: [DESIGN.md](DESIGN.md#tables-beside-the-text-rule-11-python-7--rust-7).

## Page furniture: left out where the text is read, not stripped (parser v7 decision)

Page numbers, "Table of Contents" links and running headers stay in the stored text: stripping
them would move every offset (citations, thesis pins' `?at=`, search passages, What changed
diffs, peers' mentions). One rule leaves them out where text is read, in two copies checked
against the same cases (`services/web/src/reader/furniture.cases.json`): web
`reader/furniture.ts` (the reader, the Business step, pinned quotes) and SQL
`gold.page_furniture_lines` (migration 068: search passages and snippets). What Changed
(`diff.py` rule 1, the Query API's `changes.py`) and earnings releases (`releases.py` rule 1)
skip furniture their own way. The rule, the decision and its measurements:
[DESIGN.md](DESIGN.md#page-furniture-left-out-where-the-text-is-read-not-stripped-parser-v7-decision).

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
resumes and a re-run is a no-op. Filings never indexed or diffed come first, then the rest oldest
first (migration 071), so re-indexing history never delays a new filing. One run at a time
(advisory lock; a run that finds it taken does nothing and logs so, never "complete").

```bash
make section-index LIMIT=5000   # parser-index --limit 5000 [--batch 25]
parser-index --passages         # only the passages built before migration 068 (below)
```

### Parser-output checks (migration 072)

The parity tests prove Python and Rust agree; these prove what they agree on is right ("Item
7.01Regulation" keyed 3,465 8-K sections "7" in both). Each filing section-index indexes goes through
`ops.check_sections`: `invalid_key` (a key the form doesn't have: 8-K items 1.01-9.01, 10-K items with
4A, 10-Q items of their Part, 20-F items with 3.D, 6-K and 40-F documents (095), exhibit keys),
`missing_core_item` (an original 10-K without 1, 1A, 7 or 8, or 20-F without 3.D, 4, 5 or 18, unless
it has a cross-reference index; a 10-Q without Part I items 1 and 2), `duplicate_section`,
`empty_section` (nothing past the heading; a 10-K's reserved Item 6 isn't one) and `no_sections` (a
stored document a day old the parser couldn't split). Findings in `ops.section_findings` (keys and
counts, never text), stamps in `ops.section_checks`; counts for ops_reader in
`ops.parser_quality_summary` and on the public status page. `filing_parser/invariants.py` holds the
same rules for the tests (`test_invariants.py` checks they are the migration's), run over every real
filing in `tests/fixtures`, including Chicago Rivet's "Item 7.01Regulation" 8-K (`cvr-8-K`).
Known gap, both parsers: a 10-Q whose Part I Item 1 heading isn't an Item line (Molina, TriNet; 14,882
10-Qs on hermes) has no `I-1`; the fixtures' tests mark it a strict xfail.

### Statement lines (migration 074)

For each 10-K and 10-Q it indexes, section-index reads the filing's own statement tables with
`filing_parser/statements.py` (its docstring is the spec; `READER`): revenue, net income, total
assets and total liabilities and equity as printed in the current period's column, times the table's
scale words, every candidate kept (`silver.statement_lines`, one `silver.statement_reads` row per
filing, lines or none). `ops.check_financials` compares them with gold (`statement_mismatch`, a
warning) and the company's confidence mark counts them; a read makes the company's check due, so
the facts-fetcher catch-up compares within the hour. Measured: the reader agrees with the filing's
own XBRL on 97-100% of the lines it finds in 97 recent 10-Ks; on hermes' first 300 parsed
10-K/10-Qs, 4 findings in 367 comparisons (two gold's side, two the reader's: migration 074's
header). A rule change bumps `READER`: the catch-up re-reads every filing.

### Search without page furniture (migration 068)

`gold.refresh_section_search` blanks a section's page furniture (as spaces, so offsets stay)
before `to_tsvector`, and stores the blanked lines per passage (`gold.section_search.furniture`);
snippets and exact phrases read through `gold.blank`, so they are clean at once even for older
passages. `ops.section_index.passages_rule` = `furniture-1` marks filings rebuilt this way.
The hourly [catch-up](#catch-up) rebuilds older filings' passages without touching diffs;
`parser-index --passages` (Helm CronJob `section-passages`, emergency only) does all of them at
once. After a full rebuild, `REINDEX INDEX CONCURRENTLY gold.section_search_tsv`.
Details: [DESIGN.md](DESIGN.md#search-without-page-furniture-migration-068).

Config: `DATABASE_URL`, `SECTION_INDEX_LIMIT` (default 5000; 20,000 in the Helm values),
`SECTION_INDEX_BATCH` (25), `CATCHUP_SECONDS` (1200), `PARSER_CATCHUP_PER_RUN` (3000). Helm: CronJob
`section-index` (hourly at :40; ships suspended, the Flux release turns it on). Progress:
`ops.section_index_status`; counts per compared section: `ops.section_change_summary`.

### Catch-up

A deploy that changes the parser (`PARSER_VERSION`, both parsers), the passages (`SEARCH_RULE`) or the
diffs (`diff.ALGORITHM`) needs nothing run by hand (migration 071, `filing_parser/catchup.py`). Once a
`section-index` run has done its own work, it:

1. re-queues up to `PARSER_CATCHUP_PER_RUN` documents whose sections an older parser wrote, oldest
   filing first, through the document crawl's outbox (`ops.doc_crawl_targets.published_at`):
   `doc-crawler` publishes them to `documents.backfill` and `parser-backfill` parses them, never the
   live parser. Only while the crawl has under 1,000 targets of its own pending, its outbox under
   3,000 events and under 6,000 of the last day's requests wait for parser-backfill. A document the
   live doc-fetcher stored has no crawl target; it gets the row the crawl writes for a filing it
   finds already stored. Each document is re-queued once per version;
2. runs the parser-output checks (072) on every stored document no check of
   `ops.check_sections_version()` has seen, oldest filing first, 10,000 a transaction, for up to 5 of
   those minutes (kind `check_sections`; 634,653 documents on hermes; the backlog query took 149 s
   per batch until 079 made it an anti-join, 0.2 s);
3. reads the statement lines (074) of every stored 10-K/10-Q with tables that the current
   `statements.READER` hasn't read, oldest filing first, 100 a transaction, for up to 5 minutes
   (kind `statements`, `ops.statement_backlog`);
4. rebuilds older passages for the rest of `CATCHUP_SECONDS` (never past 50 minutes into the job, so
   the next hour's run is never skipped), oldest filing first, under the same lock as
   `section-passages` (one waits for the other; both take the same filings, so they can overlap in
   time without harm);
5. measures diffs of an older `ALGORITHM` (the run itself recomputes them).

Re-parsed documents whose sections change are re-indexed by the next runs (after new filings).
Progress: `SELECT * FROM ops.catchup_status` (`paused`, `behind`, `in_flight`, `done_last_day`,
`eta`, `note`). A new version's backlog is recorded when its first run starts; a run locked out by
a hand-run job (`section-passages` holds the lock) records every kind as `skipped: lock held` with
its last count (079). The parser kind ships paused (migration 071): it is measured every hour and re-queues
nothing until the owner runs

```sql
UPDATE ops.catchup SET paused = false WHERE kind = 'parser';   -- `true` pauses it again
```

From the next `section-index` run (hourly at :40) it re-queues 3,000 documents an hour, which
`doc-crawler` publishes within about 30 minutes and `parser-backfill` parses: on 2026-11-16, 595,775
documents predated rust-7, about 8 days. Version 8 (rules 12 and 13) restarts that count: every stored
document is re-queued once more (a 10-K, 10-Q or 8-K re-parses to the same rows, kept with their
`parsed_at`, so nothing is re-indexed), about a day more than finishing v7 would have taken; the
re-queue also waits while the crawl has over 1,000 targets pending (its first foreign plan). Passages are not paused: 433,157 filings predated
`furniture-1` (hours).
Cost and index size: [DESIGN.md](DESIGN.md#section-diffs-and-search-index-cost).

## Peers by business

`parser-peers`, a weekly batch job in the same image (`filing_parser/business.py`, its docstring is
the spec; `ALGORITHM` stored with each profile, now business-2; migrations 047, 160), compares
what companies say they do: TF-IDF term vectors of each company's latest usable 10-K Item 1 or
20-F Item 4 (standard library only, no paid embeddings, no external calls). It writes
`gold.business_profiles` (the filing and section each profile came from, its most distinctive
terms), `gold.business_peers` (each company's 60 most similar companies and the shared terms, so
the [Query API](../query-api/README.md#peers) can say why), `gold.business_mentions` (companies
its text names as competitors, with offsets to cite) and `gold.business_relations` (companies it
names anywhere else: customers, suppliers, partners, owners), replaced in one transaction per run;
one run at a time (advisory lock). A cross-reference Item 1 (Intel, GE, McDonald's) gives no
profile. Method, cost
and how the settings were chosen: [DESIGN.md](DESIGN.md#peers-by-business).

```bash
parser-peers   # DATABASE_URL; no options
```

Helm: CronJob `business-peers` (Sundays 05:30 ET, not suspended, 1 GiB limit); run it once by hand
after the deploy that adds migration 047. Until a run has stored peers, the Query API ranks them by
SIC and size.

## Parser v7 rollout (runbook)

Deploying v7 on hermes (what ships, re-queueing earnings releases, the full `parser-reparse`,
`section-passages`, verification and rollback): [DESIGN.md](DESIGN.md#parser-v7-rollout-runbook).
Since migration 071 its step 4 happens by itself and steps 2-3 once the parser kind is unpaused
([catch-up](#catch-up)).

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
(CI). `test_invariants.py` checks every fixture's keys and core items (072's rules);
`test_section_checks_db.py` runs `ops.check_sections`, section-index's call and the catch-up.
