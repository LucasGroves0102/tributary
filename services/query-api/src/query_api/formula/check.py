"""Semantic checks: metric names, spans, units and limits. Pure: no database or OpenFGA.

Units are exponents of USD and shares: (0, 0) ratio, (1, 0) USD, (0, 1) shares,
(1, -1) USD/shares. + and - need equal units, except that a constant takes the other side's
unit; * and / add and subtract exponents. Dividing by a constant that is zero is an error (a
zero in the data is only a gap: compile.py's NULLIF).

A dimensionless result is not necessarily a percent, so it also gets a `display`, from the
formula's shape (the web app draws from it; a layer's own choice overrides it):

- a ratio metric: `DISPLAY`, else percent (margins, growth; debt to equity is a multiple);
- x / y in the same unit: multiple across companies or for one metric over itself
  (NVDA.revenue / MSFT.revenue, revenue / revenue[-4q]); percent for a share of revenue or of
  total assets, or a flow over a balance (a return); else multiple (debt / equity, operating
  cash flow / net income);
- multiple - 1 (or + 1) is a percent change; any other sum of a value and a constant keeps the
  value's display, and a sum of two values too when they agree, else number;
- x * or / a constant (other than 1) is number: receivables / revenue * 365 is days, not a
  percent; so is a constant / x. Products and quotients of ratios: alike stays alike, percent *
  multiple is percent, percent / percent is a multiple, anything else number.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from query_api.formula.grammar import (
    MAX_LAG_QUARTERS,
    BinOp,
    Call,
    FormulaError,
    FormulaErrors,
    Lag,
    Neg,
    Node,
    Num,
    Ref,
)

MAX_COMPANIES = 5
MAX_INPUTS = 20  # distinct (company, metric, span, lag) series

BOTH, QUARTER, TTM = ("quarter", "ttm"), ("quarter",), ("ttm",)
# metric → (unit, views with values): silver.metrics (003, 012) and gold.derived_metrics (014).
# Non-additive durations (EPS, weighted shares) have no TTM.
CATALOG: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": ("USD", BOTH), "cost_of_revenue": ("USD", BOTH), "gross_profit": ("USD", BOTH),
    "research_and_development": ("USD", BOTH), "sga": ("USD", BOTH),
    "operating_income": ("USD", BOTH), "interest_expense": ("USD", BOTH),
    "pretax_income": ("USD", BOTH), "income_tax": ("USD", BOTH), "net_income": ("USD", BOTH),
    "eps_basic": ("USD/shares", QUARTER), "eps_diluted": ("USD/shares", QUARTER),
    "weighted_shares_basic": ("shares", QUARTER), "weighted_shares_diluted": ("shares", QUARTER),
    "cash": ("USD", BOTH), "short_term_investments": ("USD", BOTH),
    "receivables": ("USD", BOTH), "inventory": ("USD", BOTH), "current_assets": ("USD", BOTH),
    "total_assets": ("USD", BOTH), "current_liabilities": ("USD", BOTH),
    "long_term_debt_current": ("USD", BOTH), "long_term_debt": ("USD", BOTH),
    "total_liabilities": ("USD", BOTH), "stockholders_equity": ("USD", BOTH),
    "shares_outstanding": ("shares", BOTH),
    "operating_cash_flow": ("USD", BOTH), "capex": ("USD", BOTH),
    "depreciation_amortization": ("USD", BOTH), "stock_based_compensation": ("USD", BOTH),
    "dividends_paid": ("USD", BOTH), "share_buybacks": ("USD", BOTH),
    # derived
    "gross_margin": ("ratio", BOTH), "operating_margin": ("ratio", BOTH),
    "net_margin": ("ratio", BOTH), "free_cash_flow": ("USD", BOTH),
    "fcf_margin": ("ratio", BOTH), "revenue_growth": ("ratio", BOTH),
    "net_income_growth": ("ratio", BOTH), "eps_growth": ("ratio", QUARTER),
    "payout_ratio": ("ratio", BOTH), "debt_to_equity": ("ratio", BOTH),
    "net_debt": ("USD", BOTH), "current_ratio": ("ratio", BOTH), "roe": ("ratio", TTM),
}
UNITS = {"ratio": (0, 0), "USD": (1, 0), "shares": (0, 1), "USD/shares": (1, -1)}
RATIO = UNITS["ratio"]
PERCENT, MULTIPLE, NUMBER = "percent", "multiple", "number"
# ratio metrics that don't read as a percent; the tables show them the same way
DISPLAY = {"debt_to_equity": MULTIPLE, "current_ratio": MULTIPLE}
# balances (a point in time): the rest of the catalog's amounts are flows over a period
BALANCES = frozenset({
    "cash", "short_term_investments", "receivables", "inventory", "current_assets",
    "total_assets", "current_liabilities", "long_term_debt_current", "long_term_debt",
    "total_liabilities", "stockholders_equity", "net_debt", "shares_outstanding"})
# everything that describes one date and so has no trailing twelve months: the balances, and the
# ratios of two balances
POINT_IN_TIME = BALANCES | {"debt_to_equity", "current_ratio"}
# gold.derived_metrics (014): these live in gold.derived_financials, every other metric's quarters
# in gold.quarterly_financials
DERIVED = frozenset({
    "gross_margin", "operating_margin", "net_margin", "free_cash_flow", "fcf_margin",
    "revenue_growth", "net_income_growth", "eps_growth", "payout_ratio", "debt_to_equity",
    "net_debt", "current_ratio", "roe"})
_NAMES = {v: k for k, v in UNITS.items()}
_LABELS = {"ratio": "a ratio", "USD": "dollars", "shares": "shares",
           "USD/shares": "dollars per share"}


@dataclass(frozen=True)
class Input:
    """One series the formula reads. ticker None = the default company."""
    ticker: str | None
    metric: str
    span: str     # quarter | ttm
    lag: int      # quarters back

    @property
    def ref(self) -> str:
        name = f"{self.ticker}.{self.metric}" if self.ticker else self.metric
        span = f"ttm({name})" if self.span == "ttm" else name
        return f"{span}[-{self.lag}q]" if self.lag else span


@dataclass
class Analysis:
    unit: str
    inputs: list[Input]
    tickers: dict[str, tuple[int, int]] = field(default_factory=dict)  # first (pos, length)
    bare: tuple[int, int] | None = None  # first metric without a ticker
    input_of: dict[int, int] = field(default_factory=dict)  # id(Ref) → index into inputs
    display: str | None = None  # percent | multiple | number; None unless unit is ratio


def unit_name(u: tuple[int, int]) -> str:
    if u in _NAMES:
        return _NAMES[u]
    usd, shares = u
    num = [f"{n}^{e}" if e > 1 else n for n, e in (("USD", usd), ("shares", shares)) if e > 0]
    den = [f"{n}^{-e}" if e < -1 else n for n, e in (("USD", usd), ("shares", shares)) if e < 0]
    return "*".join(num or ["1"]) + ("/" + "*".join(den) if den else "")


def label(u: tuple[int, int]) -> str:
    name = unit_name(u)
    return _LABELS.get(name, name)


def suggest(word: str, options: list[str]) -> str | None:
    """Closest option within an edit distance of max(2, len/3)."""
    best, score = None, 99
    for o in sorted(options):
        s = _levenshtein(word, o)
        if s < score:
            best, score = o, s
    return best if score <= max(2, len(word) // 3) else None


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class _Value:
    """What a subtree works out to."""
    unit: tuple[int, int]
    const: bool = False
    number: Decimal | None = None  # a constant's value, when it could be worked out
    shape: str | None = None       # how a dimensionless value reads (the module docstring)
    scaled: bool = False           # multiplied or divided by a constant somewhere inside


def _refs(n: Node) -> list[Ref]:
    """The metric references under a node, in source order."""
    match n:
        case Ref():
            return [n]
        case Lag(expr=e) | Call(arg=e) | Neg(arg=e):
            return _refs(e)
        case BinOp(left=left, right=right):
            return _refs(left) + _refs(right)
    return []


def _unknown(ast: Node) -> list[FormulaError]:
    """One problem for each distinct name that isn't a metric, so all of them show at once."""
    seen: dict[str, FormulaError] = {}
    for n in _refs(ast):
        if n.metric not in CATALOG and n.metric not in seen:
            s = suggest(n.metric, list(CATALOG))
            seen[n.metric] = FormulaError(
                f"Unknown metric '{n.metric}'", n.metric_pos, n.pos + n.length - n.metric_pos,
                f"Did you mean {s}?" if s else "Start typing to see the metric names",
                code="unknown_metric")
    return list(seen.values())


def _quotient_shape(left: Node, right: Node) -> str:
    """x / y where both are in the same unit, other than a ratio."""
    above, below = _refs(left), _refs(right)
    if len({r.ticker for r in above + below}) > 1:
        return MULTIPLE
    tops, bottoms = {r.metric for r in above}, {r.metric for r in below}
    if tops == bottoms:
        return MULTIPLE
    if bottoms in ({"revenue"}, {"total_assets"}):
        return PERCENT
    return PERCENT if bottoms <= BALANCES and not tops <= BALANCES else MULTIPLE


def _product_shape(a: str | None, b: str | None) -> str:
    if a == b and a is not None:
        return a
    return PERCENT if {a, b} == {PERCENT, MULTIPLE} else NUMBER


def _sum_shape(left: _Value, right: _Value) -> str | None:
    if left.const or right.const:
        value, constant = (right, left) if left.const else (left, right)
        if value.shape == MULTIPLE and constant.number == 1:
            return PERCENT
        return value.shape
    return left.shape if left.shape == right.shape else NUMBER


def _arith(op: str, a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Constants are worked out only to catch a zero divisor; one too large is simply unknown."""
    if a is None or b is None:
        return None
    try:
        return a + b if op == "+" else a - b if op == "-" else a * b if op == "*" else a / b
    except ArithmeticError:
        return None


def _combine(n: BinOp, left: _Value, right: _Value) -> _Value:
    op = n.op
    if op in "+-":
        if not (left.const or right.const or left.unit == right.unit):
            verb = "add" if op == "+" else "subtract"
            raise FormulaError(f"Can't {verb} {label(left.unit)} and {label(right.unit)}",
                               n.op_pos, 1, "Both sides need the same unit", code="unit")
        unit = right.unit if left.const else left.unit
    else:
        if op == "/" and right.const and right.number == 0:
            raise FormulaError("Can't divide by zero", n.op_pos,
                               n.right.pos + n.right.length - n.op_pos,
                               "Divide by a metric, or a number other than 0", code="zero")
        sign = 1 if op == "*" else -1
        unit = (left.unit[0] + sign * right.unit[0], left.unit[1] + sign * right.unit[1])
    if left.const and right.const:
        return _Value(unit, True, _arith(op, left.number, right.number))
    scaled = left.scaled or right.scaled or (op in "*/" and (
        (right.const and right.number != 1) or (left.const and left.number != 1)))
    shape = None
    if unit == RATIO:
        if op in "+-":
            shape = _sum_shape(left, right)
        elif right.const:  # x * 365, x / 100; x * 1 and x / 1 change nothing
            shape = left.shape if right.number == 1 else NUMBER
        elif left.const:  # 1 / x, 365 * x
            shape = right.shape if left.number == 1 and op == "*" else NUMBER
        elif left.unit == right.unit and left.unit != RATIO:
            shape = _quotient_shape(n.left, n.right) if op == "/" and not scaled else NUMBER
        elif op == "*":
            shape = _product_shape(left.shape, right.shape)
        else:
            shape = MULTIPLE if left.shape == right.shape == PERCENT else NUMBER
    return _Value(unit, shape=shape, scaled=scaled)


def analyze(ast: Node, view: str = "quarter") -> Analysis:
    """Unit, display, inputs and tickers of a parsed formula; FormulaError on a problem: every
    unknown metric at once (FormulaErrors), otherwise the first one found."""
    unknown = _unknown(ast)
    if len(unknown) == 1:
        raise unknown[0]
    if unknown:
        raise FormulaErrors(unknown)
    a = Analysis(unit="ratio", inputs=[])
    index: dict[Input, int] = {}

    def walk(n: Node, span: str, lag: int) -> _Value:
        match n:
            case Num(value=v):
                return _Value(RATIO, True, v)
            case Ref(metric=metric, ticker=ticker):
                unit, spans = CATALOG[metric]
                if span not in spans:
                    what = "trailing-twelve-month" if span == "ttm" else "quarterly"
                    raise FormulaError(f"{metric} has no {what} values", n.pos, n.length,
                                       "Use it without ttm()" if span == "ttm" else
                                       "Wrap it in ttm()", code="span")
                if ticker:
                    a.tickers.setdefault(ticker, (n.pos, n.ticker_length))
                elif a.bare is None:
                    a.bare = (n.pos, n.length)
                if len(a.tickers) + (a.bare is not None) > MAX_COMPANIES:
                    raise FormulaError(f"A formula can use at most {MAX_COMPANIES} companies",
                                       n.pos, n.length, code="limit")
                inp = Input(ticker, metric, span, lag)
                if inp not in index:
                    if len(index) >= MAX_INPUTS:
                        raise FormulaError(f"A formula can read at most {MAX_INPUTS} series",
                                           n.pos, n.length, code="limit")
                    index[inp] = len(a.inputs)
                    a.inputs.append(inp)
                a.input_of[id(n)] = index[inp]
                shape = DISPLAY.get(metric, PERCENT) if unit == "ratio" else None
                return _Value(UNITS[unit], shape=shape)
            case Lag(expr=e, quarters=q):
                if lag + q > MAX_LAG_QUARTERS:
                    raise FormulaError(f"Lags add up to more than {MAX_LAG_QUARTERS} quarters",
                                       n.pos, n.length, code="limit")
                v = walk(e, span, lag + q)
                if v.const:
                    raise FormulaError("Lags apply to a metric, e.g. revenue[-4q]", n.pos,
                                       n.length)
                return v
            case Call(name="ttm", arg=arg):
                return walk(arg, "ttm", lag)
            case Call(arg=arg):
                v = walk(arg, span, lag)
                return v if v.number is None else _Value(v.unit, True, abs(v.number))
            case Neg(arg=arg):
                v = walk(arg, span, lag)
                return v if v.number is None else _Value(v.unit, True, -v.number)
            case BinOp(left=left, right=right):
                return _combine(n, walk(left, span, lag), walk(right, span, lag))
        raise TypeError(n)

    value = walk(ast, view, 0)
    if value.const:
        raise FormulaError("Use at least one metric, e.g. net_income / revenue", ast.pos,
                           ast.length, code="no_metric")
    a.unit = unit_name(value.unit)
    a.display = (value.shape or NUMBER) if value.unit == RATIO else None
    return a
