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


# --- inputs, stated scales and decimals (#22, #23, M1) -------------------------------------
# NVIDIA's Q2 FY2027 rows as /financials returns them: derived metrics carry their inputs.
NVDA_DERIVED = json.dumps({"company_id": NVDA, "rows": [
    {"metric": "net_debt", "period_end": "2026-07-26", "value": 10923000000,
     "inputs": {"cash": 22443000000, "long_term_debt": 32366000000,
                "long_term_debt_current": 1000000000},
     "citation": {"accession_no": Q2}},
    {"metric": "revenue_growth", "period_end": "2026-07-26", "value": 1.0585,
     "inputs": {"revenue": 96221000000, "revenue_year_ago": 46743000000},
     "citation": {"accession_no": Q2}},
    {"metric": "net_income_growth", "period_end": "2026-07-26", "value": 1.259,
     "inputs": {"net_income": 59688000000, "net_income_year_ago": 26422000000},
     "citation": {"accession_no": Q2}},
    {"metric": "revenue", "period_end": "2026-07-26", "value": 302969000000,
     "inputs": {"quarters": [
         {"value": 57006000000, "period_end": "2025-10-26",
          "accession_no": "0001045810-25-000230"},
         {"value": 68127000000, "period_end": "2026-01-25",
          "accession_no": "0001045810-26-000021"},
         {"value": 81615000000, "period_end": "2026-04-26", "accession_no": Q1},
         {"value": 96221000000, "period_end": "2026-07-26", "accession_no": Q2}]},
     "citation": {"accession_no": Q2}},
]})


def test_a_derived_metrics_inputs_are_in_the_data():
    """The thesis check's "$32.37B and $1.00B, both on the balance sheet" (#23) and the
    assistant's year-ago "$46.74B and $26.42B" (#22) were in the tool results all along, as
    net debt's and the growth rates' inputs."""
    figs, v = statuses("Debt of $32.37B and $1.00B less cash of $22.44B; revenue $96.22B vs "
                       "$46.74B, net income vs $26.42B; Q3 FY2026 revenue $57.01B.",
                       [NVDA_DERIVED])
    assert figs == {"$32.37B": "verified", "$1.00B": "verified", "$22.44B": "verified",
                    "$96.22B": "verified", "$46.74B": "verified", "$26.42B": "verified",
                    "$57.01B": "verified"}
    by = {f.text: f.source for f in v.figures}
    assert by["$1.00B"] == {"company_id": NVDA, "metric": "long_term_debt_current",
                            "period_end": "2026-07-26", "accession_no": Q2}
    # a year-ago value's period isn't in the row, so the source doesn't claim one
    assert by["$46.74B"] == {"company_id": NVDA, "metric": "revenue_year_ago",
                             "period_end": None, "accession_no": None}


def test_inputs_combine_like_values_and_wrong_figures_still_fail():
    """A sum or difference is of one metric, as for rows: revenue less its year-ago value is
    derived; long-term debt plus its current part (two metrics) is not."""
    figs, _ = statuses("Up $49.48B on a year ago; debt of $33.37B; not $32.38B, $46.75B nor "
                       "$10.92T.", [NVDA_DERIVED])
    assert figs == {"$49.48B": "derived", "$33.37B": "unverified", "$32.38B": "unverified",
                    "$46.75B": "unverified", "$10.92T": "unverified"}


def test_tables_read_at_the_scale_they_state():
    thousands = json.dumps({"sections": [{"text": "(In thousands, except per share data)\n"
                                                  "Net sales $ 1,425,123 $ 46,743"}]})
    figs, _ = statuses("Sales were $1.43B; $46.74M, not $46.74B.", [thousands], set())
    assert figs == {"$1.43B": "quoted", "$46.74M": "quoted", "$46.74B": "unverified"}


def test_decimal_cells_need_a_stated_scale():
    stated = json.dumps({"sections": [{"text": "($ in millions)\nBacklog 1,234.5"}]})
    unstated = json.dumps({"sections": [{"text": "Backlog 1,234.5 units"}]})
    assert statuses("Backlog was $1.23B.", [stated], set())[0] == {"$1.23B": "quoted"}
    assert statuses("Backlog was $1.23B.", [unstated], set())[0] == {"$1.23B": "unverified"}
