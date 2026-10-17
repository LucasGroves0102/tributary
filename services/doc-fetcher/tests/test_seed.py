import json
from datetime import date
from pathlib import Path

from doc_fetcher.seed import select_filings

SUBMISSIONS = json.loads((Path(__file__).parent / "fixtures" / "nvda-submissions.json").read_text())
NVDA = "0001045810"


def test_selects_recent_10k_10q_and_8k():
    events = select_filings(NVDA, SUBMISSIONS, today=date(2026, 10, 17))
    by_form: dict[str, list[str]] = {}
    for e in events:
        by_form.setdefault(e["form_type"], []).append(e["accession_no"])
    assert by_form["10-K"] == ["0001045810-26-000021", "0001045810-25-000023"]
    assert by_form["10-Q"] == ["0001045810-26-000075", "0001045810-26-000052",
                               "0001045810-25-000230", "0001045810-25-000209"]
    assert len(by_form["8-K"]) == 9  # filed on or after 2026-04-20
    assert set(by_form) == {"10-K", "10-Q", "8-K"}


def test_events_match_filings_raw_schema():
    e = select_filings(NVDA, SUBMISSIONS, today=date(2026, 10, 17))[0]
    assert set(e) == {"accession_no", "cik", "company_name", "role", "form_type",
                      "filed_at", "url"}
    assert e["url"] == ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/"
                        "0001045810-26-000078-index.htm")


def test_old_filings_of_a_retired_cik_are_skipped():
    # e.g. Google Inc.'s last 10-Ks are from 2015-2016
    events = select_filings(NVDA, SUBMISSIONS, today=date(2030, 1, 1))
    assert [e for e in events if e["form_type"] in ("10-K", "10-Q")] == []
