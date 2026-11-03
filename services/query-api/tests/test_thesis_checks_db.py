"""AppThesisChecks against the real app database (db/app migration 010): only the owner's
theses take checks, the forced row-level security binds the table owner, the newest MAX_CHECKS
are kept, and checks go with their thesis. Skipped unless APP_TEST_DATABASE_URL is set; every
test is rolled back."""

import os
import uuid
from contextlib import contextmanager

import pytest

import query_api.thesis_checks as tc
from query_api.thesis_checks import AppThesisChecks, validate_check
from query_api.workspaces import AppWorkspaces, validate_state

from .test_thesis_checks import result

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NVDA = "0001045810"
THESIS = validate_state("thesis", {"companyId": NVDA, "markdown": "# NVDA\n\nRevenue grew."})
CHART = validate_state("chart", {"title": "t", "layers": [
    {"kind": "metric", "companyId": NVDA, "metric": "revenue"}]})


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO users (user_id, email) VALUES ('t-tc1', 't-tc1@example.com'),"
                     " ('t-tc2', 't-tc2@example.com')")
        ws = AppWorkspaces(OneConnection(conn))
        w = ws.create("t-tc1", "Semis")
        thesis = ws.add_item("t-tc1", w["workspace_id"], "thesis", "NVDA thesis", THESIS)
        chart = ws.add_item("t-tc1", w["workspace_id"], "chart", "Rev", CHART)
        yield AppThesisChecks(OneConnection(conn)), ws, conn, thesis["item_id"], chart["item_id"]


def check(item_id):
    return validate_check(result(item_id))


def test_add_list_get(db):
    s, _, _, tid, _ = db
    row = s.add("t-tc1", tid, check(tid))
    uuid.UUID(row["check_id"])
    assert row["text_hash"] == "a" * 64 and row["model"] == "claude-haiku-5-5"
    assert row["counts"]["supported"] == 1 and row["cost_usd"] == pytest.approx(0.0012)
    assert [c["check_id"] for c in s.list("t-tc1", tid)] == [row["check_id"]]
    got = s.get("t-tc1", tid, row["check_id"])
    assert got["result"]["claims"][0]["quote"] == "Revenue was $46.7B."


def test_summary_carries_what_revisit_needs(db):
    """SUMMARY reads the thesis's company, checked_at and filings out of the result; a result
    stored before Revisit (no filings) lists []."""
    s, _, _, tid, _ = db
    basis = [{"company_id": NVDA, "evidence": None, "latest": {
        "accession_no": "0001045810-26-000030", "form": "10-Q", "filed": "2026-08-27"}}]
    row = s.add("t-tc1", tid, validate_check(result(tid, filings=basis)))
    assert (row["company_id"], row["filings"]) == (NVDA, basis)
    assert row["checked_at"] == "2026-11-02T09:00:00+00:00"
    old = check(tid)
    del old["filings"]
    s.add("t-tc1", tid, old)
    assert [c["filings"] for c in s.list("t-tc1", tid)] == [[], basis]


def test_only_the_owners_thesis(db):
    s, _, _, tid, chart = db
    assert s.add("t-tc2", tid, check(tid)) is None  # bob's view: no such thesis
    assert s.add("t-tc1", chart, check(chart)) is None  # not a thesis
    assert s.list("t-tc2", tid) is None and s.list("t-tc1", chart) is None
    row = s.add("t-tc1", tid, check(tid))
    assert s.get("t-tc2", tid, row["check_id"]) is None


def test_newest_are_kept(db, monkeypatch):
    s, _, _, tid, _ = db
    monkeypatch.setattr(tc, "MAX_CHECKS", 2)
    ids = [s.add("t-tc1", tid, check(tid))["check_id"] for _ in range(3)]
    assert [c["check_id"] for c in s.list("t-tc1", tid)] == ids[:0:-1]  # newest first


def test_row_level_security_binds_the_table_owner(db):
    s, _, conn, tid, _ = db
    s.add("t-tc1", tid, check(tid))
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'thesis_checks'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))

    def visible(user):
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        return conn.execute("SELECT count(*) FROM thesis_checks").fetchone()[0]

    assert visible("t-tc1") == 1
    assert visible("t-tc2") == 0 and visible("") == 0
    assert conn.execute("UPDATE thesis_checks SET model = 'x'").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO thesis_checks (item_id, user_id, text_hash, model, counts,"
                     " result) VALUES (%s, 't-tc1', %s, 'm', '{}', '{}')", (tid, "b" * 64))
    conn.execute("RESET ROLE")


def test_a_check_belongs_to_its_thesis_owner_and_goes_with_it(db):
    s, ws, conn, tid, _ = db
    s.add("t-tc1", tid, check(tid))
    # a row naming someone else as owner of alice's thesis can't exist (composite FK)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO thesis_checks (item_id, user_id, text_hash, model, counts,"
                     " result) VALUES (%s, 't-tc2', %s, 'm', '{}', '{}')", (tid, "b" * 64))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO thesis_checks (item_id, user_id, text_hash, model, counts,"
                     " result) VALUES (%s, 't-tc1', 'short', 'm', '{}', '{}')", (tid,))
    assert ws.delete_item("t-tc1", tid)
    assert conn.execute("SELECT count(*) FROM thesis_checks WHERE item_id = %s",
                        (tid,)).fetchone()[0] == 0
