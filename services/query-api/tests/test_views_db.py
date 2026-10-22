"""Views (app migration 006) and reports (005) against the real app database: ownership, the
forced row-level security, the checks in the tables, and that both go with the user. Skipped
unless APP_TEST_DATABASE_URL is set; every test is rolled back."""

import os
from contextlib import contextmanager

import pytest

from query_api.reports import validate_report
from query_api.views import AppViews, UnknownUser, validate_view
from query_api.workspaces import AppWorkspaces, validate_state

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NVDA = "0001045810"
CHART = validate_state("chart", {"title": "t", "layers": [
    {"kind": "metric", "companyId": NVDA, "metric": "revenue"}]})
HOME = validate_view("home", {"cards": [{"id": "watchlist"}, {"id": "status", "hidden": True}]})
COLS = validate_view("screener", {"columns": ["revenue_ttm", "net_income_ttm"]})


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO users (user_id, email) VALUES ('t-v1', 't-v1@example.com'),"
                     " ('t-v2', 't-v2@example.com')")
        pool = OneConnection(conn)
        yield AppViews(pool), AppWorkspaces(pool), conn


def report(workspace_id, item_id, markdown="# R\n\n[[embed:item-1]]"):
    return validate_report({
        "workspace_id": workspace_id, "markdown": markdown,
        "items": [{"ref": "item-1", "item_id": item_id, "kind": "chart", "name": "Rev",
                   "state": CHART}],
        "verification": {"figures": []}, "written_at": "2026-10-21T09:00:00+00:00",
        "model": "claude-haiku-5-5", "questions": 3, "stop": "end_turn"})


def test_views_save_replace_reset_and_stay_with_their_owner(db):
    v, _, _ = db
    assert v.all("t-v1") == {}
    v.put("t-v1", "home", HOME)
    v.put("t-v1", "screener", COLS)
    v.put("t-v1", "screener", {"columns": ["revenue_ttm"]})
    assert v.all("t-v1") == {"home": HOME, "screener": {"columns": ["revenue_ttm"]}}
    assert v.all("t-v2") == {}
    assert v.delete("t-v2", "home") is False
    assert v.delete("t-v1", "home") is True
    assert v.all("t-v1") == {"screener": {"columns": ["revenue_ttm"]}}
    with pytest.raises(UnknownUser):
        v.put("t-nobody", "home", HOME)


def test_views_row_level_security_binds_the_owner(db):
    v, _, conn = db
    v.put("t-v1", "home", HOME)
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'user_views'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', 't-v2', true)")
    assert conn.execute("SELECT count(*) FROM user_views").fetchone()[0] == 0
    assert conn.execute("UPDATE user_views SET state = '{}'").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO user_views (user_id, view, state)"
                     " VALUES ('t-v1', 'home', '{}')")
    conn.execute("SELECT set_config('app.user_id', 't-v1', true)")
    assert conn.execute("SELECT count(*) FROM user_views").fetchone()[0] == 1
    conn.execute("RESET ROLE")


def test_views_checks_in_the_database_and_cascade(db):
    v, _, conn = db
    for view, state in (("admin", "{}"), ("home", "[]"),
                        ("home", '{"x": "' + "y" * 5000 + '"}')):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO user_views (user_id, view, state) VALUES ('t-v1', %s, %s)",
                         (view, state))
    v.put("t-v1", "home", HOME)
    conn.execute("DELETE FROM users WHERE user_id = 't-v1'")
    assert conn.execute("SELECT count(*) FROM user_views WHERE user_id = 't-v1'"
                        ).fetchone()[0] == 0


def test_reports_are_items_owner_only_and_larger(db):
    _, s, conn = db
    ws = s.create("t-v1", "Semis")
    chart = s.add_item("t-v1", ws["workspace_id"], "chart", "Rev", CHART)
    big = report(ws["workspace_id"], chart["item_id"],
                 "# R\n\n" + "word " * 6000 + "\n[[embed:item-1]]")  # ~30 KB > 16 KiB
    r = s.add_item("t-v1", ws["workspace_id"], "report", "Report: Semis", big)
    got = s.get("t-v1", ws["workspace_id"])
    assert [(i["kind"], i["state"] is None) for i in got["items"]] == [
        ("chart", False), ("report", True)]
    assert s.get("t-v2", ws["workspace_id"]) is None
    assert s.item("t-v1", r["item_id"])["state"]["markdown"] == big["markdown"]
    again = report(ws["workspace_id"], chart["item_id"], "# R2\n\n[[embed:item-1]]")
    assert s.update_report("t-v2", ws["workspace_id"], r["item_id"], again) is None
    other = s.create("t-v1", "Other")
    assert s.update_report("t-v1", other["workspace_id"], r["item_id"], again) is None
    assert s.update_report("t-v1", ws["workspace_id"], chart["item_id"], again) is None
    assert s.update_report("t-v1", ws["workspace_id"], r["item_id"], again)["state"][
        "markdown"] == "# R2\n\n[[embed:item-1]]"
    # other kinds keep 004's 16 KiB cap; reports stop at 128 KiB
    wid = ws["workspace_id"]
    for kind, size in (("chart", 17_000), ("report", 140_000)):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                         " VALUES (%s, 't-v1', %s, 'x', %s::jsonb)",
                         (wid, kind, '{"t": "' + "x" * size + '"}'))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                     " VALUES (%s, 't-v1', 'notes', 'x', '{}')", (wid,))
