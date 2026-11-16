"""A tag disagreement's two figures in its data note (migration 075, owner decision 2026-11-16):
read at read time, as the user, after require(financials); the finding itself holds none (014).
Zura Bio's shape (072's header): cash and restricted cash 205,134 beside cash 205,134,000 in one
filing. The DB tests need TEST_DATABASE_URL and roll back."""

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.notes import shape

from .conftest import SECRET
from .test_api import NVDA, FakeAuthorizer

ACC = "0001829126-26-000011"
END = date(2026, 3, 31)
CHECKED = datetime(2026, 11, 16, 6, tzinfo=UTC)
DETAIL = {"rule": "scale", "tag": "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
          "other_tag": "CashAndCashEquivalentsAtCarryingValue", "factor": 1000.0,
          "accession_no": ACC, "form": "10-Q", "filed": "2026-05-12", "period_start": None}
FINDING = {"company_id": NVDA, "check_name": "tag_disagreement", "severity": "warning",
           "metric": "cash_and_restricted_cash", "period_end": END, "detail": DETAIL,
           "checked_at": CHECKED}
HELD = {(NVDA, "cash_and_restricted_cash", END, ACC): {
    DETAIL["tag"]: (Decimal("205134"), "USD"), DETAIL["other_tag"]: (Decimal("205134000"), "USD")}}


def test_the_note_carries_both_figures_gold_tag_first():
    (n,) = shape([FINDING], [], [], {}, figures=HELD)
    assert n["detail"]["figures"] == [
        {"tag": DETAIL["tag"], "value": Decimal("205134"), "unit": "USD"},
        {"tag": DETAIL["other_tag"], "value": Decimal("205134000"), "unit": "USD"}]
    assert "corrected" not in n["detail"]
    assert FINDING["detail"] == DETAIL  # the finding as stored is never changed


def test_a_figure_silver_no_longer_holds_says_corrected():
    held = {k: {DETAIL["tag"]: v[DETAIL["tag"]]} for k, v in HELD.items()}
    (n,) = shape([FINDING], [], [], {}, figures=held)
    assert n["detail"]["corrected"] is True and "figures" not in n["detail"]
    (n,) = shape([FINDING], [], [], {}, figures={})
    assert n["detail"]["corrected"] is True


def test_without_figures_read_the_note_is_as_before():
    (n,) = shape([FINDING], [], [], {})
    assert "figures" not in n["detail"] and "corrected" not in n["detail"]


class Store:
    def __init__(self):
        self.calls = []

    def data_notes(self, user_id, company_ids, derived, limit):
        self.calls.append("data_notes")
        return {"findings": [dict(FINDING, company_id=c) for c in company_ids], "derived": [],
                "behind": [], "checked_at": CHECKED}

    def filing_refs(self, user_id, accessions):
        return {}

    def disagreeing_figures(self, user_id, company_ids):
        self.calls.append(("disagreeing_figures", user_id, tuple(company_ids)))
        return HELD

    def coverage(self, user_id, company_id):
        return []


def test_the_route_reads_the_figures_after_the_access_check(token):
    store = Store()
    auth = FakeAuthorizer({"bob": {NVDA: {"financials"}}, "eve": {NVDA: {"filings"}}}, False)
    api = TestClient(create_app(auth, store, SECRET))
    assert api.get(f"/v1/companies/{NVDA}/data-notes", headers=token("eve")).status_code == 403
    assert store.calls == []
    r = api.get(f"/v1/companies/{NVDA}/data-notes", headers=token("bob"))
    assert r.status_code == 200
    (n,) = r.json()["notes"]
    assert [f["value"] for f in n["detail"]["figures"]] == ["205134", "205134000"]  # exact
    assert store.calls == ["data_notes", ("disagreeing_figures", "bob", (NVDA,))]


# over Postgres, as query_api ------------------------------------------------------------------

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
db = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
CO, OTHER = "9300000001", "9300000002"


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid in (CO, OTHER):
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                         " VALUES (%s, %s, %s)", (cid, cid, cid))
            conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                         (cid, cid))
            for tag, value in ((DETAIL["tag"], 205134), (DETAIL["other_tag"], 205134000)):
                conn.execute(
                    "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
                    " period_end, value, accession_no, form, filed) VALUES (%s,"
                    " 'cash_and_restricted_cash', %s, 'USD', NULL, %s, %s, %s, '10-Q',"
                    " '2026-05-12')", (cid, tag, END, value, ACC))
            run = conn.execute("INSERT INTO ops.quality_runs (companies) VALUES (1)"
                               " RETURNING run_id").fetchone()[0]
            conn.execute("INSERT INTO ops.quality_findings (company_id, check_name, metric,"
                         " period_end, severity, detail, run_id) VALUES (%s, 'tag_disagreement',"
                         " 'cash_and_restricted_cash', %s, 'warning', %s, %s)",
                         (cid, END, psycopg.types.json.Jsonb(DETAIL), run))
        conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                     " VALUES ('t-reader', %s, 'financials')", (CO,))
        conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                     " VALUES ('t-other', %s, 'financials')", (OTHER,))
        yield conn


def figures(conn, user, companies):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        cur.execute("SELECT company_id, tag, value, unit FROM gold.disagreeing_figures(%s)"
                    " ORDER BY 1, 2", (companies,))
        rows = cur.fetchall()
        cur.execute("RESET ROLE")
    return rows


@db
def test_figures_only_for_a_company_the_caller_can_read(conn):
    assert figures(conn, "t-reader", [CO, OTHER]) == [
        (CO, DETAIL["other_tag"], Decimal("205134000"), "USD"),
        (CO, DETAIL["tag"], Decimal("205134"), "USD")]
    assert figures(conn, "t-other", [CO]) == []  # asking for another's company names nothing
    assert figures(conn, "t-nobody", [CO, OTHER]) == []


@db
def test_a_corrected_figure_is_simply_not_there(conn):
    conn.execute("DELETE FROM silver.financial_facts WHERE cik = %s AND tag = %s",
                 (CO, DETAIL["other_tag"]))
    assert [r[1] for r in figures(conn, "t-reader", [CO])] == [DETAIL["tag"]]


@db
def test_query_api_reads_no_value_directly_and_findings_stay_figure_free(conn):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            cur.execute("SELECT value FROM silver.financial_facts LIMIT 1")
        cur.execute("RESET ROLE")
    stored = conn.execute("SELECT detail FROM ops.quality_findings WHERE company_id = %s",
                          (CO,)).fetchone()[0]
    assert set(stored) == set(DETAIL) and "figures" not in stored
