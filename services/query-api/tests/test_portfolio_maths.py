"""The portfolio's maths (query_api/portfolio_maths.py): average cost, realised gain on a sale,
oversold sales refused, values only with a price, staleness and weights."""

from datetime import date
from decimal import Decimal as D

import pytest

from query_api.portfolio_maths import (
    STALE_AFTER_DAYS,
    Oversold,
    money,
    number,
    position,
    ratio,
    stale,
    valued,
    weights,
)


def lot(shares, cost, day=None, made=0):
    return {"shares": D(shares), "cost_total": D(cost), "bought_on": day, "created_at": made}


def sell(shares, price, day, made=0):
    return {"shares": D(shares), "price": D(price), "sold_on": day, "created_at": made}


def test_two_buys_average_their_cost():
    p = position([lot(10, 1000, date(2026, 1, 5)), lot(10, 2000, date(2026, 3, 2))], [])
    assert (p.shares, p.cost, p.realised) == (D(20), D(3000), D(0))
    assert p.first_bought == date(2026, 1, 5) and p.open
    assert number(p.cost / p.shares) == "150"


def test_a_sale_takes_its_share_of_the_average_cost_and_realises_the_rest():
    p = position([lot(10, 1000, date(2026, 1, 5)), lot(10, 2000, date(2026, 3, 2))],
                 [sell(5, 300, date(2026, 6, 1))])
    # 5 of 20 shares carry a quarter of $3,000; sold for $1,500
    assert (p.shares, p.cost, p.realised) == (D(15), D(2250), D(750))
    assert money(p.realised) == "750.00"


def test_a_sale_before_a_later_buy_uses_the_cost_held_then():
    p = position([lot(10, 1000, date(2026, 1, 5)), lot(10, 3000, date(2026, 6, 2))],
                 [sell(5, 50, date(2026, 3, 1))])
    # sold at a loss against the first buy's $100 a share, before the second buy
    assert p.realised == D(-250)
    assert (p.shares, p.cost) == (D(15), D(3500))


def test_selling_everything_closes_the_holding_with_nothing_left():
    p = position([lot(D("0.333333"), 100, date(2026, 1, 5))],
                 [sell(D("0.333333"), 330, date(2026, 2, 5))])
    assert not p.open and p.shares == 0 and p.cost == 0
    assert money(p.realised) == "10.00"


def test_selling_more_than_held_on_the_day_is_refused():
    with pytest.raises(Oversold) as e:
        position([lot(10, 1000, date(2026, 1, 5))], [sell(11, 120, date(2026, 2, 1))])
    assert "2026-02-01" in str(e.value) and "(10)" in str(e.value)
    # shares bought later don't cover an earlier sale
    with pytest.raises(Oversold):
        position([lot(10, 1000, date(2026, 5, 5))], [sell(1, 120, date(2026, 2, 1))])


def test_a_lot_without_a_day_counts_as_the_earliest():
    # a broker's positions export has no buy dates: such a lot covers any sale
    p = position([lot(4, 400)], [sell(4, 150, date(2020, 1, 2))])
    assert p.realised == D(200) and not p.open and p.first_bought is None


def test_on_one_day_buys_come_before_sells():
    day = date(2026, 4, 1)
    p = position([lot(2, 200, day, made=2)], [sell(2, 110, day, made=1)])
    assert p.realised == D(20)


def test_values_only_with_a_price():
    p = position([lot(15, 2250, date(2026, 1, 5))], [])
    assert valued(p, None) == {"value": None, "unrealised": None, "unrealised_pct": None}
    v = valued(p, D(160))
    assert (v["value"], v["unrealised"]) == (D(2400), D(150))
    assert ratio(v["unrealised_pct"]) == "0.0667"
    # a closed holding has no value however recent the price
    closed = position([lot(1, 10, date(2026, 1, 5))], [sell(1, 12, date(2026, 1, 6))])
    assert valued(closed, D(13))["value"] is None
    # a free position (cost 0) has no percentage
    assert valued(position([lot(1, 0, date(2026, 1, 5))], []), D(5))["unrealised_pct"] is None


def test_a_price_older_than_seven_days_is_stale():
    today = date(2026, 11, 16)
    assert STALE_AFTER_DAYS == 7
    assert not stale(date(2026, 11, 9), today)
    assert stale(date(2026, 11, 8), today)


def test_weights_by_value_when_every_holding_has_one_else_by_cost():
    basis, w = weights([("a", D(100), D(300)), ("b", D(100), D(100))])
    assert basis == "value" and (w["a"], w["b"]) == (D("0.75"), D("0.25"))
    basis, w = weights([("a", D(100), D(300)), ("b", D(300), None)])
    assert basis == "cost" and (w["a"], w["b"]) == (D("0.25"), D("0.75"))
    assert weights([]) == ("cost", {})
    assert weights([("a", D(0), None)]) == ("cost", {"a": None})


def test_money_is_cents_and_numbers_keep_what_was_given():
    assert money(D("1.005")) == "1.00" and money(D("1.015")) == "1.02"  # half to even
    assert money(None) is None and number(None) is None
    assert number(D("1.2500")) == "1.25" and number(D("1E+3")) == "1000"
