"""Screener queries: company metadata (public) joined to the latest metric values (RLS).

Like formula/compile.py, the SQL text holds no user input: column keys and sort keys are
checked against fixed catalogs and become fixed fragments (v0, v1, ...); every user value is a
bind parameter.

Columns are `<metric>_ttm` (trailing twelve months; balance-sheet metrics at the quarter end)
or `<metric>_q` (latest fiscal quarter), for every metric in the formula CATALOG with values in
that view. Each value is the latest one of that metric and span (gold.latest_value: an index
lookup per company and column, migration 032) and carries its own period_end and filing.

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
from decimal import Decimal, InvalidOperation
from typing import Literal, get_args

from query_api.formula import CATALOG

SPAN_SUFFIX = {"quarter": "q", "ttm": "ttm"}
# column key → (metric, span, unit)
COLUMNS: dict[str, tuple[str, str, str]] = {
    f"{metric}_{SPAN_SUFFIX[span]}": (metric, span, unit)
    for metric, (unit, spans) in CATALOG.items() for span in spans
}
# sortable metadata → SQL over the base CTE (fixed fragments)
META_SORT = {"name": "b.name", "company_id": "b.company_id", "sic": "b.sic",
             "fiscal_year_end": "b.fiscal_year_end", "filer_category": "b.filer_category",
             "state_of_incorporation": "b.state_of_incorporation"}
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
    granted_only: bool = False             # only companies the caller can read financials for
    sic: list[str] = field(default_factory=list)
    sic_prefix: str | None = None
    industry: str | None = None
    q: str | None = None
    exchanges: list[str] = field(default_factory=list)
    fye_months: list[int] = field(default_factory=list)
    filer_categories: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    sort: str = "-revenue_ttm"
    limit: int = 50
    offset: int = 0


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


def _values(slots: dict[str, int], company: str, granted: list[str] | None) -> str:
    """A lateral row with v<i> and src<i> of these slots, for the company in the column
    `company`. Closed catalog: a one-time filter, so an ungranted company reads no value."""
    if not slots:
        return "(SELECT 1)"
    outputs, joins = [], []
    for i in slots.values():
        outputs += [f"x{i}.value AS v{i}",
                    f"CASE WHEN x{i}.period_end IS NOT NULL THEN jsonb_build_object("
                    f"'period_end', x{i}.period_end, 'accession_no', x{i}.accession_no,"
                    f" 'form', x{i}.form, 'filed', x{i}.filed) END AS src{i}"]
        joins.append(f"LEFT JOIN LATERAL gold.latest_value({company}, %(s{i})s, %(m{i})s) x{i}"
                     " ON true")
    gate = "" if granted is None else f" WHERE {company} = ANY(%(granted)s)"
    return f"(SELECT {', '.join(outputs)} FROM (SELECT 1{gate}) one {' '.join(joins)})"


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
    meta = ["TRUE"]
    if s.coverage:
        meta.append("EXISTS (SELECT 1 FROM gold.financials_coverage v"
                    " WHERE v.company_id = c.company_id)")
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
        meta.append("left(c.fiscal_year_end, 2)::int = ANY(%(fye_months)s)")
        params["fye_months"] = s.fye_months
    if s.filer_categories:
        meta.append("gold.filer_category(c.filer_categories) = ANY(%(filer_categories)s)")
        params["filer_categories"] = s.filer_categories

    for key, i in slots.items():
        params[f"m{i}"], params[f"s{i}"] = COLUMNS[key][0], COLUMNS[key][1]

    where = ["TRUE"]
    for j, f in enumerate(s.filters):
        where.append(f"v.v{slots[f.column]} {OPS[f.op]} %(w{j})s")
        params[f"w{j}"] = f.value

    needed = {f.column for f in s.filters} | ({sort_key} if sort_key in COLUMNS else set())
    ranked = {k: i for k, i in slots.items() if k in needed}
    page = {k: i for k, i in slots.items() if k not in needed}
    direction = "DESC" if descending else "ASC"
    order = f"v.v{slots[sort_key]}" if sort_key in COLUMNS else META_SORT[sort_key]
    final = order.replace("v.v", "r.v").replace("b.", "r.")
    ranked_outputs = "".join(f", v.v{i}, v.src{i}" for i in ranked.values())
    page_outputs = "".join(f", w.v{i}, w.src{i}" for i in page.values())
    sql = f"""
WITH base AS (
    SELECT c.company_id, c.name, c.tickers, c.exchanges, c.sic,
           c.sic_description AS industry, c.fiscal_year_end,
           gold.filer_category(c.filer_categories) AS filer_category, c.filer_categories,
           c.state_of_incorporation
    FROM silver.companies c
    WHERE {' AND '.join(meta)}
),
ranked AS (
    SELECT b.*{ranked_outputs}, count(*) OVER () AS total
    FROM base b
    LEFT JOIN LATERAL {_values(ranked, 'b.company_id', granted)} v ON true
    WHERE {' AND '.join(where)}
    ORDER BY {order} {direction} NULLS LAST, b.company_id
    LIMIT %(limit)s OFFSET %(offset)s
)
SELECT r.*{page_outputs}
FROM ranked r
LEFT JOIN LATERAL {_values(page, 'r.company_id', granted)} w ON true
ORDER BY {final} {direction} NULLS LAST, r.company_id
"""
    return Compiled(sql, params, list(dict.fromkeys(s.columns)), slots)


def shape(row: dict, compiled: Compiled, financials: bool) -> dict:
    """One API row: public metadata, then values and their sources (None unless the caller may
    read this company's financials: layer 1 again, on top of the query's own restriction)."""
    out = {k: row[k] for k in ("company_id", "name", "tickers", "exchanges", "sic", "industry",
                                "fiscal_year_end", "filer_category", "filer_categories",
                                "state_of_incorporation")}
    out["values"] = {k: row[f"v{compiled.slots[k]}"] if financials else None
                     for k in compiled.columns}
    out["sources"] = {k: row[f"src{compiled.slots[k]}"] if financials else None
                      for k in compiled.columns}
    return out


def column_info(keys: list[str]) -> list[dict]:
    return [{"key": k, "metric": COLUMNS[k][0], "span": COLUMNS[k][1], "unit": COLUMNS[k][2]}
            for k in keys]
