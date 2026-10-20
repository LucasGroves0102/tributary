"""Compile an analyzed formula to one parameterized query over gold.financials.

Alignment: every input row gets a period key, quarters since year 0. With one company and
basis fiscal the key is its fiscal quarter (fiscal_year * 4 + fiscal_quarter - 1; the calendar
quarter when its fiscal year end is unknown) over every fiscal row. Otherwise (basis calendar,
or more than one company) the key is the calendar quarter of gold.financials' calendar rule,
over calendar_primary rows only. If two rows of an input share a key, the latest period end wins.

A lag of n quarters moves an input's rows to key + n. A point exists for each key where the
expression is not NULL: a missing input or a division by zero (NULLIF) gives no point.
The SQL text is built only from fixed fragments and input indexes; companies, metrics, spans,
lags, constants and dates are all bind parameters.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from query_api.formula.check import Analysis
from query_api.formula.grammar import BinOp, Call, Lag, Neg, Node, Num, Ref


@dataclass
class Compiled:
    sql: str
    params: list[Any]
    alignment: str  # fiscal | calendar


def alignment(basis: str, company_ids: set[str]) -> str:
    return "fiscal" if basis == "fiscal" and len(company_ids) == 1 else "calendar"


def compile_formula(ast: Node, a: Analysis, companies: dict[str | None, str], basis: str,
                    start: date | None = None, end: date | None = None) -> Compiled:
    """companies: ticker (None = default company) → company_id for every input."""
    params: list[Any] = []
    rows = []
    for i, inp in enumerate(a.inputs):
        rows.append("(%s::int, %s::text, %s::text, %s::text, %s::int)")
        params += [i, companies[inp.ticker], inp.metric, inp.span, inp.lag]
    align = alignment(basis, {companies[i.ticker] for i in a.inputs})
    fiscal = align == "fiscal"

    pivots = ",\n".join(f"max(value) FILTER (WHERE i = {i}) AS v{i}"
                        for i in range(len(a.inputs)))
    expr_params: list[Any] = []
    expr = _sql(ast, a, expr_params)

    sql = f"""
WITH input (i, company_id, metric, span, lag) AS (VALUES {", ".join(rows)}),
keyed AS (
    SELECT DISTINCT ON (l.i, k) l.i, l.lag, k, f.period_start, f.period_end, f.value,
           f.value_source, f.accession_no, f.form, f.filed, f.source_accessions,
           f.ingested_at, f.fiscal_year IS NOT NULL AS fiscal_key,
           (SELECT ff.cik FROM silver.financial_facts ff
            WHERE ff.accession_no = f.accession_no LIMIT 1) AS filer_cik
    FROM input l
    JOIN gold.financials f
      ON f.company_id = l.company_id AND f.metric = l.metric AND f.span = l.span
    CROSS JOIN LATERAL (SELECT CASE WHEN %s AND f.fiscal_year IS NOT NULL
                                    THEN f.fiscal_year * 4 + f.fiscal_quarter - 1
                                    ELSE f.calendar_year * 4 + f.calendar_quarter - 1
                               END AS k) key
    WHERE %s OR f.calendar_primary
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
               'ingested_at', ingested_at, 'filer_cik', filer_cik) ORDER BY i) AS inputs,
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
    params += [fiscal, fiscal, *expr_params, start, start, end, end]
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
