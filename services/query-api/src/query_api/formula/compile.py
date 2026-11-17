"""Compile an analyzed formula to one parameterized query over gold.financials.

Alignment: every input row gets a period key, quarters since year 0. With one company and
basis fiscal the key is its fiscal quarter (fiscal_year * 4 + fiscal_quarter - 1; the calendar
quarter when its fiscal year end is unknown) over every fiscal row. Otherwise (basis calendar,
or more than one company) the key is the calendar quarter of gold.financials' calendar rule,
over calendar_primary rows only. If two rows of an input share a key, the latest period end wins.

A lag of n quarters moves an input's rows to key + n. A point exists for each key where the
expression is not NULL: a missing input or a division by zero (NULLIF) gives no point. Each
input row carries `not_meaningful`, migration 035's reason a ratio can't be read (the tables'
n/m; 057: the dollar bases only for a company whose amounts are dollars); the route marks a point
built from one (F7a, B5's charts plotted the raw ratio). And its `unit`, the row's own (057: a
euro company's revenue is "EUR"), which the route reads as the series' currency.
The SQL text is built only from fixed fragments and input indexes; companies, metrics, spans,
lags, constants and dates are all bind parameters. The keyed rows also filter on the input
companies as a literal array so the filter reaches gold.financials' base tables.

As of a day (`as_of`, migrations 085-087; query_api.point_in_time): each input reads its company's
rows as they stood at the end of that day (gold.company_financials_at, one company, span and metric
an input) and the currency then (gold.amount_currency_of with the day); the rest is the same.

The caller's own data (`mine.<dataset>.<field>`, B2c, migration 100): such an input reads
userdata.field_points under the caller's row-level security, the dataset by its id and the field by
its position (both bound, never a name in the SQL text): each day of the company's rows, the
version known at the end of `as_of` (the latest without it), labelled with gold.financials' fiscal
and calendar rules, so it keys like a period end. If two days share a key, the latest wins, as for
Tributary's rows. Its rows have no filing: `value_source` 'yours' and `filed` the day they were
known. A formula without such inputs compiles to exactly the SQL it always did.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from query_api.formula.check import FIELD_UNITS, Analysis
from query_api.formula.grammar import BinOp, Call, Lag, Neg, Node, Num, Ref


@dataclass
class Compiled:
    sql: str
    params: list[Any]
    alignment: str  # fiscal | calendar


def alignment(basis: str, company_ids: set[str]) -> str:
    return "fiscal" if basis == "fiscal" and len(company_ids) == 1 else "calendar"


def compile_formula(ast: Node, a: Analysis, companies: dict[str | None, str], basis: str,
                    start: date | None = None, end: date | None = None,
                    as_of: date | None = None) -> Compiled:
    """companies: ticker (None = default company) → company_id for every input. as_of: the
    inputs as they stood at the end of that day."""
    if any(inp.dataset for inp in a.inputs):
        return _compile_with_data(ast, a, companies, basis, start, end, as_of)
    params: list[Any] = []
    rows = []
    for i, inp in enumerate(a.inputs):
        rows.append("(%s::int, %s::text, %s::text, %s::text, %s::int)")
        params += [i, companies[inp.ticker], inp.metric, inp.span, inp.lag]
    align = alignment(basis, {companies[i.ticker] for i in a.inputs})
    fiscal = align == "fiscal"

    source = ("""CROSS JOIN LATERAL (SELECT gold.amount_currency(l.company_id) AS currency) cu
    JOIN gold.financials f
      ON f.company_id = l.company_id AND f.metric = l.metric AND f.span = l.span"""
              if as_of is None else
              # one input's company, span and metric as of the day (087)
              """CROSS JOIN LATERAL (SELECT c.unit AS currency
                        FROM gold.amount_currency_of(l.company_id, %s::date) c) cu
    CROSS JOIN LATERAL gold.company_financials_at(l.company_id, %s::date, l.span,
                                                  ARRAY[l.metric]) f""")
    if as_of is not None:
        params += [as_of, as_of]
    pivots = ",\n".join(f"max(value) FILTER (WHERE i = {i}) AS v{i}"
                        for i in range(len(a.inputs)))
    expr_params: list[Any] = []
    expr = _sql(ast, a, expr_params)

    sql = f"""
WITH input (i, company_id, metric, span, lag) AS (VALUES {", ".join(rows)}),
keyed AS (
    SELECT DISTINCT ON (l.i, k) l.i, l.lag, k, f.period_start, f.period_end, f.value,
           f.value_source, f.accession_no, f.form, f.filed, f.source_accessions,
           f.ingested_at, f.fiscal_year IS NOT NULL AS fiscal_key, f.unit,
           gold.not_meaningful(l.metric, f.value, f.inputs, NULL, cu.currency) AS not_meaningful,
           (SELECT ff.cik FROM silver.financial_facts ff
            WHERE ff.accession_no = f.accession_no LIMIT 1) AS filer_cik
    FROM input l
    {source}
    CROSS JOIN LATERAL (SELECT CASE WHEN %s AND f.fiscal_year IS NOT NULL
                                    THEN f.fiscal_year * 4 + f.fiscal_quarter - 1
                                    ELSE f.calendar_year * 4 + f.calendar_quarter - 1
                               END AS k) key
    WHERE f.company_id = ANY(%s::text[]) AND (%s OR f.calendar_primary)
    ORDER BY l.i, k, f.period_end DESC
),
points AS (
    SELECT k + lag AS k,
           coalesce(max(period_end) FILTER (WHERE lag = 0),
                    max((period_end + make_interval(months => 3 * lag))::date)) AS period_end,
           bool_and(fiscal_key) AS fiscal_key,
           jsonb_agg(jsonb_build_object(
               'input', i, 'period_start', period_start, 'period_end', period_end,
               'value', value, 'value_source', value_source, 'accession_no', accession_no,
               'form', form, 'filed', filed, 'source_accessions', source_accessions,
               'ingested_at', ingested_at, 'filer_cik', filer_cik,
               'not_meaningful', not_meaningful, 'unit', unit) ORDER BY i) AS inputs,
           {pivots}
    FROM keyed
    GROUP BY k + lag
)
SELECT k AS period_key, period_end, fiscal_key, value, inputs
FROM (SELECT k, period_end, fiscal_key, inputs, {expr} AS value FROM points) p
WHERE value IS NOT NULL
  AND (%s::date IS NULL OR period_end >= %s::date)
  AND (%s::date IS NULL OR period_end <= %s::date)
ORDER BY k
"""
    # The company filter repeats input's ids as a literal array: gold.financials is a UNION
    # view, and a filter that arrives only through the join can't reach its indexes (12 s →
    # 0.02 s for one company on hermes).
    ids = sorted({companies[inp.ticker] for inp in a.inputs})
    params += [fiscal, ids, fiscal, *expr_params, start, start, end, end]
    return Compiled(sql, params, align)


_POINTS = """
points AS (
    SELECT k + lag AS k,
           coalesce(max(period_end) FILTER (WHERE lag = 0),
                    max((period_end + make_interval(months => 3 * lag))::date)) AS period_end,
           bool_and(fiscal_key) AS fiscal_key,
           jsonb_agg(jsonb_build_object(
               'input', i, 'period_start', period_start, 'period_end', period_end,
               'value', value, 'value_source', value_source, 'accession_no', accession_no,
               'form', form, 'filed', filed, 'source_accessions', source_accessions,
               'ingested_at', ingested_at, 'filer_cik', filer_cik,
               'not_meaningful', not_meaningful, 'unit', unit) ORDER BY i) AS inputs,
           {pivots}
    FROM keyed
    GROUP BY k + lag
)
SELECT k AS period_key, period_end, fiscal_key, value, inputs
FROM (SELECT k, period_end, fiscal_key, inputs, {expr} AS value FROM points) p
WHERE value IS NOT NULL
  AND (%s::date IS NULL OR period_end >= %s::date)
  AND (%s::date IS NULL OR period_end <= %s::date)
ORDER BY k
"""


def _compile_with_data(ast: Node, a: Analysis, companies: dict[str | None, str], basis: str,
                       start: date | None, end: date | None, as_of: date | None) -> Compiled:
    """compile_formula for a formula that reads the caller's own data: Tributary's inputs as
    always (a branch of `keyed`), the datasets' through userdata.field_points (the other)."""
    params: list[Any] = []
    align = alignment(basis, {companies[i.ticker] for i in a.inputs})
    fiscal = align == "fiscal"
    ours = [(i, inp) for i, inp in enumerate(a.inputs) if not inp.dataset]
    mine = [(i, inp) for i, inp in enumerate(a.inputs) if inp.dataset]
    ctes, branches = [], []
    if ours:
        values = []
        for i, inp in ours:
            values.append("(%s::int, %s::text, %s::text, %s::text, %s::int)")
            params += [i, companies[inp.ticker], inp.metric, inp.span, inp.lag]
        ctes.append(f"input (i, company_id, metric, span, lag) AS (VALUES {', '.join(values)})")
    if mine:
        values = []
        for i, inp in mine:
            info = a.datasets[inp.dataset or ""]
            unit = FIELD_UNITS[info.unit_of(inp.metric)][2]
            if unit.startswith("USD"):  # money in the dataset's own currency
                unit = info.currency + unit[3:]
            values.append("(%s::int, %s::uuid, %s::text, %s::int, %s::int, %s::text)")
            params += [i, info.dataset_id, companies[inp.ticker],
                       info.fields.index(inp.metric) + 1, inp.lag, unit]
        ctes.append("mine_input (i, dataset_id, company_id, position, lag, unit) AS"
                    f" (VALUES {', '.join(values)})")
    if ours:
        source = ("""CROSS JOIN LATERAL (SELECT gold.amount_currency(l.company_id) AS currency) cu
        JOIN gold.financials f
          ON f.company_id = l.company_id AND f.metric = l.metric AND f.span = l.span"""
                  if as_of is None else
                  """CROSS JOIN LATERAL (SELECT c.unit AS currency
                            FROM gold.amount_currency_of(l.company_id, %s::date) c) cu
        CROSS JOIN LATERAL gold.company_financials_at(l.company_id, %s::date, l.span,
                                                      ARRAY[l.metric]) f""")
        if as_of is not None:
            params += [as_of, as_of]
        branches.append(f"""(
        SELECT DISTINCT ON (l.i, k) l.i, l.lag, k, f.period_start, f.period_end, f.value,
               f.value_source, f.accession_no, f.form, f.filed, f.source_accessions,
               f.ingested_at, f.fiscal_year IS NOT NULL AS fiscal_key, f.unit,
               gold.not_meaningful(l.metric, f.value, f.inputs, NULL, cu.currency)
                   AS not_meaningful,
               (SELECT ff.cik FROM silver.financial_facts ff
                WHERE ff.accession_no = f.accession_no LIMIT 1) AS filer_cik
        FROM input l
        {source}
        CROSS JOIN LATERAL (SELECT CASE WHEN %s AND f.fiscal_year IS NOT NULL
                                        THEN f.fiscal_year * 4 + f.fiscal_quarter - 1
                                        ELSE f.calendar_year * 4 + f.calendar_quarter - 1
                                   END AS k) key
        WHERE f.company_id = ANY(%s::text[]) AND (%s OR f.calendar_primary)
        ORDER BY l.i, k, f.period_end DESC)""")
        params += [fiscal, sorted({companies[inp.ticker] for _, inp in ours}), fiscal]
    if mine:
        branches.append("""(
        SELECT DISTINCT ON (l.i, k) l.i, l.lag, k, NULL::date AS period_start,
               p.day AS period_end, p.value, 'yours'::text AS value_source,
               NULL::text AS accession_no, NULL::text AS form, p.known_at AS filed,
               '{}'::text[] AS source_accessions, NULL::timestamptz AS ingested_at,
               p.fiscal_year IS NOT NULL AS fiscal_key, l.unit,
               NULL::text AS not_meaningful, NULL::text AS filer_cik
        FROM mine_input l
        CROSS JOIN LATERAL userdata.field_points(l.dataset_id, l.company_id, l.position,
                                                 %s::date) p
        CROSS JOIN LATERAL (SELECT CASE WHEN %s AND p.fiscal_year IS NOT NULL
                                        THEN p.fiscal_year * 4 + p.fiscal_quarter - 1
                                        ELSE p.calendar_year * 4 + p.calendar_quarter - 1
                                   END AS k) key
        ORDER BY l.i, k, p.day DESC)""")
        params += [as_of, fiscal]
    pivots = ",\n".join(f"max(value) FILTER (WHERE i = {i}) AS v{i}"
                        for i in range(len(a.inputs)))
    expr_params: list[Any] = []
    expr = _sql(ast, a, expr_params)
    sql = (f"WITH {', '.join(ctes)},\nkeyed AS (\n{' UNION ALL '.join(branches)}\n),"
           + _POINTS.format(pivots=pivots, expr=expr))
    params += [*expr_params, start, start, end, end]
    return Compiled(sql, params, align)


def _sql(n: Node, a: Analysis, params: list[Any]) -> str:
    match n:
        case Num(value=v):
            params.append(v)
            return "%s::numeric"
        case Ref():
            return f"v{int(a.input_of[id(n)])}"
        case Lag(expr=e) | Call(name="ttm", arg=e):
            return _sql(e, a, params)
        case Call(name="abs", arg=e):
            return f"abs({_sql(e, a, params)})"
        case Neg(arg=e):
            return f"(-{_sql(e, a, params)})"
        case BinOp(op="/", left=left, right=right):
            return f"({_sql(left, a, params)} / NULLIF({_sql(right, a, params)}, 0))"
        case BinOp(op=op, left=left, right=right) if op in ("+", "-", "*"):
            return f"({_sql(left, a, params)} {op} {_sql(right, a, params)})"
    raise TypeError(n)
