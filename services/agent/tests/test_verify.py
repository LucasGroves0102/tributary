import json

import pytest

from tributary_agent.verify import figures_in, verify

NVDA = "0001045810"
Q2, Q1 = "0001045810-26-000075", "0001045810-26-000052"


def financials(*rows):
    return json.dumps({"company_id": NVDA, "rows": [
        {"metric": m, "period_end": p, "value": v, "citation": {"accession_no": a}}
        for m, p, v, a in rows]})


REVENUE = financials(("revenue", "2026-07-26", 96221000000, Q2),
                     ("revenue", "2026-04-26", 81615000000, Q1),
                     ("eps_diluted", "2026-07-26", 1.05, Q2),
                     ("net_income", "2026-07-26", -1234000000, Q2))
CITED = {Q2, Q1}


def statuses(answer, results=(REVENUE,), cited=CITED):
    v = verify(answer, list(results), cited)
    return {f.text: f.status for f in v.figures}, v


@pytest.mark.parametrize("written", ["$96.2B", "$96.22 billion", "$96.221B", "$96,221 million",
                                     "$96,221,000,000", "96.2 billion"])
def test_reported_values_at_any_precision(written):
    figs, _ = statuses(f"Revenue was {written}.")
    assert figs == {written: "verified"}


def test_wrong_rounding_is_unverified():
    assert statuses("Revenue was $96.3B.")[0] == {"$96.3B": "unverified"}


def test_differences_and_sums_are_derived():
    figs, _ = statuses("Up $14.6B to $96.2B; together $177.8B.")
    assert figs == {"$14.6B": "derived", "$96.2B": "verified", "$177.8B": "derived"}


def test_eps_and_negative_values():
    assert statuses("EPS $1.05; a net loss of ($1.23B).")[0] == {
        "$1.05": "verified", "$1.23B": "verified"}


def test_figures_quoted_from_filing_text():
    section = json.dumps({"sections": [{"text": "purchase obligations of $50.3 billion"}]})
    assert statuses("Obligations were $50.3B.", [section], set())[0] == {"$50.3B": "quoted"}


def test_years_percentages_and_counts_are_not_figures():
    assert figures_in("In 2026 revenue rose 17.9% across 4 segments and 12,000 employees.") == []


def test_accession_numbers_must_come_from_tools():
    _, v = statuses(f"See {Q2} and 0000320193-26-000020. Freshness: filed 2026-08-26.")
    assert v.unverified_accessions == ["0000320193-26-000020"] and not v.ok


def test_answers_with_data_need_freshness():
    assert not statuses(f"Revenue was $96.2B ({Q2}).")[1].ok
    assert statuses(f"Revenue was $96.2B ({Q2}). Freshness: filed 2026-08-26.")[1].ok


def test_denials_without_data_pass():
    v = verify("You don't have access to Tesla's financials.", [], set())
    assert v.ok and not v.used_data and v.figures == []


def test_comparisons_across_companies_are_derived():
    msft = json.dumps({"company_id": "0000789019", "rows": [
        {"metric": "net_income", "period_end": "2026-06-30", "value": 35766000000}]})
    nvda = financials(("net_income", "2026-07-26", 59688000000, Q2))
    assert statuses("Nvidia earned $23.9B more.", [msft, nvda])[0] == {"$23.9B": "derived"}


def test_table_cells_in_millions_are_quoted():
    section = json.dumps({"sections": [{"text": "(In millions)\nRevenue\n$\n82,886\n$\n70,066"}]})
    figs, _ = statuses("Revenue rose to $82.9 billion from $70.1B; not $71.2B.", [section], set())
    assert figs == {"$82.9 billion": "quoted", "$70.1B": "quoted", "$71.2B": "unverified"}
