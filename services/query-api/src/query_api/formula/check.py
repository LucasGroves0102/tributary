"""Semantic checks: metric names, spans, units and limits. Pure: no database or OpenFGA.

Units are exponents of USD and shares: (0, 0) ratio, (1, 0) USD, (0, 1) shares,
(1, -1) USD/shares. + and - need equal units, except that a constant takes the other side's
unit; * and / add and subtract exponents.
"""

from dataclasses import dataclass, field

from query_api.formula.grammar import (
    MAX_LAG_QUARTERS,
    BinOp,
    Call,
    FormulaError,
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


def analyze(ast: Node, view: str = "quarter") -> Analysis:
    """Unit, inputs and tickers of a parsed formula; FormulaError on the first problem."""
    a = Analysis(unit="ratio", inputs=[])
    index: dict[Input, int] = {}

    def walk(n: Node, span: str, lag: int) -> tuple[tuple[int, int], bool]:
        """(unit, is a constant)."""
        match n:
            case Num():
                return UNITS["ratio"], True
            case Ref(metric=metric, ticker=ticker):
                if metric not in CATALOG:
                    s = suggest(metric, list(CATALOG))
                    raise FormulaError(f"Unknown metric '{metric}'", n.metric_pos,
                                       n.pos + n.length - n.metric_pos,
                                       f"Did you mean {s}?" if s else "GET /v1/metrics lists "
                                       "the metrics", code="unknown_metric")
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
                return UNITS[unit], False
            case Lag(expr=e, quarters=q):
                if lag + q > MAX_LAG_QUARTERS:
                    raise FormulaError(f"Lags add up to more than {MAX_LAG_QUARTERS} quarters",
                                       n.pos, n.length, code="limit")
                u, const = walk(e, span, lag + q)
                if const:
                    raise FormulaError("Lags apply to a metric, e.g. revenue[-4q]", n.pos,
                                       n.length)
                return u, False
            case Call(name="ttm", arg=arg):
                return walk(arg, "ttm", lag)
            case Call(arg=arg) | Neg(arg=arg):
                return walk(arg, span, lag)
            case BinOp(op=op, left=left, right=right):
                ul, cl = walk(left, span, lag)
                ur, cr = walk(right, span, lag)
                if op in "+-":
                    if cl or cr or ul == ur:
                        return (ur if cl else ul), cl and cr
                    verb = "add" if op == "+" else "subtract"
                    raise FormulaError(f"Can't {verb} {label(ul)} and {label(ur)}", n.op_pos, 1,
                                       "Both sides need the same unit", code="unit")
                sign = 1 if op == "*" else -1
                return (ul[0] + sign * ur[0], ul[1] + sign * ur[1]), cl and cr
        raise TypeError(n)

    unit, const = walk(ast, view, 0)
    if const:
        raise FormulaError("Use at least one metric, e.g. net_income / revenue", ast.pos,
                           ast.length, code="no_metric")
    a.unit = unit_name(unit)
    return a
