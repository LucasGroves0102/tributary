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
