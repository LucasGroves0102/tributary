"""query_api.point_in_time: the day's bounds, the restatement kinds (the notes' one split rule),
and `original` on the right rows. Pure: rows shaped as gold.restatements and /financials give them;
the database side is tests/golden/test_point_in_time*.py."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from query_api import point_in_time as pit

CO = "0001075531"
K1, K2 = "0001075531-25-000010", "0001075531-26-000020"


def change(metric, end, original, restated, *, span="quarter", same=True, accession=K2,
           filed=date(2026, 4, 28)):
    return {"span": span, "metric": metric, "period_start": None, "period_end": end,
            "unit": "USD", "original": Decimal(original), "original_tag": "us-gaap:X",
            "original_accession": K1, "original_form": "10-Q", "original_filed": date(2025, 4, 29),
            "original_known_from": date(2025, 4, 29), "restated": Decimal(restated),
            "restated_tag": "us-gaap:X" if same else "us-gaap:Y", "restated_accession": accession,
            "restated_form": "10-Q", "restated_filed": filed, "restated_known_from": filed,
            "same_line": same}


def test_a_day_from_2009_to_today():
    today = date(2026, 11, 16)
    assert pit.check(None, today) is None
    assert pit.check(date(2009, 1, 1), today) == date(2009, 1, 1)
    assert pit.check(today, today) == today
    for bad in (date(2008, 12, 31), date(2026, 11, 17)):
        with pytest.raises(HTTPException) as e:
            pit.check(bad, today)
        assert e.value.status_code == 422


def test_a_split_another_line_and_the_same_line():
    end = date(2025, 3, 31)
    rows = [change("weighted_shares_diluted", end, "33093000", "827000000"),  # 25-for-1
            change("eps_diluted", end, "10.07", "0.40"),
            change("revenue", end, "4757000000", "4800000000", same=False),
            change("net_income", end, "-92282000", "-88252000")]
    assert pit.kinds(CO, rows) == ["split", "split", "another_line", "same_line"]
    # shares alone at a clean ratio, no EPS beside them: the notes' rule doesn't call it a split
    assert pit.kinds(CO, rows[:1]) == ["same_line"]
    shaped = pit.restatement_rows(CO, rows)
    assert shaped[0]["original"]["value"] == Decimal("33093000")
    assert shaped[0]["restated"]["accession_no"] == K2 and shaped[0]["kind"] == "split"


def fin(metric, end, value, source="reported", tag="us-gaap:X"):
    return {"metric": metric, "period_end": end, "value": Decimal(value), "value_source": source,
            "source_tag": tag, "accession_no": K2, "filed": date(2026, 4, 28)}


def test_original_on_the_rows_a_later_filing_changed_and_on_their_own_span():
    end = date(2025, 3, 31)
    changes = [change("revenue", end, "1000", "1100"),
               change("revenue", end, "4000", "4100", span="ttm"),
               change("cash", end, "50", "55")]
    quarter = pit.originals(CO, "quarter", [fin("revenue", end, "1100"), fin("cash", end, "55")],
                            changes)
    assert quarter[("revenue", end)]["value"] == Decimal("1000")
    assert quarter[("revenue", end)]["kind"] == "same_line"
    # a TTM view: the sum's own change, and a balance's quarter row
    ttm = pit.originals(CO, "ttm", [fin("revenue", end, "4100", source="ttm"),
                                    fin("cash", end, "55")], changes)
    assert ttm[("revenue", end)]["value"] == Decimal("4000")
    assert ttm[("cash", end)]["value"] == Decimal("50")
    # back where it started, or never changed: no original
    assert pit.originals(CO, "quarter", [fin("revenue", end, "1000")], changes) == {}
    assert pit.originals(CO, "quarter", [fin("sga", end, "7")], changes) == {}
