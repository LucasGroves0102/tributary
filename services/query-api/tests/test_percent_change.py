"""shapes.percent_change: the one rule for a period-on-period percentage change."""

from decimal import Decimal as D

import pytest

from query_api.shapes import (
    PCT_FROM_NEGATIVE,
    PCT_FROM_ZERO,
    PCT_TO_NEGATIVE,
    percent_change,
)


def test_a_positive_base_gives_the_change_over_it():
    assert percent_change(D("46743000000"), D("44062000000"), "USD") == (D("0.060846"), None)
    assert percent_change(D("90"), D("100"), "USD", D("0.0001")) == (D("-0.1000"), None)


def test_a_fall_to_zero_is_a_real_minus_100_percent():
    assert percent_change(D("0"), D("100"), "USD") == (D("-1.000000"), None)


@pytest.mark.parametrize(("current", "earlier", "why"), [
    # JPMorgan's operating cash flow (analyst test 4): +231% QoQ, +173% YoY by |earlier| before
    ("33160000000", "-25280000000", PCT_FROM_NEGATIVE),
    ("33160000000", "-45620000000", PCT_FROM_NEGATIVE),
    ("-10", "-20", PCT_FROM_NEGATIVE),  # a smaller loss isn't "-50%" either
    ("5", "0", PCT_FROM_ZERO),
    ("-50", "100", PCT_TO_NEGATIVE),  # -150%: a profit turned loss, as 035 reads net income growth
])
def test_a_base_that_makes_it_mislead_gives_none_and_the_reason(current, earlier, why):
    assert percent_change(D(current), D(earlier), "USD") == (None, why)


def test_ratios_change_in_points_with_no_reason():
    assert percent_change(D("0.72"), D("0.75"), "ratio") == (None, None)
    assert percent_change(D("0.12"), D("-0.05"), "pure") == (None, None)


def test_a_huge_change_from_a_small_dollar_base_says_why():
    """Riley 45: Pro-Dex's cash from $33K to $11.76M read +35,533.3%. 035's revenue growth bound,
    for any dollar amount; another currency's base isn't dollars (057)."""
    from query_api.shapes import PCT_SMALL_BASE, PCT_TINY_BASE
    assert percent_change(D("11760000"), D("33000"), "USD") == (None, PCT_SMALL_BASE)
    # another currency's base isn't dollars: the tiny-base bound (155) still applies
    assert percent_change(D("11760000"), D("33000"), "JPY") == (None, PCT_TINY_BASE)
    assert percent_change(D("11760000"), D("33000"), "shares") == (None, PCT_TINY_BASE)


def test_growth_off_a_base_under_a_fifth_of_the_figure_says_why():
    """Morgan, test 11, finding 18 (Avery's review): Kennametal's operating income, $37.559M in
    the quarter to Sep 30, 2025 and $379.251M a year later (its 10-Q: tungsten pricing), read
    +909.7%. Over 400% (the earlier figure under a fifth of this one) reads n/m; 400% exactly is a
    figure."""
    from query_api.shapes import PCT_TINY_BASE
    assert percent_change(D("379251000"), D("37559000"), "USD") == (None, PCT_TINY_BASE)
    assert percent_change(D("90000000"), D("9000000"), "USD") == (None, PCT_TINY_BASE)  # 900%
    assert percent_change(D("50000000"), D("10000000"), "USD") == (D("4.000000"), None)
    assert percent_change(D("768193000"), D("497974000"), "USD") == (D("0.542637"), None)
