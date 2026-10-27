# Golden 10-K figures

Checks the financial pipeline (XBRL companyfacts → bronze → silver → gold) against the numbers
five companies printed in their 10-Ks, read independently of the XBRL the pipeline loads.

| Company | Fiscal year (end) | 10-K |
|---|---|---|
| NVIDIA (NVDA) | FY2026 (2026-01-25) | [0001045810-26-000021](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) |
| Costco (COST) | FY2026 (2026-08-30) | [0000909832-26-000093](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| JPMorgan Chase (JPM) | 2025 (2025-12-31) | [0001628280-26-008131](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm) |
| Exxon Mobil (XOM) | 2025 (2025-12-31) | [0000034088-26-000045](https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm) |
| Home Depot (HD) | FY2025 (2026-02-01) | [0001628280-26-019436](https://www.sec.gov/Archives/edgar/data/354950/000162828026019436/hd-20260201.htm) |

These are the companies of `services/facts-fetcher/tests/test_pipeline.py`, each at its most
recent fiscal year in those fixtures.

## What is checked

[`10k_figures.csv`](10k_figures.csv) has 149 printed figures: 134 of the current fiscal year
(every one of the 32 standardized metrics that the company prints on its income statement,
balance sheet or cash flow statement, plus a few printed in captions or notes) and, for growth
and ROE, the prior-year revenue, net income and equity printed next to them.
[`services/facts-fetcher/tests/test_golden_10k.py`](../../services/facts-fetcher/tests/test_golden_10k.py)
loads the five companies' real companyfacts payloads
(`services/facts-fetcher/tests/fixtures/golden/`) through the real migrations and asserts, by
the CSV's `check` column:

| check | Rows | Assertion |
|---|---|---|
| `annual` | 129 | gold's fiscal-year value equals the printed figure within half its last printed digit (`printed_unit`; ±0.5 million when printed in millions). Flows: the TTM ending at year-end; balance sheet: the year-end value. |
| (same rows, flows) | 75 | gold's four fiscal quarters (Q4 usually derived as year minus nine months) sum to the printed year. |
| `annual_silver` | 18 | EPS and weighted shares: never summed or derived, so gold keeps quarters only; silver must hold the 10-K's fiscal-year fact at the printed value. |
| `absent` | 2 | printed, but gold has no value; asserted so the gap stays visible (see below). |

Every TTM derived metric (gross/operating/net/FCF margin, free cash flow, revenue and net income
growth, payout ratio, debt to equity, net debt, current ratio, ROE: 12 x 5 companies) must equal
its formula from migration 014 recomputed from the CSV's `annual` figures, within what half a
printed digit on each input can move it, or be absent when an input is. `eps_growth` is
quarterly only.

The test runs in CI's `python` job (it sets `TEST_DATABASE_URL`) and is skipped without it.

## Not covered, and why

- Metrics a company doesn't print: gross profit and R&D at Costco, Exxon and JPMorgan; cost of
  revenue at JPMorgan and Exxon (crude purchases are not a cost of revenue); the classified
  balance-sheet lines (current assets/liabilities, receivables, inventory, short-term
  investments) and capex at JPMorgan; inventory at Exxon (two lines, no total); R&D at Home
  Depot.
- Figures not on the face of the statements: JPMorgan's stock-based compensation, Exxon's
  stock-based compensation and weighted shares.
- `absent`: Nvidia's marketable securities (51,951) are tagged with Nvidia's own extension
  (`nvda:MarketableSecuritiesAndEquitySecuritiesFVNI`) in the FY2026 10-K; gold maps us-gaap
  tags only, so short-term investments are missing and net debt counts them as 0. JPMorgan's
  long-term debt: an unclassified bank balance sheet has no noncurrent-debt tag, so gold has
  none by design (migration 012) and debt ratios aren't computed.
- Definitional matches, noted per row: Exxon's revenue is total revenues and other income;
  JPMorgan's cash is cash and due from banks plus deposits with banks (the cash-flow
  statement's total); Home Depot's D&A is the cash-flow line, not the smaller income-statement
  line; Nvidia's current debt is printed as short-term debt; Exxon's current debt is printed in
  the debt note ($6.2 billion).

## What it found

Two pipeline gaps, fixed in `db/migrations/024_gold_ignores_proxy_statements.sql`:

1. **Proxy statements replaced 10-K figures.** A DEF 14A's pay-versus-performance table repeats
   five years of net income, rounded (JPMorgan 2025: 57.0 billion; the 10-K prints 57,048
   million). Filed after the 10-K, it won gold's "latest filing" rule, so the derived Q4 and the
   TTM were off by the rounding (JPMorgan 57,000 instead of 57,048; Home Depot 14,160 instead of
   14,156). On hermes 6,387 gold rows of 1,669 companies cited a DEF 14A. Gold now ignores
   proxy and information statements.
2. **Costco had no quarterly pre-tax income.** Its 10-Qs tag it only as
   `IncomeLossAttributableToParent`; added as the third pretax_income tag.

Production needs `facts-fetcher --rebuild` (`make financials-rebuild`) once 024 is deployed.

## How the figures were extracted

1. The 10-K documents are stored in `bronze.filing_documents` (`content_gzip` is gzip of the
   HTML). Pull each one read-only and decompress it into a scratch directory outside the repo:

   ```bash
   echo "COPY (SELECT translate(encode(content_gzip, 'base64'), E'\n', '')
         FROM bronze.filing_documents WHERE accession_no = '<accession>' AND form = '10-K'
         ORDER BY size_bytes DESC LIMIT 1) TO STDOUT;" \
     | ssh <server> 'psql -d tributary -At' | base64 -d | gunzip > "$SCRATCH/<accession>.htm"
   ```

2. [`extract.py`](extract.py) reads the visible text only (what a reader sees; never inline
   XBRL tag names or attributes), finds the three statement tables, reads each table's scale
   ("in millions", "millions of dollars"), and maps rows to metrics by printed label
   (`MAPPING`, with the sign flip for printed outflows and the custom readers for captions and
   notes). `python -I tests/golden/extract.py dump $SCRATCH/<accession>.htm` prints every row
   for review; `python -I tests/golden/extract.py csv $SCRATCH` writes candidate rows.
3. The candidates were reviewed row by row against the statements and committed as
   `10k_figures.csv`. Tests never re-run extraction; the CSV is the reference. Don't regenerate
   it to make a failing test pass: a failure means gold disagrees with the filing.
4. The fixtures are the same companies' companyfacts payloads as stored in
   `bronze.company_facts.raw_gzip`, trimmed by [`trim_facts.py`](trim_facts.py) to the mapped
   us-gaap tags and to periods ending in the two fiscal years up to year-end. Facts filed after
   the 10-K (later 10-Qs, proxy statements) are kept: they are what the pipeline really sees.

## Hand check

These 21 figures were hand-checked against the filings by the owner (Lucas Groves) on
2026-10-27, and all matched; their CSV rows have `verified_by` = "Lucas Groves, 2026-10-27".
Values are as printed (millions of dollars unless noted).

| # | Company | Statement | Line | Printed | Filing |
|---|---|---|---|---|---|
| 1 | NVIDIA | Income statement | Revenue | 215,938 | [nvda-20260125.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) |
| 2 | NVIDIA | Income statement | Net income per share, Diluted | $4.90 | [nvda-20260125.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) |
| 3 | NVIDIA | Balance sheet | Marketable securities | 51,951 | [nvda-20260125.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) |
| 4 | NVIDIA | Cash flows | Purchases related to property and equipment and intangible assets | (6,042) | [nvda-20260125.htm](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm) |
| 5 | Costco | Income statement | Total revenue | 303,154 | [cost-20260830.htm](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| 6 | Costco | Income statement | Income before income taxes | 12,251 | [cost-20260830.htm](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| 7 | Costco | Income statement | Shares used in calculation (000's), Diluted | 444,427 | [cost-20260830.htm](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| 8 | Costco | Balance sheet | Current portion of long-term debt | 2,248 | [cost-20260830.htm](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| 9 | Costco | Cash flows | Cash dividend payments | (2,458) | [cost-20260830.htm](https://www.sec.gov/Archives/edgar/data/909832/000090983226000093/cost-20260830.htm) |
| 10 | JPMorgan | Income statement | Total net revenue | 182,447 | [jpm-20251231.htm](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm) |
| 11 | JPMorgan | Income statement | Net income | 57,048 | [jpm-20251231.htm](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm) |
| 12 | JPMorgan | Balance sheet | Total stockholders' equity | 362,438 | [jpm-20251231.htm](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm) |
| 13 | JPMorgan | Cash flows | Cash and due from banks and deposits with banks at the end of the period | 343,338 | [jpm-20251231.htm](https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm) |
| 14 | Exxon Mobil | Income statement | Total revenues and other income | 332,238 | [xom-20251231.htm](https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm) |
| 15 | Exxon Mobil | Income statement | Net income (loss) attributable to ExxonMobil | 28,844 | [xom-20251231.htm](https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm) |
| 16 | Exxon Mobil | Debt note (text) | Long-term debt maturing within one year | $6.2 billion | [xom-20251231.htm](https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm) |
| 17 | Exxon Mobil | Cash flows | Additions to property, plant, and equipment | (28,358) | [xom-20251231.htm](https://www.sec.gov/Archives/edgar/data/34088/000003408826000045/xom-20251231.htm) |
| 18 | Home Depot | Income statement | Net earnings | 14,156 | [hd-20260201.htm](https://www.sec.gov/Archives/edgar/data/354950/000162828026019436/hd-20260201.htm) |
| 19 | Home Depot | Balance sheet | Long-term debt, excluding current installments | 46,341 | [hd-20260201.htm](https://www.sec.gov/Archives/edgar/data/354950/000162828026019436/hd-20260201.htm) |
| 20 | Home Depot | Balance sheet | Common stock caption: outstanding shares at February 1, 2026 | 996 (millions) | [hd-20260201.htm](https://www.sec.gov/Archives/edgar/data/354950/000162828026019436/hd-20260201.htm) |
| 21 | Home Depot | Cash flows | Depreciation and amortization, excluding amortization of intangible assets | 3,514 | [hd-20260201.htm](https://www.sec.gov/Archives/edgar/data/354950/000162828026019436/hd-20260201.htm) |

The other 128 rows have an empty `verified_by`: machine-extracted and reviewed, not hand-checked.
