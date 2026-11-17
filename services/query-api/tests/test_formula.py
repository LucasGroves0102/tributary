"""Formula language: grammar, errors, limits, units and the compiled SQL. No database."""

import re
from decimal import Decimal

import pytest

from query_api.formula import FormulaError, analyze, compile_formula, parse, to_dict
from query_api.formula.check import DatasetInfo
from query_api.formula.grammar import MAX_DEPTH, MAX_LENGTH, BinOp, Call, Lag, Neg, Num, Ref


def _stores() -> dict[str, DatasetInfo]:
    """The caller's own dataset (B2c), as query_api.datasets reads it for a formula."""
    return {"stores": DatasetInfo("00000000-0000-4000-8000-000000000001", "stores",
                                  ("count", "sales", "share"), ("number", "money", "ratio"))}


def test_mine_refs_parse_by_how_many_names():
    ast = parse("mine.Stores.Count")
    assert isinstance(ast, Ref)
    assert (ast.dataset, ast.metric, ast.ticker) == ("stores", "count", None)
    assert ast.key == "mine.stores.count" and ast.dataset_pos == 5 and ast.metric_pos == 12
    ast = parse("nvda.mine.stores.count")
    assert isinstance(ast, Ref)
    assert (ast.ticker, ast.dataset, ast.ticker_length) == ("NVDA", "stores", 4)
    assert to_dict(parse("mine.stores.count"))["dataset"] == "stores"
    assert "dataset" not in to_dict(parse("KO.revenue"))
    # two names are always TICKER.metric: a company whose ticker is MINE stays reachable
    two = parse("MINE.revenue")
    assert isinstance(two, Ref) and two.ticker == "MINE" and two.dataset is None
    for bad in ("a.b.c", "KO.revenue.x", "a.b.mine.c", "mine.stores.", "mine..x"):
        with pytest.raises(FormulaError):
            parse(bad)
    with pytest.raises(FormulaError, match="Unexpected '.'"):
        parse("a.mine.b.c.d")


def test_mine_refs_take_their_units_and_never_a_ttm():
    a = analyze(parse("revenue / mine.stores.count"), "ttm", _stores())
    assert a.unit == "USD" and [i.ref for i in a.inputs] == ["ttm(revenue)", "mine.stores.count"]
    assert a.inputs[1].span == "quarter" and a.datasets["stores"].dataset_id.endswith("1")
    share = analyze(parse("mine.stores.share"), "quarter", _stores())
    assert (share.unit, share.display) == ("ratio", "percent")
    count = analyze(parse("mine.stores.count[-4q]"), "quarter", _stores())
    assert (count.unit, count.display) == ("ratio", "number")
    assert count.inputs[0].ref == "mine.stores.count[-4q]"
    assert analyze(parse("mine.stores.sales - revenue"), "quarter", _stores()).unit == "USD"
    with pytest.raises(FormulaError, match="Can't subtract"):
        analyze(parse("mine.stores.count - revenue"), "quarter", _stores())


def test_unknown_datasets_and_fields_suggest_only_the_callers_own():
    with pytest.raises(FormulaError) as e:
        analyze(parse("mine.stores.cuont"), "quarter", _stores())
    assert (e.value.code, e.value.hint) == ("unknown_field", "Did you mean count?")
    with pytest.raises(FormulaError) as e:
        analyze(parse("mine.stroes.count"), "quarter", _stores())
    assert (e.value.code, e.value.hint) == ("unknown_dataset", "Did you mean stores?")
    with pytest.raises(FormulaError) as e:
        analyze(parse("mine.stores.count"), "quarter", None)
    assert e.value.code == "unknown_dataset" and "stores" not in (e.value.hint or "")
    # every unknown name at once, a dataset's beside a metric's
    with pytest.raises(FormulaError) as e:
        analyze(parse("mine.x.y + revnue"), "quarter", _stores())
    assert {p.code for p in e.value.all()} == {"unknown_dataset", "unknown_metric"}


def test_a_formula_with_your_data_binds_its_dataset_and_field_by_id_and_position():
    ast = parse("revenue / mine.stores.count")
    a = analyze(ast, "quarter", _stores())
    c = compile_formula(ast, a, {None: "0000320193"}, "fiscal")
    assert "userdata.field_points(l.dataset_id, l.company_id, l.position" in c.sql
    assert "stores" not in c.sql and "mine." not in c.sql
    assert "00000000-0000-4000-8000-000000000001" in c.params
    assert c.sql.count("%s") == len(c.params)
    only = parse("mine.stores.sales")
    c = compile_formula(only, analyze(only, "quarter", _stores()), {None: "0000320193"},
                        "calendar")
    assert "gold.financials" not in c.sql and c.alignment == "calendar"
    assert "USD" in c.params  # money in the dataset's own currency
    # a formula without your data compiles as it always did
    plain = parse("revenue")
    assert "userdata" not in compile_formula(plain, analyze(plain), {None: "0000320193"},
                                             "fiscal").sql


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
    ("zzzzzzzz", "Unknown metric 'zzzzzzzz'", 0, 8, "Start typing to see the metric names"),
])
def test_unknown_metrics_suggest(src, message, pos, length, hint):
    e = error(src)
    assert (e.message, e.pos, e.length, e.hint, e.code) == (
        message, pos, length, hint, "unknown_metric")


def test_every_unknown_metric_is_reported_with_its_own_suggestion():
    problems = error("ebitda / revnue + ebitda - revenue").all()
    assert [(p.message, p.pos, p.length, p.hint) for p in problems] == [
        ("Unknown metric 'ebitda'", 0, 6, "Start typing to see the metric names"),
        ("Unknown metric 'revnue'", 9, 6, "Did you mean revenue?"),
    ]
    assert error("revnue").all()[0].message == "Unknown metric 'revnue'"


@pytest.mark.parametrize("src, pos, length", [
    ("revenue / 0", 8, 3),
    ("revenue / 0.00", 8, 6),
    ("revenue / (0)", 8, 4),
    ("revenue / -0", 8, 4),
    ("revenue / (1 - 1)", 8, 8),
    ("ttm(revenue) / (2 * 0)", 13, 8),
])
def test_dividing_by_a_constant_zero_is_an_error(src, pos, length):
    e = error(src)
    assert (e.message, e.code) == ("Can't divide by zero", "zero")
    assert e.hint == "Divide by a metric, or a number other than 0"
    assert (e.pos, e.length) == (pos, length)


def test_a_zero_that_is_not_a_divisor_is_fine():
    assert analyze(parse("revenue + 0")).unit == "USD"
    assert analyze(parse("0 / revenue")).unit == "1/USD"
    assert analyze(parse("revenue / (1 - 0.5)")).unit == "USD"


def test_huge_constants_dont_break_the_check():
    big = " * ".join(["9" * 30] * 12)
    assert analyze(parse(f"revenue / ({big})")).unit == "USD"


@pytest.mark.parametrize("src, display", [
    # ratio metrics
    ("gross_margin", "percent"),
    ("revenue_growth", "percent"),
    ("debt_to_equity", "multiple"),
    ("current_ratio", "multiple"),
    ("ttm(operating_margin)", "percent"),
    ("abs(current_ratio)", "multiple"),
    ("-gross_margin", "percent"),
    # same unit over same unit
    ("net_income / revenue", "percent"),
    ("(revenue - cost_of_revenue) / revenue", "percent"),
    ("NVDA.revenue / MSFT.revenue", "multiple"),
    ("NVDA.revenue / revenue", "multiple"),
    ("revenue / revenue[-4q]", "multiple"),
    ("operating_cash_flow / net_income", "multiple"),
    ("long_term_debt / stockholders_equity", "multiple"),
    ("(long_term_debt + long_term_debt_current) / stockholders_equity", "multiple"),
    ("current_assets / current_liabilities", "multiple"),
    ("net_income / stockholders_equity", "percent"),
    ("cash / total_assets", "percent"),
    ("eps_diluted / eps_diluted[-4q]", "multiple"),
    # changes
    ("revenue / revenue[-4q] - 1", "percent"),
    ("abs(revenue / revenue[-4q]) - 1", "percent"),
    ("(net_income / revenue) / (net_income / revenue)[-4q] - 1", "percent"),
    ("current_ratio - 2", "multiple"),
    ("gross_margin - 0.05", "percent"),
    # scaled: days, points, per-hundred
    ("receivables / revenue * 365", "number"),
    ("365 * receivables / revenue", "number"),
    ("receivables * 365 / revenue", "number"),
    ("receivables / (revenue / 365)", "number"),
    ("net_income / revenue * 100", "number"),
    ("net_income / revenue / 4", "number"),
    ("1 / current_ratio", "number"),
    ("gross_margin * 1", "percent"),
    # two ratios
    ("gross_margin - operating_margin", "percent"),
    ("gross_margin - current_ratio", "number"),
    ("net_margin * debt_to_equity", "percent"),
    ("current_ratio * debt_to_equity", "multiple"),
    ("gross_margin / operating_margin", "multiple"),
    ("current_ratio / debt_to_equity", "number"),
    # dimensions that cancel in a product have no usual reading
    ("revenue * (1 / revenue)", "number"),
])
def test_a_dimensionless_result_says_how_it_reads(src, display):
    a = analyze(parse(src))
    assert a.unit == "ratio"
    assert a.display == display


@pytest.mark.parametrize("src", ["revenue", "revenue * 365", "eps_diluted", "shares_outstanding",
                                 "gross_margin * revenue", "revenue * revenue"])
def test_a_result_with_a_unit_has_no_display(src):
    assert analyze(parse(src)).display is None


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
    src = "revenue / KO.revenue[-4q] * 1.5 - abs(-net_income) / revenue / 7"
    ast = parse(src)
    c = compile_formula(ast, analyze(ast), COMPANIES, "fiscal")
    for word in ("revenue", "net_income", "KO", "1.5", "0000077476"):
        assert word not in c.sql
    assert c.params[:10] == [0, "0000077476", "revenue", "quarter", 0,
                             1, "0000021344", "revenue", "quarter", 4]
    assert Decimal("1.5") in c.params and Decimal("7") in c.params
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
