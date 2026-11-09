"""query_api.changes (What changed, read for materiality) over real filings: Deckers' and
Vertiv's 10-Qs of Oct 2026 against the same quarter a year earlier and the prior 10-Q
(fixtures/changes: both section texts from production and the rows gold.section_changes holds
for them), and the rules one by one."""

import gzip
import json
from functools import cache
from pathlib import Path

import pytest

from query_api.changes import (
    Furniture,
    calendar,
    figures,
    inline_heading,
    matches_topic,
    moved,
    new_figures,
    normalize,
    periods_in,
    quiet_calendar,
    read_section,
    roles,
    topic_terms,
    units,
)

FIXTURES = Path(__file__).parent / "fixtures" / "changes"


@cache
def fixture(name: str) -> dict:
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


def reading(name: str):
    f = fixture(name)
    return read_section(f["section_key"], f["form"], f["text"], f["prior_text"], f["stored"])


def text_of(c) -> str:
    return (c.new or c.old).text


# --- real filings ----------------------------------------------------------------------------

def test_deckers_year_ago_is_fewer_changes_with_the_noise_counted():
    """Tester 2: 'too noisy'. The stored diff has 145 changed MD&A paragraphs against the
    same quarter a year earlier; split paragraphs, figure-only table rows and minor edits are
    hidden and counted, the rest shown."""
    f, r = fixture("deck-2026-10-29-mdna-year-ago"), reading("deck-2026-10-29-mdna-year-ago")
    assert len(f["stored"]) == 145
    assert len(r.changes) < 100
    assert r.hidden["resplit"] > 0 and r.hidden["figures_only"] > 0
    # no change is a page header or a fragment of a sentence split by the page
    starts = ("Table of Contents", "Statements,")
    assert not any(t.startswith(starts) for t in map(text_of, r.changes))


def test_deckers_tariff_refunds_rank_high_and_say_their_period():
    r = reading("deck-2026-10-29-mdna-year-ago")
    ranked = sorted(r.changes, key=lambda c: -c.score)
    refund = next(c for c in r.changes if "$46,000 of IEEPA tariff net refunds" in text_of(c))
    assert refund.kind == "new" and refund.period == "three and six months"
    assert "$46,000" in refund.figures_new and "tariff" in refund.risk
    assert ranked.index(refund) < 5
    assert refund.why[:2] == ["new passage", "5 new figures"]


def test_deckers_same_heading_pairs_quarter_with_quarter_and_half_with_half():
    """'Gross Profit.' is rewritten past fuzzy recognition each year; its run-in heading and
    its period pair it, three months with three months, six with six (H4: Q1's three-month
    text was diffed against six-month text)."""
    r = reading("deck-2026-10-29-mdna-year-ago")
    gross = [c for c in r.changes if text_of(c).startswith("Gross Profit.")]
    assert sorted((c.kind, c.period) for c in gross) == [
        ("rewritten", "six months"), ("rewritten", "three months")]
    for c in gross:
        assert c.old is not None and c.old.period == c.period


def test_deckers_against_the_prior_10q_never_pairs_a_quarter_with_the_half():
    r = reading("deck-2026-10-29-mdna-prior")
    for c in r.changes:
        if c.kind == "rewritten" and c.new.period and c.old.period:
            assert roles(c.new.period) & roles(c.old.period), (c.new.period, c.old.period)
    # the six-month text is new in Q2 and says so
    halves = [c for c in r.changes if c.kind == "new" and c.period == "six months"]
    assert len(halves) >= 10


def test_vertiv_second_to_third_quarter_is_a_calendar_rollover():
    """Tester 1: 'the 10-Q diff is noisy ("second" to "third" quarter)'."""
    f, r = fixture("vrt-2026-10-28-mdna-prior"), reading("vrt-2026-10-28-mdna-prior")
    assert len(f["stored"]) == 39 and len(r.changes) <= 25
    assert r.hidden["calendar"] >= 3
    shown = " | ".join(map(text_of, r.changes))
    assert "Comparison of the Nine Months Ended September 30, 2026" not in shown
    assert "(Dollars in millions) Three months ended September 30, 2026" not in shown
    for c in r.changes:  # what's left changed more than its calendar
        if c.kind == "rewritten":
            assert calendar(normalize(c.new.text)) != calendar(normalize(c.old.text))


def test_vertiv_new_covenant_risk_factors_rank_above_results():
    r = reading("vrt-2026-10-28-risk-factors-year-ago")
    assert [c.kind for c in r.changes] == ["new"] * 5
    top = max(r.changes, key=lambda c: c.score)
    assert top.subsection == "Risk factors" and {"covenant", "breach"} <= set(top.risk)


def test_topic_and_new_figure_filters_on_deckers():
    r = reading("deck-2026-10-29-mdna-year-ago")
    terms = topic_terms("tariffs")
    hits = [c for c in r.changes
            if matches_topic(terms, c.new and c.new.text, c.old and c.old.text)]
    assert 5 <= len(hits) <= 15
    assert all("tariff" in (text_of(c) + (c.old.text if c.old else "")).lower() for c in hits)
    with_figures = [c for c in r.changes if c.figures_new]
    assert 0 < len(with_figures) < len(r.changes)


# --- the rules ---------------------------------------------------------------------------------

def test_furniture_masks_page_numbers_and_running_headers():
    text = "\n".join(["Table of Contents 24", "Body text of the page.", "Deckers | Q2 | 25",
                      "Deckers | Q2 | 26", "Deckers | Q2 | 27", "(1)", "Liquidity"])
    f = Furniture(text)
    assert f("Table of Contents 25") and f("Deckers | Q2 | 99") and f("(1)") and f("  ")
    assert not f("Liquidity") and not f("Body text of the page.")


def test_a_paragraph_split_by_a_page_break_is_one_unit():
    text = ("HOKA Brand. The brand offers enhanced cushioning, coupled with strategic presence\n"
            "Table of Contents 25\n"
            "have fueled both domestic and international sales growth.\n"
            "UGG Brand. Another paragraph that ends.\n"
            "Statements continue on a new line\n"
            "Overview\n"
            "A paragraph after a heading line.")

    def spans(line_no: int) -> list[int]:
        start = sum(len(x) + 1 for x in text.split("\n")[:line_no])
        return [start, start + len(text.split("\n")[line_no])]
    # diff.py glued the page header to the continuation; the first half ends mid-sentence
    paragraphs = [(0, spans(0)), (1, spans(1) + spans(2)), (2, spans(3)), (3, spans(4)),
                  (4, spans(6))]
    us, dropped = units("new", text, paragraphs, Furniture(text))
    assert [u.text for u in us] == [
        "HOKA Brand. The brand offers enhanced cushioning, coupled with strategic presence "
        "have fueled both domestic and international sales growth.",
        "UGG Brand. Another paragraph that ends.",  # a sentence ends, the next starts upper
        "Statements continue on a new line",
        "A paragraph after a heading line."]  # "Overview" between: a heading, not furniture
    assert dropped == 0 and us[0].parts == 2


def test_a_table_row_between_keeps_paragraphs_apart():
    text = ("(Dollars in millions) Three months ended September 30, 2026 $ Change % Change\n"
            "Net sales $ 2,988.8 $ 1,712.4 $ 1,276.4 74.5 %\n"
            "Americas net sales were $2,988.8 in the third quarter.")
    lines = text.split("\n")
    paragraphs = [(0, [0, len(lines[0])]),
                  (1, [len(lines[0]) + len(lines[1]) + 2, len(text)])]
    us, _ = units("new", text, paragraphs, Furniture(text))
    assert len(us) == 2


def test_calendar_masks_months_quarters_and_the_year_to_date():
    a = calendar(normalize("Net sales were $3,274.3 in the second quarter of 2026, for the six "
                           "months ended June 30, 2026, on May 22, 2026"))
    b = calendar(normalize("Net sales were $4,312.8 in the third quarter of 2026, for the nine "
                           "months ended September 30, 2026, on May 22, 2026"))
    assert a == b
    # but not the quarter against the year to date, nor "may" the verb
    assert calendar(normalize("for the three months ended")) != calendar(
        normalize("for the six months ended"))
    assert "may" in calendar(normalize("results may vary"))


@pytest.mark.parametrize("text, expected", [
    ("During the three and six months ended September 30, 2026, we recorded",
     ["three months", "six months"]),
    ("Net sales were $544.7 in the third quarter of 2026", ["three months"]),
    ("in the first nine months of 2026 compared to", ["nine months"]),
    ("For the 26 weeks ended September 27, 2026", ["six months"]),
    ("phased out during the prior fiscal year.", []),
    ("For the fiscal year ended March 31, 2026", ["year"]),
])
def test_periods_a_passage_names(text, expected):
    assert periods_in(text) == expected


def test_figures_and_how_far_they_moved():
    assert figures("increased 4.8% to $740,359, 280 basis points, 22,500 units, in 2026, Note 7"
                   ) == ["4.8%", "$740,359", "280 basis points", "22,500"]
    added, removed = new_figures("rose 4.8% to $740,359 and 1.5%", "fell 0.2% to $706,785 and 1.5%")
    assert (added, removed) == (["4.8%", "$740,359"], ["0.2%", "$706,785"])
    m = moved([["=", "sales"], ["-", "$706,785."], ["+", "$740,359."], ["=", "and"],
               ["-", "0.2%"], ["+", "4.8%"]])
    # $706,785 → $740,359 moved 4.5%, under MOVED_MIN: the percentage is the figure that moved
    assert m == {"from": "0.2%", "to": "4.8%", "change": 0.958}
    m = moved([["=", "sales"], ["-", "$3,274.3 and $7.2"], ["+", "$4,312.8 and $0.3"],
               ["=", "or"], ["-", "24.1%"], ["+", "61.2%"]])
    assert m == {"from": "$3,274.3", "to": "$4,312.8", "change": 0.241}  # not $7.2 → $0.3


def test_topic_terms_and_matching():
    assert topic_terms('tariffs "supply chain" liabilities x') == [
        "supply chain", "tariff", "liabilit"]
    assert matches_topic(topic_terms("tariffs"), "IEEPA Tariff refunds")
    assert not matches_topic(topic_terms("tariffs"), "rate cards")  # a word prefix, not inside
    assert matches_topic(topic_terms('"net refunds" tariff'), "tariff  net\nrefunds", None)


def test_inline_headings():
    assert inline_heading("Gross Profit. Gross margin increased") == "gross profit"
    assert inline_heading("Total Other Income, Net. The decrease") == "total other income, net"
    assert inline_heading("We are a global leader. We sell") is None


def test_calendar_swaps_inside_a_rewritten_passage_are_not_marked():
    runs = [["=", "Net sales were"], ["-", "$3,274.3"], ["+", "$4,312.8"], ["=", "in the"],
            ["-", "second"], ["+", "third"], ["=", "quarter of 2026, compared with"],
            ["-", "June 30, 2025."], ["+", "September 30, 2026."], ["-", "Strong."],
            ["+", "Weak."]]
    assert quiet_calendar(runs) == [
        ["=", "Net sales were"], ["-", "$3,274.3"], ["+", "$4,312.8"],
        ["=", "in the third quarter of 2026, compared with September 30, 2026."],
        ["-", "Strong."], ["+", "Weak."]]
    # figures are never calendar words; a run that mixes them stays marked
    assert quiet_calendar([["-", "second $5.0"], ["+", "third $6.0"]]) == [
        ["-", "second $5.0"], ["+", "third $6.0"]]
