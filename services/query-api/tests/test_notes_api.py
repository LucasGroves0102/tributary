"""Data notes routes with fake layers: financials tier checked first, companies only from
readable(), and the note shapes (kinds, filings, newest first)."""

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.notes import accessions_of, note_date, shape

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer

AAPL = "0000320193"
GRANTS = {"bob": {NVDA: {"financials", "filings"}, TSLA: {"filings"}},
          "alice": {AAPL: {"financials"}}}
CHECKED = datetime(2026, 10, 20, 6, tzinfo=UTC)
ORIG, RESTATED, Q4 = "0001045810-25-000023", "0001045810-26-000021", "0001045810-26-000075"


class Store:
    def __init__(self):
        self.calls = []

    def data_notes(self, user_id, company_ids, derived, limit):
        self.calls.append(("data_notes", user_id, tuple(company_ids), derived, limit))
        findings = [
            {"company_id": c, "check_name": "restatement", "severity": "info",
             "metric": "revenue", "period_end": date(2025, 4, 27),
             "detail": {"original_accession": ORIG, "original_filed": "2025-05-28",
                        "restated_accession": RESTATED, "restated_filed": "2026-02-25",
                        "change": 0.0123},
             "checked_at": CHECKED} for c in company_ids
        ] + [{"company_id": company_ids[0], "check_name": "series_gap", "severity": "warning",
              "metric": "capex", "period_end": date(2019, 7, 28),
              "detail": {"missing_from": "2019-01-28", "missing_to": "2019-04-28"},
              "checked_at": CHECKED}]
        derived_rows = [{"company_id": company_ids[0], "metric": "revenue", "quarters": 9,
                         "first_period_end": date(2017, 1, 29),
                         "latest_period_end": date(2026, 1, 25), "latest_accession": Q4}]
        behind = [{"company_id": company_ids[0], "facts_filed": date(2026, 5, 28),
                   "accession_no": "0001045810-26-000099", "form": "10-Q",
                   "filing_date": date(2026, 8, 27), "cik": "0001045810"}]
        return {"findings": findings, "derived": derived_rows if derived else [],
                "behind": behind, "checked_at": CHECKED}

    def filing_refs(self, user_id, accessions):
        self.calls.append(("filing_refs", user_id, tuple(accessions)))
        return {a: {"form": "10-K", "filed": date(2026, 2, 25), "cik": "0001045810"}
                for a in accessions}


def client(grants=GRANTS, down=False):
    store = Store()
    auth = FakeAuthorizer(grants, down)
    c = TestClient(create_app(auth, store, SECRET))
    c.store, c.authz = store, auth  # type: ignore[attr-defined]
    return c


def test_company_notes_need_financials_and_check_first(token):
    c = client()
    r = c.get(f"/v1/companies/{TSLA}/data-notes", headers=token("bob"))  # filings only
    assert r.status_code == 403
    assert c.store.calls == []  # type: ignore[attr-defined]
    assert c.authz.checks == [("bob", "financials", TSLA)]  # type: ignore[attr-defined]


def test_unknown_and_ungranted_company_look_the_same(token):
    c = client()
    a = c.get("/v1/companies/0000000001/data-notes", headers=token("alice"))
    b = c.get(f"/v1/companies/{NVDA}/data-notes", headers=token("alice"))
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    assert a.status_code == 403


def test_company_notes_shapes(token):
    c = client()
    r = c.get(f"/v1/companies/{NVDA}/data-notes", headers=token("bob"))
    assert r.status_code == 200
    body = r.json()
    assert body["checked_at"].startswith("2026-10-20T06:00:00")
    kinds = [n["kind"] for n in body["notes"]]
    # newest first: the missing 10-Q (Aug 2026), the restatement (filed Feb 2026), derived
    # quarters (latest period Jan 2026), the 2019 gap
    assert kinds == ["companyfacts_behind", "restatement", "derived_quarters", "series_gap"]
    behind, restated, derived, gap = body["notes"]
    assert behind["severity"] == "warning"
    assert behind["detail"] == {"missing_filings": 1, "missing_filed": "2026-08-27",
                                "facts_latest_filed": "2026-05-28"}
    assert behind["filings"][0]["filing_url"].endswith("/1045810/000104581026000099/")
    assert [(f["role"], f["accession_no"]) for f in restated["filings"]] == [
        ("original", ORIG), ("restated", RESTATED)]
    assert restated["filings"][0]["form"] == "10-K"
    assert derived["detail"]["quarters"] == 9
    assert derived["filings"][0]["accession_no"] == Q4
    assert gap["filings"] == []
    assert ("data_notes", "bob", (NVDA,), True, 1000) in c.store.calls  # type: ignore[attr-defined]


def test_recent_notes_cover_readable_financials_only(token):
    c = client()
    r = c.get("/v1/data-notes?limit=2", headers=token("bob"))
    assert r.status_code == 200
    body = r.json()
    assert body["companies"] == 1
    assert [n["kind"] for n in body["notes"]] == ["companyfacts_behind", "restatement"]
    # TSLA (filings only) never reaches the store; no derived-quarters notes on Home. It asks for
    # more findings than it shows, since a split's read as one note
    assert c.store.calls[0] == ("data_notes", "bob", (NVDA,), False, 200)  # type: ignore[attr-defined]


def test_recent_notes_of_named_companies_only_those_the_caller_can_read(token):
    c = client()
    r = c.get(f"/v1/data-notes?company_id={NVDA}&company_id={TSLA}&company_id={AAPL}",
              headers=token("bob"))
    assert r.status_code == 200 and r.json()["companies"] == 1
    assert c.store.calls[0][2] == (NVDA,)  # type: ignore[attr-defined]
    assert {n["company_id"] for n in r.json()["notes"]} == {NVDA}
    # a watchlist of nothing readable asks for nothing
    c = client()
    r = c.get(f"/v1/data-notes?company_id={TSLA}", headers=token("bob"))
    assert r.json()["notes"] == [] and c.store.calls == []  # type: ignore[attr-defined]


def test_recent_notes_refuse_a_long_or_malformed_list(token):
    c = client()
    many = "&".join(f"company_id={i:010d}" for i in range(51))
    assert c.get(f"/v1/data-notes?{many}", headers=token("bob")).status_code == 422
    assert c.get("/v1/data-notes?company_id=NVDA", headers=token("bob")).status_code == 422


def test_recent_notes_without_companies_skip_the_store(token):
    c = client()
    r = c.get("/v1/data-notes", headers=token("carol"))
    assert r.json()["notes"] == [] and r.json()["companies"] == 0
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_notes_fail_closed(token):
    c = client(down=True)
    assert c.get(f"/v1/companies/{NVDA}/data-notes", headers=token("bob")).status_code == 503
    assert c.get("/v1/data-notes", headers=token("bob")).status_code == 503
    assert c.get("/v1/data-notes").status_code == 401


def test_accessions_and_dates_from_detail():
    n = {"detail": {"accession_no": "a", "annual_accession": "b",
                    "quarter_accessions": ["c", "d"], "rule": "negative"},
         "period_end": date(2026, 1, 1)}
    assert accessions_of(n) == [("source", "a"), ("annual", "b"), ("quarter", "c"),
                                ("quarter", "d")]
    assert note_date(n) == date(2026, 1, 1)
    assert note_date({"detail": {"restated_filed": "2026-02-25"}}) == date(2026, 2, 25)
    assert note_date({"detail": {}, "period_end": None}) is None


def test_shape_orders_errors_before_warnings_on_the_same_date():
    same = date(2026, 3, 31)
    rows = [{"company_id": NVDA, "check_name": c, "severity": s, "metric": "revenue",
             "period_end": same, "detail": {}} for c, s in
            (("outlier", "warning"), ("impossible_value", "error"))]
    assert [n["kind"] for n in shape(rows, [], [], {})] == ["impossible_value", "outlier"]


def restated(metric, end, change, filing="0001045810-24-000264", filed="2024-08-28"):
    return {"company_id": NVDA, "check_name": "restatement", "severity": "info", "metric": metric,
            "period_end": end, "checked_at": CHECKED,
            "detail": {"original_accession": ORIG, "original_filed": "2023-08-28",
                       "restated_accession": filing, "restated_filed": filed, "change": change}}


# NVIDIA's 10-for-1, as ops.check_financials found it (real figures)
TEN_FOR_ONE = [
    restated("eps_basic", date(2023, 7, 30), -0.9000),
    restated("eps_diluted", date(2023, 7, 30), -0.8992),
    restated("weighted_shares_basic", date(2023, 7, 30), 8.9996),
    restated("weighted_shares_diluted", date(2023, 7, 30), 9.0016),
    restated("eps_diluted", date(2023, 10, 29), -0.9003, "0001045810-24-000316", "2024-11-20"),
    restated("weighted_shares_diluted", date(2023, 10, 29), 9.0,
             "0001045810-24-000316", "2024-11-20"),
    restated("shares_outstanding", date(2024, 1, 28), 9.0012, "0001045810-25-000023", "2025-02-26"),
]


def only(notes, kind):
    return [n for n in notes if n["kind"] == kind]


def test_a_stock_split_reads_as_one_note_and_counts_what_it_covers():
    notes = shape(TEN_FOR_ONE, [], [], {})
    assert [n["kind"] for n in notes] == ["stock_split"]
    split = notes[0]
    assert split["severity"] == "info"
    assert split["detail"]["split"] == "10-for-1" and split["detail"]["reverse"] is False
    assert split["detail"]["findings"] == 7 and split["detail"]["periods"] == 3
    assert split["detail"]["first_period_end"] == "2023-07-30"
    assert split["detail"]["last_period_end"] == "2024-01-28"
    assert {f["role"] for f in split["filings"]} == {"restated"}
    assert len(split["filings"]) == 3
    assert split["date"] == date(2025, 2, 26)


def test_two_splits_of_one_company_are_two_notes():
    filing = ("0001045810-21-000131", "2021-08-20")
    four = [restated("eps_diluted", date(2020, 7, 26), -0.7475, *filing),
            restated("weighted_shares_diluted", date(2020, 7, 26), 3.0, *filing)]
    notes = shape([*four, *TEN_FOR_ONE], [], [], {})
    assert sorted(n["detail"]["split"] for n in notes) == ["10-for-1", "4-for-1"]


def test_real_restatements_beside_a_split_are_not_folded_into_it():
    real = [restated("net_income", date(2023, 7, 30), 0.0612),
            restated("eps_diluted", date(2016, 5, 1), 0.0606, "0001045810-18-000010", "2018-02-28")]
    notes = shape([*TEN_FOR_ONE, *real], [], [], {})
    assert len(only(notes, "stock_split")) == 1
    assert sorted(n["metric"] for n in only(notes, "restatement")) == ["eps_diluted", "net_income"]
    assert all("may_be_split" not in n["detail"] for n in only(notes, "restatement"))


def test_a_reverse_split_on_a_loss_is_a_split_too():
    # Guerrilla RF's 1-for-6: shares x 1/6, a loss per share six times the size
    guer = [restated("weighted_shares_basic", date(2022, 9, 30), -0.8333),
            restated("eps_basic", date(2022, 9, 30), -5.0)]
    notes = shape(guer, [], [], {})
    assert [n["kind"] for n in notes] == ["stock_split"]
    assert notes[0]["detail"]["split"] == "1-for-6" and notes[0]["detail"]["reverse"] is True


def test_one_side_alone_stays_a_restatement_that_may_be_a_split():
    eps = shape([restated("eps_diluted", date(2023, 7, 30), -0.9)], [], [], {})[0]
    assert eps["kind"] == "restatement" and eps["detail"]["may_be_split"] == "10-for-1"
    shares = shape([restated("shares_outstanding", date(2023, 7, 30), 5.0)], [], [], {})[0]
    assert shares["kind"] == "restatement" and shares["detail"]["may_be_split"] == "6-for-1"
    # a change that isn't a clean ratio says nothing of the kind
    plain = shape([restated("eps_diluted", date(2023, 7, 30), 0.0606)], [], [], {})[0]
    assert "may_be_split" not in plain["detail"]


def test_shares_and_eps_that_dont_match_arent_a_split():
    mismatch = [restated("weighted_shares_diluted", date(2023, 7, 30), 9.0),
                restated("eps_diluted", date(2023, 7, 30), -0.2)]
    assert [n["kind"] for n in shape(mismatch, [], [], {})] == ["restatement", "restatement"]


def test_a_factor_of_a_thousand_is_a_unit_change_and_not_a_split():
    # NVIDIA's weighted shares, filed in thousands then in ones
    notes = shape([restated("weighted_shares_basic", date(2011, 7, 31), 999.0),
                   restated("weighted_shares_basic", date(2009, 7, 26), -0.999)], [], [], {})
    assert all(n["kind"] == "restatement" and n["detail"]["unit_change"] for n in notes)


def spike(end, x=12.1):
    return {"company_id": NVDA, "check_name": "outlier", "severity": "warning",
            "metric": "operating_cash_flow", "period_end": end,
            "detail": {"vs_neighbours": x, "accession_no": Q4}, "checked_at": CHECKED}


# Deckers' operating cash flow, $M: December every year
DECK = [(date(2023, 3, 31), 59.5), (date(2023, 6, 30), 125.3), (date(2023, 9, 30), -3.7),
        (date(2023, 12, 31), 940.3), (date(2024, 3, 31), -28.7), (date(2024, 6, 30), 112.7),
        (date(2024, 9, 30), -90.6), (date(2024, 12, 31), 1095.4), (date(2025, 3, 31), -73.0),
        (date(2025, 6, 30), 36.1), (date(2025, 9, 30), 8.1), (date(2025, 12, 31), 1041.9)]


def test_a_spike_the_same_quarter_has_every_year_is_seasonal():
    n = shape([spike(date(2024, 12, 31))], [], [], {},
              {(NVDA, "operating_cash_flow"): DECK})[0]
    assert n["kind"] == "outlier" and n["detail"]["seasonal"] is True and n["severity"] == "info"


def test_a_spike_only_this_quarter_has_stays_a_warning():
    # JPMorgan's cash, $B: Dec 2015 and nothing like it in other Decembers
    jpm = [(date(2014, 9, 30), 25.37), (date(2014, 12, 31), 27.83), (date(2015, 3, 31), 22.82),
           (date(2015, 6, 30), 24.1), (date(2015, 9, 30), 21.26), (date(2015, 12, 31), 361.68),
           (date(2016, 3, 31), 18.21), (date(2016, 6, 30), 19.71), (date(2016, 9, 30), 21.39),
           (date(2016, 12, 31), 25.0), (date(2017, 3, 31), 22.0)]
    n = shape([spike(date(2015, 12, 31), 17.0)], [], [], {},
              {(NVDA, "operating_cash_flow"): jpm})[0]
    assert n["severity"] == "warning" and "seasonal" not in n["detail"]
    # no series at all: nothing to say it's seasonal
    assert "seasonal" not in shape([spike(date(2015, 12, 31))], [], [], {})[0]["detail"]


def test_one_other_year_is_a_coincidence_not_a_season():
    # a buyback in two Decembers out of four
    lumpy = [(date(2022, 9, 30), 1.0), (date(2022, 12, 31), 1.0), (date(2023, 3, 31), 1.0),
             (date(2023, 9, 30), 1.0), (date(2023, 12, 31), 12.0), (date(2024, 3, 31), 1.0),
             (date(2024, 9, 30), 1.0), (date(2024, 12, 31), 15.0), (date(2025, 3, 31), 1.0),
             (date(2025, 9, 30), 1.0), (date(2025, 12, 31), 1.0), (date(2026, 3, 31), 1.0)]
    n = shape([spike(date(2024, 12, 31))], [], [], {}, {(NVDA, "operating_cash_flow"): lumpy})[0]
    assert "seasonal" not in n["detail"] and n["severity"] == "warning"


def covered(notes):
    return sum(n["detail"]["findings"] if n["kind"] == "stock_split"
               else n["detail"].get("repeats", 1) for n in notes)


def test_every_finding_still_reaches_a_note():
    findings = [*TEN_FOR_ONE, restated("net_income", date(2023, 7, 30), 0.06),
                spike(date(2024, 12, 31)), *VERTIV_SEGMENTS]
    notes = shape(findings, [], [], {})
    assert covered(notes) == len(findings)


# --- M2: ranked, grouped, summarised -----------------------------------------------------------

NOW = datetime(2026, 11, 9, tzinfo=UTC)
# Vertiv's product/service revenue members, double counted every quarter since 2021 (24 findings)
VERTIV_SEGMENTS = [
    {"company_id": NVDA, "check_name": "segment_sum_product_service", "severity": "warning",
     "metric": "revenue", "period_end": date(2021 + i // 4, 3 * (i % 4) + 1, 28),
     "detail": {"axis": "product_service", "members": 6, "deviation": 1.0, "accession_no": Q4},
     "checked_at": CHECKED} for i in range(24)]


def finding(kind, metric, end, severity="warning", **detail):
    return {"company_id": NVDA, "check_name": kind, "severity": severity, "metric": metric,
            "period_end": end, "detail": detail, "checked_at": CHECKED}


def test_a_series_repeats_are_one_note_with_its_periods():
    (n,) = shape(VERTIV_SEGMENTS, [], [], {}, now=NOW)
    assert n["kind"] == "segment_sum_product_service"
    assert n["detail"]["repeats"] == 24
    assert n["detail"]["first_period_end"] == "2021-01-28"
    assert n["detail"]["last_period_end"] == "2026-10-28"
    assert len(n["detail"]["periods"]) == 24 and n["period_end"] == date(2026, 10, 28)
    assert n["filings"] == [{"role": "source", "accession_no": Q4, "form": None, "filed": None,
                             "filing_url": None}]


def test_different_rules_or_metrics_stay_apart():
    notes = shape([finding("impossible_value", "revenue", date(2026, 3, 31), "error",
                           rule="negative"),
                   finding("impossible_value", "revenue", date(2026, 6, 30), "error",
                           rule="gross_profit > revenue"),
                   finding("impossible_value", "cash", date(2026, 6, 30), "error",
                           rule="negative")], [], [], {}, now=NOW)
    assert len(notes) == 3 and all("repeats" not in n["detail"] for n in notes)


def test_what_matters_comes_first_whatever_its_date():
    notes = shape([
        restated("revenue", date(2026, 6, 30), 0.003, filed="2026-10-30"),       # small: minor
        finding("impossible_value", "revenue", date(2025, 3, 31), "error", rule="negative"),
        finding("outlier", "net_income", date(2026, 3, 31), vs_neighbours=14.0),  # attention
        finding("impossible_value", "gross_profit", date(2012, 3, 31), "error",
                rule="gross_profit > revenue"),
        finding("sign_flip", "stock_based_compensation", date(2026, 6, 30), "info",
                rule="benefit"),                                                  # 050: minor
        finding("series_gap", "revenue", date(2025, 9, 30)),                      # note
    ], [{"company_id": NVDA, "metric": "revenue", "quarters": 9,
         "first_period_end": date(2017, 1, 29), "latest_period_end": date(2026, 9, 30),
         "latest_accession": Q4}], [], {}, now=NOW)
    got = [(n["rank"], n["kind"], n["metric"], str(n["period_end"])) for n in notes]
    assert got == [
        ("attention", "outlier", "net_income", "2026-03-31"),
        ("attention", "impossible_value", "revenue", "2025-03-31"),
        ("note", "series_gap", "revenue", "2025-09-30"),
        ("note", "impossible_value", "gross_profit", "2012-03-31"),  # old, still a note
        ("minor", "restatement", "revenue", "2026-06-30"),  # dated by its filing, Oct 30
        ("minor", "derived_quarters", "revenue", "2026-09-30"),
        ("minor", "sign_flip", "stock_based_compensation", "2026-06-30"),
    ]


def test_missing_filings_always_need_attention_and_segment_sums_never_do():
    notes = shape([finding("segment_sum_geography", "revenue", date(2026, 6, 30), "warning",
                           axis="geography", deviation=0.4)], [], [
        {"company_id": NVDA, "facts_filed": date(2026, 5, 28), "accession_no": Q4, "form": "10-Q",
         "filing_date": date(2018, 8, 27), "cik": NVDA}], {}, now=NOW)
    assert [(n["kind"], n["rank"]) for n in notes] == [
        ("companyfacts_behind", "attention"), ("segment_sum_geography", "note")]


def test_a_group_takes_its_most_important_members_rank(token):
    old = [finding("outlier", "revenue", date(2012 + i, 3, 31), vs_neighbours=11.0)
           for i in range(3)]
    (n,) = shape([*old, finding("outlier", "revenue", date(2026, 3, 31), vs_neighbours=11.0)],
                 [], [], {}, now=NOW)
    assert n["rank"] == "attention" and n["detail"]["repeats"] == 4


def test_both_routes_summarise_notes_by_rank(token):
    c = client()
    body = c.get(f"/v1/companies/{NVDA}/data-notes", headers=token("bob")).json()
    assert body["summary"] == {"attention": 1, "note": 0, "minor": 3, "findings": 2}
    recent = c.get("/v1/data-notes", headers=token("bob")).json()
    assert recent["summary"]["findings"] == 2 and recent["notes"][0]["rank"] == "attention"


def test_a_mistagged_fact_is_dated_by_its_filing_not_its_period():
    """Legacy Housing's income tax at 2033-03-31, in a 10-Q filed 2024-05-09: not the newest note
    until 2033."""
    from datetime import date

    from query_api.notes import note_date
    note = {"period_end": date(2033, 3, 31), "detail": {"filed": "2024-05-09"}}
    assert note_date(note) == date(2024, 5, 9)
