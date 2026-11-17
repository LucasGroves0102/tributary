"""What the public API docs say about each route an API key can use: a short summary and a
description written for someone calling the API (what it returns, its parameters), never the
developer docstring. `api_keys.key_routes_openapi` puts these on the generated OpenAPI
(`/v1/openapi-keys.json`, which the web app's "Using the API" page renders), adds each route's
scope, and drops every other description the app's own OpenAPI carries (docstrings, parameter and
model notes), so nothing written for developers reaches the public page.

Every route in api_keys.KEY_ROUTES and PUBLIC_ROUTES needs an entry (test_api_keys checks both
ways). Write for an API user: no table, file, migration or ticket names (test_api_keys refuses
them in the public spec)."""

SCOPE_WORDS = {
    "read": "Scope: read (any API key).",
    "write": "Scope: write (a key with read and write).",
    "public": "Public: no key needed.",
}

ROUTE_DOCS: dict[tuple[str, str], tuple[str, str]] = {
    # --- public -----------------------------------------------------------------------------
    ("GET", "/v1/metrics"): (
        "Metric names",
        "Every metric name the financials route accepts: `metrics` (as companies report them) and "
        "`derived_metrics` (computed from them, such as margins and free cash flow)."),
    ("GET", "/v1/screener/columns"): (
        "Screener columns",
        "The screener's column keys with the metric, period and unit of each, and the filer "
        "categories and size bands its filters accept."),

    # --- read: you and companies ------------------------------------------------------------
    ("GET", "/v1/me"): (
        "Who you are",
        "Your user id and what you can read: `catalog` says whether every company in the public "
        "catalog is readable to you and with which tiers (financials, filings), `companies` lists "
        "companies you read beyond it. A request made with an API key also gets `api_key`: the "
        "key's name, first 8 characters, scopes and expiry."),
    ("GET", "/v1/companies/search"): (
        "Find companies",
        "Companies matching a ticker, a CIK or any part of a name, best match first, each with its "
        "identity and what you can read of it (`access`). `q` is required; `limit` 1-25 (default "
        "5); `access=financials` lists only companies whose financials you can read."),
    ("GET", "/v1/sectors"): (
        "Sectors",
        "Tributary's open grouping of SIC codes into sectors and industries, each industry with "
        "its SIC ranges and how many companies with financials it holds."),

    # --- read: figures ----------------------------------------------------------------------
    ("GET", "/v1/companies/{company_id}/financials"): (
        "Financial figures",
        "A company's figures, newest period first, each with its period, unit, the filing it came "
        "from (accession number, form, XBRL tag, EDGAR link) and when that was filed. `metrics` "
        "(repeat for several; default every metric), `quarters` 1-120 (default 4), `view` quarter "
        "or ttm (trailing twelve months), `basis` fiscal or calendar, `format` json or csv; "
        "`readings=true` says why a ratio isn't meaningful, `callouts=true` (quarterly) marks "
        "quarters far outside their series. Amounts are in the company's reporting currency "
        "(`currency` is set when it isn't USD). `as_of` (a past day, YYYY-MM-DD) gives the "
        "figures as they stood at its end, from the filings filed by then (echoed as `known_on`). "
        "`originals=true` adds `original` to each figure a later filing changed: the first value, "
        "its filing and day, and the kind of change (split, another_line, same_line)."),
    ("GET", "/v1/companies/{company_id}/annual-per-share"): (
        "Annual per-share figures",
        "EPS and weighted average shares for each fiscal year as the annual report states them, "
        "each cited. `years` 1-30 (default 10). For the history on one share basis, with stock "
        "splits applied and the fourth quarter and trailing twelve months worked out, use the "
        "per-share figures. `as_of` (a past day): the annual reports filed by its end."),
    ("GET", "/v1/companies/{company_id}/restatements"): (
        "Restated figures",
        "Every quarterly or trailing-twelve-month figure a later filing changed: the value first "
        "reported and the one after it, each with its filing and the day it was first known, and "
        "the kind of change: `split` (a stock split restated share counts and EPS), "
        "`another_line` (the period is read from another reported line than before) or "
        "`same_line` (the company restated the figure). `as_of` (a past day): the changes filed "
        "by its end. `history` false: none is kept for this company yet."),
    ("GET", "/v1/companies/{company_id}/per-share"): (
        "Per-share figures on one share basis",
        "EPS and weighted average shares by quarter (`view=quarter`), trailing twelve months "
        "(`ttm`) or fiscal year (`annual`), with every period restated to today's share basis "
        "after stock splits (`shares=adjusted`, the default; `as_filed` for each filing's own "
        "figure). Each figure keeps what the filing stated (`as_filed`) and the splits applied. "
        "The fourth quarter is worked out from the year and its first three quarters, and the "
        "trailing twelve months' EPS is the four quarters' EPS added; both say how in "
        "`derivation`. `basis` fiscal or calendar, `quarters` 1-120 (default 40). `as_of` (a past "
        "day): on that day's share basis, only the splits restated by then applied."),
    ("GET", "/v1/companies/{company_id}/segments"): (
        "Segment figures",
        "Revenue, operating income and other figures per business segment, geography or product "
        "line, as the filings break them down, each an exact decimal with its filing. `axis` "
        "(business_segment, geography, product_service, reconciliation), `metric`, `quarters` "
        "1-40 (default 8), `view`, `basis`, `format`."),
    ("GET", "/v1/companies/{company_id}/components"): (
        "What a figure is made of",
        "The inputs behind one figure: a computed figure's inputs, a trailing twelve months' "
        "quarters or a total's parts, with the XBRL tags, values and filings behind each. "
        "`metric`, `period_end` (YYYY-MM-DD) and `view` (quarter or ttm) name the figure."),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/figures"): (
        "One filing's figures",
        "What one 10-K or 10-Q itself reports for the company, plus net debt, cash and "
        "investments and free cash flow computed from that filing alone, each cited."),
    ("GET", "/v1/filings/{accession_no}/recap"): (
        "Filing recap",
        "A 10-Q's or 10-K's figures against the prior quarter and the same quarter a year earlier, "
        "each cited, and the one-time items its MD&A mentions where you can read filings. "
        "`metrics` chooses which figures."),
    ("GET", "/v1/companies/{company_id}/data-notes"): (
        "Data notes",
        "Notes on one company's figures (gaps, overlapping quarters, restatements, quarters "
        "computed from year-to-date figures, filings the SEC's XBRL data doesn't reflect yet), "
        "each with its filings, most important first; `coverage`: what is missing from its "
        "figures or documents and why (fixable, not in the SEC's data, or out of scope)."),
    ("GET", "/v1/data-notes"): (
        "Recent data notes",
        "The newest data notes across the companies whose financials you can read, or the ones "
        "named with `company_id` (repeat, at most 50). `limit` (default 20)."),
    ("GET", "/v1/companies/{company_id}/text-kpis"): (
        "Backlog and orders",
        "Backlog, orders and order growth as the company's filings and earnings releases state "
        "them in words, each value with the passages that state it. `years` 1-15 (default 10)."),
    ("POST", "/v1/companies/{company_id}/model"): (
        "Run a model",
        "Runs a forecast from revenue to EPS and free cash flow on the company's figures with the "
        "assumptions in the body (years of growth, margins and other rates, optional bear and bull "
        "cases, an optional share price) and returns the history, the forecast and, with a price, "
        "the valuation. Saves nothing."),
    ("POST", "/v1/formulas/validate"): (
        "Check a formula",
        "Parses and checks a formula in the chart formula language (for example "
        "`net_income / revenue`, `revenue / revenue[-4q] - 1` or `KO.revenue / PEP.revenue`): "
        "names, units, limits, and that every ticker is a company you can read. Body: `formula`, "
        "`default_company` (for metrics without a ticker), `basis`, `view`, `from`, `to`. Reads "
        "no figures."),
    ("POST", "/v1/formulas/evaluate"): (
        "Evaluate a formula",
        "A formula's series over time, each point citing every input's filing. The same body as "
        "checking a formula; every company it names must be readable to you, and figures in two "
        "currencies can't be combined. `as_of` in the body (a past day): the inputs as they stood "
        "at its end. `mine.<dataset>.<field>` reads your own uploaded data; its points carry "
        "`value_source` \"yours\" and the day each value was known instead of a filing."),
    ("POST", "/v1/sheets/values"): (
        "Many figures at once",
        "Up to 200 figures in one request, for spreadsheets; counts as one request. Body: "
        "`requests`, each `company` (ticker or CIK), `metric`, `period` (latest, TTM, FY, FY2024, "
        "FY2024 Q3, CY2024 Q3, a quarter's end day, TTM before a quarter), optional `as_of`, "
        "`currency` (default USD), `shares` (adjusted or as_filed, for EPS) and `count` (a series "
        "of up to 40 periods back from latest, TTM or FY). At most 25 companies per request. Each "
        "answer, in order, has `status`; `value` only when it is a number to show (current, "
        "behind or ok), else `reason` says why: stale, old, not_meaningful, not_applicable, "
        "not_computed, other_currency (never converted), no_figure, not_permitted or invalid. "
        "Every figure has its period, unit and `citation` (filing, form, filed, EDGAR link)."),

    # --- read: filings and text -------------------------------------------------------------
    ("GET", "/v1/companies/{company_id}/filings"): (
        "Parsed filings",
        "A company's recent 10-K, 10-Q and 8-K filings that can be read here, newest first, each "
        "with its sections (titles and sizes, no text). `limit` 1-20 (default 8), or "
        "`accession_no` for one filing; `as_of` (a past day): filed by its end."),
    ("GET", "/v1/filings/latest"): (
        "Latest filings",
        "The newest parsed filings across the companies whose filings you can read. `limit` 1-50 "
        "(default 20)."),
    ("GET", "/v1/companies/{company_id}/sections/{section_key}"): (
        "A section's text",
        "One section's text (10-K \"1A\", 10-Q \"II-1A\", 8-K \"2.02\", a press release "
        "\"EX-99.1\") from the company's newest filings that have it, with its citation and "
        "character offsets. `form` (10-K, 10-Q or 8-K), `accession_no` for one filing, `limit` "
        "1-5 (default 1), `max_chars` 100-1,000,000 (default 8,000; `truncated` says when the "
        "section is longer); `as_of` (a past day): the newest filings filed by its end."),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/changes"): (
        "What changed",
        "Risk Factors and MD&A paragraphs that are new, removed or rewritten in this filing "
        "against the comparable earlier one, with word-level differences and citations in both "
        "filings. `section`, `basis` (prior or year_ago), `kind` (new, removed, rewritten); "
        "`view` reading or ranked hides boilerplate and ranks changes by materiality, and then "
        "`q` (a topic) and `numbers=new` (paragraphs with new figures) filter them."),
    ("GET", "/v1/search"): (
        "Search filings",
        "Full-text search over the sections of filings you can read: the best passage per "
        "section with a snippet (matches between « and ») and its citation. `q` is required "
        "(quoted phrases match exactly); `company`, `form`, `section`, `from`, `to`, `sort` "
        "relevance or date, `limit`, `offset`; `total` counts the matching sections. "
        "`window=exact` searches exactly from `from` to `to`; `as_of` (a past day): filings filed "
        "by its end."),
    ("GET", "/v1/companies/{company_id}/events"): (
        "Events since the last report",
        "8-K filings since the latest 10-Q or 10-K period end, newest first: each one's items, an "
        "excerpt of its own words, its press release, and which figures at that period end it may "
        "have changed."),
    ("GET", "/v1/companies/{company_id}/earnings"): (
        "Earnings releases",
        "Each quarter's earnings release (8-K Item 2.02), newest first: the release, its headline, "
        "the outlook it states and management's adjustments, each passage with its offsets in the "
        "release. `quarters` 1-8 (default 4); `tables=true` adds the tables those passages quote, "
        "as grids. No beat or miss: there are no consensus estimates."),
    ("GET", "/v1/companies/{company_id}/insiders"): (
        "Insider filings",
        "Forms 3, 4 and 5, newest first: who filed and their role, each transaction's code in "
        "words, shares, price, value and shares owned after, and a summary per filing. `limit` "
        "(default 50), `cursor` (the previous page's `next_cursor`), `before` (a date)."),

    # --- read: the SEC index, dates, peers, screens -----------------------------------------
    ("GET", "/v1/companies/{company_id}/filing-index"): (
        "A company's filing index",
        "Every filing of the company in the SEC's index, newest first: form, dates, period and "
        "EDGAR links, and how many of its sections can be read here where you can read the "
        "company's filings. `form`, `from` and `to` (dates), `limit` (default 25), `offset`."),
    ("GET", "/v1/filing-index"): (
        "Filing index",
        "The SEC's filing index across companies, newest first: the companies named with "
        "`company_id` (repeat, at most 50), or by default the companies you can read. `form`, "
        "`from`, `to`, `limit`, `offset`."),
    ("GET", "/v1/companies/{company_id}/expected-filings"): (
        "Expected filings",
        "When the company's next 10-Q and 10-K are expected, estimated from its own filing history "
        "and capped at the SEC deadline, with the basis of each estimate. `periods` (default 2)."),
    ("GET", "/v1/calendar/expected-filings"): (
        "Filing calendar",
        "Expected 10-Q and 10-K filings in the next `days` days (1-180, default 30), plus those "
        "already due, for your watchlist (`scope=watchlist`, the default) or every company you can "
        "read (`scope=readable`). `format` json or csv."),
    ("GET", "/v1/companies/{company_id}/peers"): (
        "Peers",
        "Suggested peers, each with why: how alike the two companies' business descriptions are, "
        "competitors either one names, industry and size, with figures for the companies you can "
        "read. `limit` (default 10), `columns` (screener column keys), `access=financials` for "
        "only companies you can read."),
    ("GET", "/v1/companies/{company_id}/peers/check/{candidate_id}"): (
        "Can a company be a peer",
        "Whether `candidate_id` can join this company's comparison and, if not, why: a foreign "
        "filer without figures, no XBRL figures yet, or no annual report."),
    ("GET", "/v1/screener"): (
        "Screener",
        "Companies with their latest figures in the chosen `columns`, filtered and sorted. "
        "Filters: `where` (`<column>:<gt|gte|lt|lte>:<number>`, ratios as fractions, repeatable), "
        "`sic`, `sic_prefix`, `industry`, `sector`, `sector_industry`, `exchange`, "
        "`fiscal_year_end_month`, `filer_category`, `revenue_band`, `assets_band`, `q` (a name), "
        "`company_id`, `issue` (a data check's name); `sort` (a column, `-` first for descending), "
        "`limit`, `offset`, `format` json or csv. Figures only for companies whose financials you "
        "can read, each with how to read it. `as_of` (a past day): each company's figures as they "
        "stood at its end, read against that day. `metric` (one of your saved metrics' item id, "
        "up to 5): a column `m_<id without dashes>` evaluated over each company's latest figures "
        "and your own data, sortable and filterable like the rest."),

    # --- read: your saved work --------------------------------------------------------------
    ("GET", "/v1/watchlist"): (
        "Your watchlist",
        "The companies on your watchlist with their latest report, next expected filing and "
        "figures (only for companies whose financials you can read). `columns`, `sort`, `format` "
        "json or csv."),
    ("GET", "/v1/workspaces"): (
        "Your workspaces",
        "Your workspaces, oldest first, each with its items (names and kinds, no contents)."),
    ("GET", "/v1/workspaces/{workspace_id}"): (
        "A workspace",
        "One of your workspaces with its items in order and their saved contents (a thesis's or "
        "report's text comes from the item route)."),
    ("GET", "/v1/workspace-items/{item_id}"): (
        "A saved item",
        "One of your saved charts, comps sets, screens, theses, models or reports with its "
        "contents."),
    ("GET", "/v1/workspace-items/{item_id}/versions"): (
        "An item's earlier versions",
        "The earlier versions kept of a thesis or model, newest first, without their contents."),
    ("GET", "/v1/workspace-items/{item_id}/versions/{version_id}"): (
        "One earlier version",
        "One earlier version of a thesis or model, with its contents."),

    # --- read: your own data ----------------------------------------------------------------
    ("GET", "/v1/datasets"): (
        "Your datasets",
        "The datasets you uploaded, by name, each with its fields and their units, its currency, "
        "row, value and company counts, first and last day, and how its rows are dated "
        "(`known_from`: a publication date column, or the upload day); and the upload limits. "
        "Only you can read them."),
    ("GET", "/v1/datasets/{dataset_id}"): (
        "A dataset",
        "One of your datasets: its fields, units, counts and days."),
    ("GET", "/v1/datasets/{dataset_id}/rows"): (
        "A dataset's rows",
        "A page of one of your datasets' rows by company, newest day first, values as exact "
        "decimal strings by field. `company_id`, `from`/`to` (days), `limit` 1-1000 (default "
        "100), `offset`; `as_of` (a past day): each row as you knew it at its end; "
        "`versions=true`: every version of each row instead."),
    ("GET", "/v1/companies/{company_id}/datasets"): (
        "Your data on a company",
        "Each of your datasets with rows for this company, with its latest days. `as_of` (a past "
        "day): as you knew them at its end."),

    # --- write ------------------------------------------------------------------------------
    ("PUT", "/v1/watchlist/{company_id}"): (
        "Watch a company",
        "Adds a company to your watchlist (one already there stays). Watching grants no access."),
    ("DELETE", "/v1/watchlist/{company_id}"): (
        "Stop watching a company",
        "Removes a company from your watchlist."),
    ("POST", "/v1/workspaces"): (
        "Create a workspace",
        "A new workspace named `name`; with `project` (a company, its peers, notes and the steps "
        "done), an analysis project about that company."),
    ("PATCH", "/v1/workspaces/{workspace_id}"): (
        "Rename or update a workspace",
        "Renames one of your workspaces (`name`) and/or saves an analysis project's state over "
        "(`project`)."),
    ("POST", "/v1/workspaces/{workspace_id}/duplicate"): (
        "Copy a workspace",
        "A copy of one of your workspaces and every item in it, named `name` or "
        "\"<name> (copy)\"."),
    ("POST", "/v1/workspaces/{workspace_id}/items"): (
        "Save an item",
        "Saves a chart, comps set, screen, thesis, model or metric (`kind`, `name`, `state`) into "
        "one of your workspaces."),
    ("PUT", "/v1/workspace-items/{item_id}"): (
        "Save over an item",
        "Replaces one of your items' contents (`state`, checked against its kind) and/or renames "
        "it (`name`). The version a thesis or model replaces is kept."),
    ("POST", "/v1/workspace-items/{item_id}/duplicate"): (
        "Copy an item",
        "A copy of one of your items in the same workspace, or in another of yours "
        "(`workspace_id`), optionally named `name`."),
    ("POST", "/v1/datasets"): (
        "Upload a dataset",
        "A CSV of your own figures by company and date, as multipart/form-data: `file` and "
        "`name` (formulas read it as mine.<name>.<field>), optionally `title`, `company_column` "
        "(tickers or CIKs), `date_column` (YYYY-MM-DD), `published_column` (the day each row was "
        "published; without it, rows are known from today), `units` (`field:unit,...`: number, "
        "ratio, money, shares, per_share), `currency`, `decimal` and `dry_run=true` (a preview, "
        "nothing stored). Values must be numbers; a row whose company isn't a ticker or CIK "
        "Tributary knows is left out and listed by row number. At most 20 MB, 200,000 rows and "
        "50 columns a file; 50 datasets and 200 MB in all; 10 uploads an hour."),
    ("POST", "/v1/datasets/{dataset_id}/upload"): (
        "Upload into a dataset",
        "A later file into one of your datasets, the same form as uploading one: `mode=replace` "
        "(default: its rows become the file's) or `mode=append` (the same fields: rows added, an "
        "unchanged one isn't stored again, a changed one becomes a later version)."),
    ("PATCH", "/v1/datasets/{dataset_id}"): (
        "Change a dataset",
        "Its `title`, a field's unit (`units`: field to unit) or its money's `currency`."),
}
