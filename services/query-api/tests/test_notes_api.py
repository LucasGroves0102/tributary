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
    # TSLA (filings only) never reaches the store; no derived-quarters notes on Home
    assert c.store.calls[0] == ("data_notes", "bob", (NVDA,), False, 2)  # type: ignore[attr-defined]


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
