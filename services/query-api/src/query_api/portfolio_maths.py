"""A holding's figures from its lots, sells and the user's own price: the one copy of the
portfolio's maths (query_api/portfolio.py reads it; the web only words the answer). Pure, Decimal
throughout, stores nothing.

Rules (README "Portfolio"; test_portfolio_maths.py):

- Cost basis is a running average cost. Lots and sells are taken in date order (a lot without a
  day, as a broker's positions export has none, counts as the earliest; on one day buys come before
  sells, then the order they were entered). A sale takes out its shares' share of the cost held at
  that moment: cost_out = cost x sold / held, and realised gain = sold x price - cost_out. Brokers
  report tax lots (first in, first out by default): their realised figure can differ, and the
  holding says which method this is (`METHOD`).
- A sale of more shares than are held on its day is refused (`Oversold`): every write that adds or
  removes a lot or a sell re-runs this before it commits.
- Market value and unrealised gain exist only with a price the user gave (their own market-data
  connection's latest close, or the last price they typed or imported); never a price Tributary
  fetched for itself or another user's. A price older than STALE_AFTER_DAYS is `stale`.
- Weights: shares of the open holdings' total market value when every open holding has a price,
  else of their total cost basis (`weight_basis` says which). A not-covered position (an ETF or a
  fund) has no price, so a portfolio holding one is weighted by cost. Each weight is rounded to
  WEIGHT_STEP (a tenth of a percent, what the app shows) by largest remainder, so they add up to
  exactly 100% (Morgan 32: 52.55% and 47.45% read 52.5% and 47.4%).
- A sale's price is the user's own price of its day (query_api/portfolio.py records it as typed
  unless a newer one is held): it is what they said a share fetched.
- Money is rounded to cents only on output (ROUND_HALF_EVEN); shares and prices are kept as given.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

STALE_AFTER_DAYS = 7
METHOD = "average_cost"
CENT = Decimal("0.01")
WEIGHT_STEP = Decimal("0.001")  # a weight's precision: a tenth of a percent
ZERO = Decimal(0)
# a fraction of a share left after a sale (a broker's rounding) still reads as closed
DUST = Decimal("0.000001")


class Oversold(ValueError):
    """A sale of more shares than were held on its day."""

    def __init__(self, sold_on: date, held: Decimal) -> None:
        super().__init__(f"that sells more shares than you held on {sold_on.isoformat()} "
                         f"({_plain(held)})")
        self.sold_on, self.held = sold_on, held


@dataclass(frozen=True)
class Position:
    shares: Decimal
    cost: Decimal          # the open shares' cost basis
    realised: Decimal
    first_bought: date | None
    bought: Decimal        # every share ever bought, every cost ever paid
    paid: Decimal

    @property
    def open(self) -> bool:
        return self.shares > DUST


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def money(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_EVEN))


def number(d: Decimal | None) -> str | None:
    """Shares and prices as given (no exponent, no trailing zeros)."""
    return None if d is None else _plain(d)


def position(lots: Sequence[dict[str, Any]], sells: Sequence[dict[str, Any]]) -> Position:
    """Lots ({shares, cost_total, bought_on, created_at}) and sells ({shares, price, sold_on,
    created_at}) → the open position and the realised gain (module docstring). Raises
    Oversold."""
    events: list[tuple[date, int, Any, str, dict]] = []
    for lot in lots:
        events.append((lot.get("bought_on") or date.min, 0, lot.get("created_at") or 0, "buy",
                       lot))
    for sell in sells:
        events.append((sell["sold_on"], 1, sell.get("created_at") or 0, "sell", sell))
    events.sort(key=lambda e: (e[0], e[1], str(e[2])))
    shares = cost = realised = bought = paid = ZERO
    for day, _, _, kind, row in events:
        n = Decimal(row["shares"])
        if kind == "buy":
            c = Decimal(row["cost_total"])
            shares, cost, bought, paid = shares + n, cost + c, bought + n, paid + c
            continue
        if n > shares + DUST:
            raise Oversold(day, shares)
        out = cost * n / shares if shares else ZERO
        realised += n * Decimal(row["price"]) - out
        shares, cost = shares - n, cost - out
        if shares <= DUST:  # a sale of everything leaves nothing, not a rounding crumb
            shares = cost = ZERO
    days = [lot["bought_on"] for lot in lots if lot.get("bought_on")]
    return Position(shares, cost, realised, min(days) if days else None, bought, paid)


def stale(price_on: date, today: date) -> bool:
    return (today - price_on).days > STALE_AFTER_DAYS


def valued(p: Position, price: Decimal | None) -> dict[str, Decimal | None]:
    """value, unrealised and unrealised_pct (a fraction) at `price`; None without one."""
    if price is None or not p.open:
        return {"value": None, "unrealised": None, "unrealised_pct": None}
    value = p.shares * price
    gain = value - p.cost
    return {"value": value, "unrealised": gain,
            "unrealised_pct": gain / p.cost if p.cost > 0 else None}


def weights(values: Iterable[tuple[str, Decimal, Decimal | None]]
            ) -> tuple[str, dict[str, Decimal | None]]:
    """(key, cost basis, market value or None) of the open holdings → (basis, key → weight): by
    value when every one has a value, else by cost (module docstring)."""
    rows = list(values)
    if not rows:
        return "cost", {}
    by_value = all(v is not None for _, _, v in rows)
    total = sum(((v if by_value else c) or ZERO for _, c, v in rows), ZERO)
    if total <= 0:
        return ("value" if by_value else "cost"), {k: None for k, _, _ in rows}
    raw = {k: ((v if by_value else c) or ZERO) / total for k, c, v in rows}
    return ("value" if by_value else "cost"), largest_remainder(raw)


def largest_remainder(shares: dict[str, Decimal]) -> dict[str, Decimal]:
    """Shares that add up to 1, each rounded to WEIGHT_STEP: every one rounded down, then a step
    more to the largest remainders (ties: the larger share, then the key) until they do."""
    down = {k: (s / WEIGHT_STEP).to_integral_value(rounding=ROUND_FLOOR) * WEIGHT_STEP
            for k, s in shares.items()}
    left = int(((1 - sum(down.values(), ZERO)) / WEIGHT_STEP).to_integral_value())
    order = sorted(shares, key=lambda k: (-(shares[k] - down[k]), -shares[k], k))
    for k in order[:max(0, left)]:
        down[k] += WEIGHT_STEP
    return down


def ratio(d: Decimal | None) -> str | None:
    """A fraction to four decimals (0.1234 = 12.34%)."""
    return None if d is None else str(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN))
