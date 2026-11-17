"""Screener queries: company metadata (public) joined to the latest metric values (RLS).

Like formula/compile.py, the SQL text holds no user input: column keys and sort keys are
checked against fixed catalogs and become fixed fragments (v0, v1, ...); every user value is a
bind parameter.

Columns are `<metric>_ttm` (trailing twelve months; balance-sheet metrics at the quarter end)
or `<metric>_q` (latest fiscal quarter), for every metric in the formula CATALOG with values in
that view. Each value is the latest one of that metric and span (gold.latest_reading's row,
migrations 032 and 035) and carries its own period_end, filing and how to read it: not applicable,
not meaningful, old, or just behind the company's latest period. The rules are in 035, once; this
module only asks. The figures come from what the gold build stores for the screener (migration
078: gold.screen_company and gold.screen_reading, read under RLS with today's date and the
caller's SIC, the same rules); a company with nothing stored is read live, as before. As of a
past day (`as_of`, migrations 085-087) every company is read live from gold's history
(gold.latest_period and gold.latest_reading with the day: the versions believed at its end), in
a session whose "today" is that day (stale is against it); hide_blank probes the history the
same way. The confidence mark stays today's. Sorts and
range filters take the comparable value, `k<i>`, which is NULL for all of them but "behind": the
exact figure stays in `v<i>`, so a
209,828% gross margin is still returned, with its reason, and doesn't win a sort or pass a filter.
An amount in a currency other than the dollar reads `other_currency` (migration 056) and says its
unit: shown in that currency, never sorted, filtered or averaged with dollar figures. Each page row
also carries the company's confidence mark (migration 074, gold.company_confidence: high, medium or
low and its reasons in words), read after the LIMIT and, like the values, only for a reader.

The query has two stages, so its cost follows what the screen asks for and not how many
companies the caller can read: the values filters and the sort need are read for every company
in the universe, the other columns only for the page.

Permissions: metadata rows are public; values are read only for `granted` (companies the
caller can read financials for, from OpenFGA) and, under RLS, only rows
authz.readable_companies allows. granted None: OpenFGA says the whole public catalog is
readable at this tier, so no id list goes into the query and RLS alone bounds the values (the
list would come from authz.public_access, which RLS reads anyway). A value filter can't be
evaluated without a value, so ungranted companies drop out of any screen with a value filter; a
sort on a value column puts them last.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, get_args

from query_api.formula import CATALOG, DERIVED, POINT_IN_TIME

SPAN_SUFFIX = {"quarter": "q", "ttm": "ttm"}
# column key → (metric, span, unit)
COLUMNS: dict[str, tuple[str, str, str]] = {
    f"{metric}_{SPAN_SUFFIX[span]}": (metric, span, unit)
    for metric, (unit, spans) in CATALOG.items() for span in spans
}
# sortable metadata → SQL over the base CTE (fixed fragments). "sic" is the Industry column, which
# shows the SIC's name: it sorts by that name, not by the code.
META_SORT = {"name": "b.name", "company_id": "b.company_id", "sic": "lower(b.industry)",
             # the month the fiscal year ends in (migration 042), so J&J's "0103" sorts as December
             "fiscal_year_end": "silver.fiscal_year_end_month(b.fiscal_year_end)",
             "filer_category": "b.filer_category",
             "state_of_incorporation": "b.state_of_incorporation",
             # sector, then industry within it (migration 046): the screener's grouping
             "sector": "lower(b.sector || ' / ' || b.sector_industry)"}
OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
FilerCategory = Literal["large_accelerated", "accelerated", "non_accelerated"]
FILER_CATEGORIES: tuple[str, ...] = get_args(FilerCategory)
# size bands in USD: label → (min inclusive, max exclusive)
BANDS: dict[str, tuple[Decimal | None, Decimal | None]] = {
    "lt100m": (None, Decimal("1e8")),
    "100m-1b": (Decimal("1e8"), Decimal("1e9")),
    "1b-10b": (Decimal("1e9"), Decimal("1e10")),
    "10b-100b": (Decimal("1e10"), Decimal("1e11")),
    "gte100b": (Decimal("1e11"), None),
}
BAND_LABELS = ["<$100M", "$100M-$1B", "$1B-$10B", "$10B-$100B", ">=$100B"]  # gold.revenue_band
DEFAULT_COLUMNS = ["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm", "operating_margin_ttm",
                   "net_margin_ttm", "free_cash_flow_ttm", "debt_to_equity_q", "total_assets_q"]
MAX_COLUMNS = 30
MAX_WHERE = 10


class ScreenError(ValueError):
    """A malformed screen (unknown column, bad filter). Message is safe to return."""


@dataclass
class Filter:
    column: str
    op: str      # key of OPS
    value: Decimal


@dataclass
class Screen:
    columns: list[str] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    company_ids: list[str] | None = None   # restrict the universe (watchlist, peers)
    coverage: bool = True                  # only companies with gold financials
    # only companies with a value in at least one of the columns: a row of blanks isn't a result,
    # and the count must not claim it (ungranted companies stay: nothing here may depend on values
    # they can't read). Off when someone looks for a company by name or a data check.
    hide_blank: bool = False
    granted_only: bool = False             # only companies the caller can read financials for
    sic: list[str] = field(default_factory=list)
    sic_prefix: str | None = None
    industry: str | None = None
    q: str | None = None
    exchanges: list[str] = field(default_factory=list)
    fye_months: list[int] = field(default_factory=list)
    filer_categories: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)      # 046's sectors, exact names
    sector_industries: list[str] = field(default_factory=list)  # and industries
    issue: str | None = None               # a data check's name: companies with a finding of it
    filters: list[Filter] = field(default_factory=list)
    sort: str = "-revenue_ttm"
    limit: int = 50
    offset: int = 0
    as_of: date | None = None              # gold as it stood at the end of that day (087)


@dataclass(frozen=True)
class Compiled:
    sql: str
    params: dict
    columns: list[str]      # selected column keys, v0..vn-1 in the result rows
    slots: dict[str, int]   # column key → slot (selected first, then filter/sort-only)


def parse_filter(text: str) -> Filter:
    """`<column>:<op>:<number>`, e.g. operating_margin_ttm:gte:0.2 (ratios are fractions)."""
    parts = text.split(":")
    if len(parts) != 3:
        raise ScreenError(f"filter {text!r}: expected <column>:<op>:<number>")
    column, op, raw = parts
    if column not in COLUMNS:
        raise ScreenError(f"filter {text!r}: unknown column {column!r}")
    if op not in OPS:
        raise ScreenError(f"filter {text!r}: op must be one of {sorted(OPS)}")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ScreenError(f"filter {text!r}: {raw!r} is not a number") from None
    if not value.is_finite():
        raise ScreenError(f"filter {text!r}: {raw!r} is not a finite number")
    return Filter(column, op, value)


def band_filters(column: str, band: str) -> list[Filter]:
    if band not in BANDS:
        raise ScreenError(f"band {band!r}: one of {list(BANDS)}")
    low, high = BANDS[band]
    return ([Filter(column, "gte", low)] if low is not None else []) + \
           ([Filter(column, "lt", high)] if high is not None else [])


def band_label(band: int | None) -> str | None:
    return BAND_LABELS[band] if band is not None else None


def band_of(value: Decimal | None) -> int | None:
    """Python twin of gold.revenue_band."""
    if value is None or value <= 0:
        return None
    return next(i for i, (_, high) in enumerate(BANDS.values()) if high is None or value < high)


def _like(text: str) -> str:
    """ILIKE pattern matching text anywhere, with its wildcards escaped."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _gate(company: str, granted: list[str] | None) -> str:
    """Closed catalog: a one-time filter, so an ungranted company reads nothing."""
    return "" if granted is None else f" WHERE {company} = ANY(%(granted)s)"


# the company's stored inputs (gold.screen_company, 078) as the ranked stage passes them on
INPUTS = ("latest", "equity", "currency", "recent", "cached")


def _latest(company: str, granted: list[str] | None, as_of: bool = False) -> str:
    """Lateral `a`: the company's latest period (what "old" is against) and the stored inputs of
    its readings, or its latest period read live when nothing is stored (gold.screen_company).
    As of a day: its latest period then, from the history (nothing stored applies)."""
    if as_of:
        return ("LEFT JOIN LATERAL (SELECT p.latest, NULL::numeric AS equity,"
                " NULL::text AS currency, NULL::boolean AS recent, false AS cached"
                f" FROM gold.latest_period({company}, %(as_of)s) p{_gate(company, granted)}) a"
                " ON true")
    return (f"LEFT JOIN LATERAL (SELECT {', '.join(f'p.{c}' for c in INPUTS)}"
            f" FROM gold.screen_company({company}) p{_gate(company, granted)}) a ON true")


def _values(slots: dict[str, int], company: str, sic: str, inputs: tuple[str, ...],
            granted: list[str] | None, as_of: bool = False) -> str:
    """A lateral row with v<i> (the value), src<i> (its filing), st<i> and why<i> (how to read it)
    and k<i> (what it sorts and filters by) of these slots, for the company in the column
    `company`: gold.screen_reading, which is gold.latest_reading's row, read from the stored
    figures when the company's `inputs` (INPUTS' expressions) say they are stored."""
    if not slots:
        return "(SELECT 1)"
    latest, equity, currency, recent, cached = inputs
    outputs, joins = [], []
    for i in slots.values():
        outputs += [f"x{i}.value AS v{i}",
                    f"CASE WHEN x{i}.period_end IS NOT NULL THEN jsonb_build_object("
                    f"'period_end', x{i}.period_end, 'accession_no', x{i}.accession_no,"
                    f" 'form', x{i}.form, 'filed', x{i}.filed) END AS src{i}",
                    f"x{i}.status AS st{i}", f"x{i}.reason AS why{i}", f"x{i}.unit AS u{i}",
                    f"CASE WHEN x{i}.status IS NULL OR x{i}.status = 'behind'"
                    f" THEN x{i}.value END AS k{i}"]
        joins.append(
            # 087: as of a day, gold.latest_reading over the history (the same rules)
            f"LEFT JOIN LATERAL gold.latest_reading({company}, %(s{i})s, %(m{i})s, {sic},"
            f" {latest}, %(as_of)s) x{i} ON true" if as_of else
            f"LEFT JOIN LATERAL gold.screen_reading({company}, %(s{i})s, %(m{i})s,"
            f" {sic}, {latest}, {cached}, {equity}, {currency}, {recent}) x{i} ON true")
    return (f"(SELECT {', '.join(outputs)} FROM (SELECT 1{_gate(company, granted)}) one "
            f"{' '.join(joins)})")


def _has_value_at(columns: list[str], granted: list[str] | None, params: dict) -> str:
    """_has_value as of a day: one probe of the history (086) per span, the versions believed
    then (a balance's TTM column is its quarter row)."""
    tests = []
    for span in ("quarter", "ttm"):
        names = sorted({COLUMNS[k][0] for k in columns if COLUMNS[k][1] == span})
        if span == "quarter":
            balances = (COLUMNS[k][0] for k in columns
                        if COLUMNS[k][1] == "ttm" and COLUMNS[k][0] in POINT_IN_TIME)
            names = sorted({*names, *balances})
        if names:
            params[f"has_{span}"] = names
            tests.append("(SELECT true FROM gold.financials_known k"
                         f" WHERE k.company_id = c.company_id AND k.span = '{span}'"
                         f" AND k.metric = ANY(%(has_{span})s::text[])"
                         " AND k.known_from <= %(as_of)s"
                         " AND (k.known_to IS NULL OR k.known_to > %(as_of)s) LIMIT 1) IS NOT NULL")
    test = " OR ".join(tests) or "FALSE"
    return f"({test})" if granted is None else f"(c.company_id <> ALL(%(granted)s) OR {test})"


def _has_value(columns: list[str], granted: list[str] | None, params: dict) -> str:
    """Whether a company has a value in any of these columns: the metric lists stored with its
    figures (gold.screen_companies, 078: one primary-key probe), else one lookup per place a value
    can live, each a set of primary-key probes that stops at the first hit. Scalar subqueries with
    a LIMIT, not EXISTS: under an OR the planner turns EXISTS into a hashed subplan, a seq scan of
    all 5M rows of gold.derived_financials per query (1.7 s on a copy of this size, as query_api).
    Under RLS as usual. With the catalog closed, a company the caller can't read stays listed
    whatever it has."""
    tests, stored = [], []
    for span in ("quarter", "ttm"):
        names = sorted({COLUMNS[k][0] for k in columns if COLUMNS[k][1] == span})
        if names:
            params[f"has_{span}"] = names
            tests.append("(SELECT true FROM gold.derived_financials d"
                         f" WHERE d.company_id = c.company_id AND d.span = '{span}'"
                         f" AND d.metric = ANY(%(has_{span})s::text[]) LIMIT 1) IS NOT NULL")
            stored.append(f"s.{span}_metrics && %(has_{span})s::text[]")
    reported = sorted({COLUMNS[k][0] for k in columns if COLUMNS[k][0] not in DERIVED})
    if reported:
        params["has_reported"] = reported
        tests.append("(SELECT true FROM gold.quarterly_financials q"
                     " WHERE q.company_id = c.company_id"
                     " AND q.metric = ANY(%(has_reported)s::text[]) LIMIT 1) IS NOT NULL")
        stored.append("s.reported_metrics && %(has_reported)s::text[]")
    test = "FALSE" if not tests else (
        f"coalesce((SELECT {' OR '.join(stored)} FROM gold.screen_companies s"
        f" WHERE s.company_id = c.company_id), {' OR '.join(tests)})")
    return f"({test})" if granted is None else f"(c.company_id <> ALL(%(granted)s) OR {test})"


def compile_screen(s: Screen, granted: list[str] | None) -> Compiled:
    unknown = [c for c in s.columns if c not in COLUMNS]
    if unknown:
        raise ScreenError(f"unknown columns: {unknown}")
    if len(s.columns) > MAX_COLUMNS:
        raise ScreenError(f"at most {MAX_COLUMNS} columns")
    if len(s.filters) > MAX_WHERE:
        raise ScreenError(f"at most {MAX_WHERE} filters")
    descending = s.sort.startswith("-")
    sort_key = s.sort.removeprefix("-")
    if sort_key not in COLUMNS and sort_key not in META_SORT:
        raise ScreenError(f"unknown sort {sort_key!r}")
    bad = [c for c in s.filer_categories if c not in FILER_CATEGORIES]
    if bad:
        raise ScreenError(f"filer_category: one of {list(FILER_CATEGORIES)}")

    # every value column the query needs gets a slot: selected ones first
    slots: dict[str, int] = {}
    for key in [*dict.fromkeys(s.columns), *(f.column for f in s.filters),
                *([sort_key] if sort_key in COLUMNS else [])]:
        slots.setdefault(key, len(slots))

    params: dict = {"limit": s.limit, "offset": s.offset}
    if granted is not None:
        params["granted"] = granted
    at = s.as_of is not None
    if at:
        params["as_of"] = s.as_of
    meta = ["TRUE"]
    if s.coverage:
        # 078: the stored metric lists (a company with quarterly rows) when this caller reads them,
        # else gold.financials_coverage (owner rights: metadata rows are public). Scalar subqueries
        # with a LIMIT, not EXISTS, inside coalesce: never a hashed subplan over all of gold
        meta.append("coalesce((SELECT s.reported_metrics <> '{}' FROM gold.screen_companies s"
                    " WHERE s.company_id = c.company_id),"
                    " (SELECT true FROM gold.financials_coverage v"
                    " WHERE v.company_id = c.company_id LIMIT 1), false)")
    if s.hide_blank:
        meta.append(_has_value_at(s.columns, granted, params) if at
                    else _has_value(s.columns, granted, params))
    if s.company_ids is not None:
        meta.append("c.company_id = ANY(%(ids)s)")
        params["ids"] = s.company_ids
    if s.granted_only:
        meta.append("c.company_id IN (SELECT r FROM authz.readable_companies('financials') r)"
                    if granted is None else "c.company_id = ANY(%(granted)s)")
    if s.sic:
        meta.append("c.sic = ANY(%(sic)s)")
        params["sic"] = s.sic
    if s.sic_prefix:
        meta.append("c.sic LIKE %(sic_prefix)s")
        params["sic_prefix"] = s.sic_prefix + "%"
    if s.industry:
        meta.append("c.sic_description ILIKE %(industry)s")
        params["industry"] = _like(s.industry)
    if s.q:
        meta.append("(c.name ILIKE %(q)s OR %(q_ticker)s = ANY(c.tickers))")
        params["q"], params["q_ticker"] = _like(s.q), s.q.upper()
    if s.exchanges:
        meta.append("EXISTS (SELECT 1 FROM unnest(c.exchanges) e"
                    " WHERE upper(e) = ANY(%(exchanges)s))")
        params["exchanges"] = [e.upper() for e in s.exchanges]
    if s.fye_months:
        meta.append("silver.fiscal_year_end_month(c.fiscal_year_end) = ANY(%(fye_months)s)")
        params["fye_months"] = s.fye_months
    if s.filer_categories:
        meta.append("gold.filer_category(c.filer_categories) = ANY(%(filer_categories)s)")
        params["filer_categories"] = s.filer_categories
    if s.sectors:
        meta.append("ss.sector = ANY(%(sectors)s)")
        params["sectors"] = s.sectors
    if s.sector_industries:
        meta.append("ss.industry = ANY(%(sector_industries)s)")
        params["sector_industries"] = s.sector_industries
    if s.issue:
        # under RLS on ops.quality_findings: a company the caller can't read never matches, so
        # this says nothing about it (the public status page has the counts only)
        meta.append("EXISTS (SELECT 1 FROM ops.quality_findings f"
                    " WHERE f.company_id = c.company_id AND f.check_name = %(issue)s)")
        params["issue"] = s.issue

    for key, i in slots.items():
        params[f"m{i}"], params[f"s{i}"] = COLUMNS[key][0], COLUMNS[key][1]

    where = ["TRUE"]
    for j, f in enumerate(s.filters):
        where.append(f"v.k{slots[f.column]} {OPS[f.op]} %(w{j})s")
        params[f"w{j}"] = f.value

    needed = {f.column for f in s.filters} | ({sort_key} if sort_key in COLUMNS else set())
    ranked = {k: i for k, i in slots.items() if k in needed}
    page = {k: i for k, i in slots.items() if k not in needed}
    direction = "DESC" if descending else "ASC"
    order = f"v.k{slots[sort_key]}" if sort_key in COLUMNS else META_SORT[sort_key]
    final = order.replace("v.k", "r.k").replace("b.", "r.")
    ranked_outputs = "".join(f", v.v{i}, v.src{i}, v.st{i}, v.why{i}, v.u{i}, v.k{i}"
                             for i in ranked.values())
    page_outputs = "".join(f", w.v{i}, w.src{i}, w.st{i}, w.why{i}, w.u{i}"
                           for i in page.values())
    # the latest period and the readings' stored inputs (INPUTS) are read once per row: for every
    # company when a sort or filter needs them (the ranked stage carries them to the page as
    # latest_period and in_<input>), else only for the page
    early = bool(ranked)
    a_inputs = tuple(f"a.{c}" for c in INPUTS)
    page_inputs = ("r.latest_period", *(f"r.in_{c}" for c in INPUTS[1:])) if early else a_inputs
    page_latest = page_inputs[0]
    carried = ", a.latest AS latest_period" + "".join(f", a.{c} AS in_{c}" for c in INPUTS[1:])
    # 046's sector, by range of SIC codes: for every company only when a sector filter or sort
    # needs it, else for the page's rows after the LIMIT. Joined for the whole catalog it cost
    # ~70 ms a call, and the range join's estimate (19 rows a company) misled the plan above it.
    sector_early = bool(s.sectors or s.sector_industries or sort_key == "sector")
    sector_cols = ", ss.sector, ss.industry AS sector_industry"
    sector_join = "LEFT JOIN silver.sic_sectors ss ON {}.sic BETWEEN ss.sic_from AND ss.sic_to"
    base_sector, base_join = ((sector_cols, sector_join.format("c")) if sector_early
                              else ("", ""))
    page_sector, page_join = (("", "") if sector_early
                              else (sector_cols, sector_join.format("r")))
    sql = f"""
WITH base AS (
    SELECT c.company_id, c.name, c.tickers, c.exchanges, c.sic,
           c.sic_description AS industry, c.fiscal_year_end,
           gold.filer_category(c.filer_categories) AS filer_category, c.filer_categories,
           c.state_of_incorporation,
           gold.not_applicable_metrics(c.company_id, c.sic) AS not_applicable{base_sector}
    FROM silver.companies c
    {base_join}
    WHERE {' AND '.join(meta)}
),
ranked AS (
    SELECT b.*{carried if early else ''}{ranked_outputs},
           count(*) OVER () AS total
    FROM base b
    {_latest('b.company_id', granted, at) if early else ''}
    LEFT JOIN LATERAL {_values(ranked, 'b.company_id', 'b.sic', a_inputs, granted, at)} v ON true
    WHERE {' AND '.join(where)}
    ORDER BY {order} {direction} NULLS LAST, b.company_id
    LIMIT %(limit)s OFFSET %(offset)s
)
SELECT r.*{'' if early else ', a.latest AS latest_period'}{page_outputs}{page_sector},
       cf.level AS confidence, cf.reasons AS confidence_reasons
FROM ranked r
{'' if early else _latest('r.company_id', granted, at)}
LEFT JOIN LATERAL {_values(page, 'r.company_id', 'r.sic', page_inputs, granted, at)} w ON true
LEFT JOIN LATERAL gold.company_confidence(r.company_id, r.sic, {page_latest}) cf ON true
{page_join}
ORDER BY {final} {direction} NULLS LAST, r.company_id
"""
    return Compiled(sql, params, list(dict.fromkeys(s.columns)), slots)


def shape(row: dict, compiled: Compiled, financials: bool) -> dict:
    """One API row: public metadata, then values and their sources (None unless the caller may
    read this company's financials: layer 1 again, on top of the query's own restriction)."""
    out = {k: row[k] for k in ("company_id", "name", "tickers", "exchanges", "sic", "industry",
                                "fiscal_year_end", "filer_category", "filer_categories",
                                "state_of_incorporation")}
    # additive (046): the open sector/industry grouping over the SIC code, public like it
    out["sector"] = row.get("sector")
    out["sector_industry"] = row.get("sector_industry")
    out["values"] = {k: row[f"v{compiled.slots[k]}"] if financials else None
                     for k in compiled.columns}
    out["sources"] = {k: row[f"src{compiled.slots[k]}"] if financials else None
                      for k in compiled.columns}
    # additive: how to read each value, only where there's something to say (035); an amount in
    # another currency (056) also says which, so it can be shown in it
    out["readings"] = {} if not financials else {
        k: {"status": row[f"st{compiled.slots[k]}"], "reason": row.get(f"why{compiled.slots[k]}"),
            **({"unit": row.get(f"u{compiled.slots[k]}")}
               if row[f"st{compiled.slots[k]}"] == "other_currency" else {})}
        for k in compiled.columns if row.get(f"st{compiled.slots[k]}")}
    out["latest_period"] = row.get("latest_period") if financials else None
    # additive (074): how far to trust the company's figures, from public signals, with the
    # reasons in words (gold.company_confidence); like the values, only for a reader of them
    out["confidence"] = ({"level": row["confidence"],
                          "reasons": row.get("confidence_reasons") or []}
                         if financials and row.get("confidence") else None)
    # from the public SIC, so also for a company the caller can't read
    out["not_applicable"] = row.get("not_applicable") or []
    return out


def notes_of(row: dict) -> str:
    """A CSV cell: what the readings say, since the export keeps the exact figures."""
    return "; ".join(f"{k}: {r['status'].replace('_', ' ')}"
                     f"{' (' + r['reason'] + ')' if r.get('reason') else ''}"
                     for k, r in row.get("readings", {}).items() if r["status"] != "behind")


def column_info(keys: list[str]) -> list[dict]:
    """`balance`: a point in time, so its _ttm column is the same number as its _q one."""
    return [{"key": k, "metric": COLUMNS[k][0], "span": COLUMNS[k][1], "unit": COLUMNS[k][2],
             "balance": COLUMNS[k][0] in POINT_IN_TIME}
            for k in keys]
