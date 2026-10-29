"""Formula language: grammar, errors, limits, units and the compiled SQL. No database."""

import re
from decimal import Decimal

import pytest

from query_api.formula import FormulaError, analyze, compile_formula, parse, to_dict
from query_api.formula.grammar import MAX_DEPTH, MAX_LENGTH, BinOp, Call, Lag, Neg, Num, Ref


def error(src: str, view: str = "quarter") -> FormulaError:
    with pytest.raises(FormulaError) as e:
        analyze(parse(src), view)
    return e.value


def test_precedence_and_associativity():
    ast = parse("a - b - c * d / e")
    assert isinstance(ast, BinOp) and ast.op == "-"
    assert isinstance(ast.left, BinOp) and ast.left.op == "-"  # (a - b) - ...
    right = ast.right
    assert isinstance(right, BinOp) and right.op == "/"
    assert isinstance(right.left, BinOp) and right.left.op == "*"


def test_refs_lags_functions_and_unary_minus():
    ast = parse("-abs(KO.Revenue[-1y]) + ttm((net_income))")
    assert isinstance(ast, BinOp)
    neg = ast.left
    assert isinstance(neg, Neg) and isinstance(neg.arg, Call) and neg.arg.name == "abs"
    lag = neg.arg.arg
    assert isinstance(lag, Lag) and lag.quarters == 4
    assert lag.expr == Ref(5, 10, "revenue", "KO", 8, 2)
    assert isinstance(ast.right, Call) and ast.right.name == "ttm"
    assert isinstance(ast.right.arg, Ref) and ast.right.arg.ticker is None


def test_numbers_are_decimals():
    ast = parse("0.25 * revenue + .5")
    assert isinstance(ast, BinOp) and isinstance(ast.right, Num)
    assert ast.right.value == Decimal(".5")


def test_ast_json():
    assert to_dict(parse("revenue[-4q] / 2")) == {
        "type": "binary", "op": "/", "pos": 0, "length": 16,
        "left": {"type": "lag", "quarters": 4, "pos": 0, "length": 12,
                 "expr": {"type": "ref", "ticker": None, "metric": "revenue", "pos": 0,
                          "length": 7}},
        "right": {"type": "number", "value": "2", "pos": 15, "length": 1}}


@pytest.mark.parametrize("src, message, pos", [
    ("", "Type a formula", 0),
    ("revenue +", "Formula ends too early", 9),
    ("revenue revenue", "Missing operator", 8),
    ("(revenue", "Missing ')'", 8),
    ("revenue)", "Unexpected ')'", 7),
    ("revenue * 25%", "Write percentages as ratios", 12),
    ("revenue[4q]", "Lags look back", 8),
    ("revenue[-4m]", "Lag unit must be q", 10),
    ("revenue[-q]", "Expected a lag", 9),
    ("revenue[-1.5q]", "Expected a lag", 9),
    ("revenue[-4q", "Missing ']'", 11),
    ("4[-1q]", "Lags apply to a metric", 1),
    ("KO.", "Expected a metric after 'KO.'", 3),
    ("max(revenue)", "Unknown function 'max'", 0),
    ("revenue; DROP TABLE x", "Unexpected character ';'", 7),
    ("revenue -- comment", "Unknown metric 'comment'", 11),  # revenue - (-comment)
    ("'1' or 1=1", "Unexpected character", 0),
    ("revenue$1", "Unexpected character '$'", 7),
])
def test_syntax_errors_have_positions(src, message, pos):
    e = error(src)
    assert message in e.message and e.pos == pos


@pytest.mark.parametrize("src, message, pos, length, hint", [
    ("revnue / revenue", "Unknown metric 'revnue'", 0, 6, "Did you mean revenue?"),
    ("KO.revnue / PEP.revenue", "Unknown metric 'revnue'", 3, 6, "Did you mean revenue?"),
    ("zzzzzzzz", "Unknown metric 'zzzzzzzz'", 0, 8, "GET /v1/metrics lists the metrics"),
])
def test_unknown_metrics_suggest(src, message, pos, length, hint):
    e = error(src)
    assert (e.message, e.pos, e.length, e.hint, e.code) == (
        message, pos, length, hint, "unknown_metric")


@pytest.mark.parametrize("src, unit", [
    ("net_income / revenue", "ratio"),
    ("revenue / revenue[-4q] - 1", "ratio"),
    ("net_income / weighted_shares_diluted", "USD/shares"),
    ("eps_diluted * weighted_shares_diluted", "USD"),
    ("revenue - capex + 1000", "USD"),
    ("gross_margin * revenue", "USD"),
    ("revenue * revenue", "USD^2"),
    ("shares_outstanding / revenue", "shares/USD"),
    ("abs(-free_cash_flow)", "USD"),
    ("(revenue * revenue) / revenue", "USD"),
])
def test_units(src, unit):
    assert analyze(parse(src)).unit == unit


@pytest.mark.parametrize("src, message, pos", [
    ("revenue + eps_diluted", "Can't add dollars and dollars per share", 8),
    ("revenue - shares_outstanding", "Can't subtract dollars and shares", 8),
    ("net_margin + revenue", "Can't add a ratio and dollars", 11),
])
def test_unit_errors(src, message, pos):
    e = error(src)
    assert (e.message, e.pos, e.code) == (message, pos, "unit")


def test_spans():
    assert error("ttm(eps_diluted)").message == "eps_diluted has no trailing-twelve-month values"
    assert error("eps_diluted", view="ttm").code == "span"
    assert error("roe").message == "roe has no quarterly values"
    assert analyze(parse("roe"), "ttm").inputs[0].span == "ttm"
    assert analyze(parse("ttm(roe)")).inputs[0].span == "ttm"


def test_inputs_are_deduplicated_and_lags_push_down():
    a = analyze(parse("(KO.revenue - KO.revenue[-4q])[-1q] + KO.revenue[-1q] + ttm(revenue)"))
    assert [i.ref for i in a.inputs] == ["KO.revenue[-1q]", "KO.revenue[-5q]", "ttm(revenue)"]
    assert a.tickers == {"KO": (1, 2)} and a.bare == (60, 7)


def test_constants_alone_are_rejected():
    assert error("1 + 2").code == "no_metric"


def test_limits():
    assert error("revenue + " * 60 + "revenue").code == "limit"  # longer than MAX_LENGTH
    assert len("revenue + " * 60) > MAX_LENGTH
    assert error("(" * (MAX_DEPTH + 1) + "revenue" + ")" * (MAX_DEPTH + 1)).code == "limit"
    assert error("-" * (MAX_DEPTH + 1) + "revenue").code == "limit"
    assert error("revenue[-41q]").code == "limit"
    assert error("revenue[-11y]").code == "limit"
    assert error("(revenue[-30q])[-20q]").message == "Lags add up to more than 40 quarters"
    six = " + ".join(f"T{i}.revenue" for i in range(6))
    assert error(six).message == "A formula can use at most 5 companies"
    assert analyze(parse(" + ".join(f"T{i}.revenue" for i in range(5)))).tickers
    many = " + ".join(f"revenue[-{i}q]" for i in range(1, 22))
    assert error(many).message == "A formula can read at most 20 series"
    assert error("1" * 31).message == "Number is too long"
    assert error("a" * 65).message == "Name is too long"


def test_no_recursion_error_on_deep_input():
    assert error("(" * 10_000).code in ("limit", "syntax")


COMPANIES = {None: "0000077476", "KO": "0000021344"}


def test_compiled_sql_carries_no_user_text():
    src = "revenue / KO.revenue[-4q] * 1.5 - abs(-net_income) / revenue / 0"
    ast = parse(src)
    c = compile_formula(ast, analyze(ast), COMPANIES, "fiscal")
    for word in ("revenue", "net_income", "KO", "1.5", "0000077476"):
        assert word not in c.sql
    assert c.params[:10] == [0, "0000077476", "revenue", "quarter", 0,
                             1, "0000021344", "revenue", "quarter", 4]
    assert Decimal("1.5") in c.params and Decimal("0") in c.params
    assert "NULLIF" in c.sql and "abs(" in c.sql
    assert c.sql.count("%s") == len(c.params)
    assert c.alignment == "calendar"  # two companies


def test_expression_sql():
    ast = parse("-(net_income / revenue) + revenue[-4q] / revenue * 2")
    c = compile_formula(ast, analyze(ast), COMPANIES, "fiscal")
    expr = re.search(r"SELECT k, period_end, fiscal_key, inputs, (.*) AS value", c.sql)
    assert expr and expr.group(1) == \
        "((-(v0 / NULLIF(v1, 0))) + ((v2 / NULLIF(v1, 0)) * %s::numeric))"
    assert c.alignment == "fiscal"


@pytest.mark.parametrize("basis, companies, expected", [
    ("fiscal", {None: "1", "KO": "1"}, "fiscal"),
    ("calendar", {None: "1", "KO": "1"}, "calendar"),
    ("fiscal", {None: "1", "KO": "2"}, "calendar"),
])
def test_alignment_rule(basis, companies, expected):
    ast = parse("revenue / KO.revenue")
    assert compile_formula(ast, analyze(ast), companies, basis).alignment == expected
    fiscal = expected == "fiscal"
    c = compile_formula(ast, analyze(ast), companies, basis)
    assert c.params[10:13] == [fiscal, sorted(set(companies.values())), fiscal]


def test_companies_filter_is_a_bound_array():
    # Repeated as a literal array so the filter reaches gold.financials' base tables.
    ast = parse("(KO.revenue + PEP.revenue) / revenue")
    c = compile_formula(ast, analyze(ast), {None: "1", "KO": "2", "PEP": "2"}, "calendar")
    assert "f.company_id = ANY(%s::text[])" in c.sql
    assert ["1", "2"] in c.params


def test_date_range_is_bound():
    from datetime import date
    ast = parse("revenue")
    c = compile_formula(ast, analyze(ast), COMPANIES, "fiscal", date(2020, 1, 1), None)
    assert c.params[-4:] == [date(2020, 1, 1), date(2020, 1, 1), None, None]
