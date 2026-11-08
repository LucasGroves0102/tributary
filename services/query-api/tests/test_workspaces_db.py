"""AppWorkspaces against the real app database (db/app migration 004): ownership in every
statement, the forced row-level security, caps, duplicates, recent items and filings, and that
a user's work goes with the user. Skipped unless APP_TEST_DATABASE_URL is set; every test is
rolled back."""

import os
import uuid
from contextlib import contextmanager

import pytest

import query_api.workspaces as w
from query_api.workspaces import (
    AppWorkspaces,
    LimitReached,
    NotAProject,
    UnknownUser,
    seed,
    validate_project,
    validate_state,
)

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NVDA, AAPL = "0001045810", "0000320193"
CHART = validate_state("chart", {"title": "t", "layers": [
    {"kind": "metric", "companyId": NVDA, "metric": "revenue"}]})
COMPS = validate_state("comps", {"target": NVDA, "peers": [AAPL]})
TABLES = ("workspaces", "workspace_items", "recent_filings")
ACC = "0001045810-26-000012"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO users (user_id, email) VALUES ('t-ws1', 't-ws1@example.com'),"
                     " ('t-ws2', 't-ws2@example.com')")
        yield AppWorkspaces(OneConnection(conn)), conn


def count(conn, table, user):
    return conn.execute(
        sql.SQL("SELECT count(*) FROM {} WHERE user_id = %s").format(sql.Identifier(table)),
        (user,)).fetchone()[0]


def test_create_list_items_rename_delete(db):
    s, conn = db
    ws = s.create("t-ws1", "Semis")
    uuid.UUID(ws["workspace_id"])
    item = s.add_item("t-ws1", ws["workspace_id"], "chart", "Rev", CHART)
    assert item["state"] == CHART and item["kind"] == "chart" and item["opened_at"] is None
    s.add_item("t-ws1", ws["workspace_id"], "comps", "Comps", COMPS)
    listed = s.list("t-ws1")
    assert [(x["name"], [i["name"] for i in x["items"]]) for x in listed] == [
        ("Semis", ["Rev", "Comps"])]
    assert s.list("t-ws2") == []
    got = s.item("t-ws1", item["item_id"])
    assert got["state"] == CHART and got["workspace_name"] == "Semis"
    assert s.rename("t-ws1", ws["workspace_id"], "Chips")["name"] == "Chips"
    updated = s.update_item("t-ws1", item["item_id"], None, {**CHART, "range": 5})
    assert updated["state"]["range"] == 5 and updated["name"] == "Rev"
    assert s.update_item("t-ws1", item["item_id"], "Revenue", None)["state"]["range"] == 5
    assert s.delete_item("t-ws1", item["item_id"]) is True
    assert s.delete_item("t-ws1", item["item_id"]) is False
    assert s.delete("t-ws1", ws["workspace_id"]) is True
    assert count(conn, "workspace_items", "t-ws1") == 0  # items go with their workspace


@pytest.mark.parametrize("catalog_public", [False, True])
def test_another_users_work_is_out_of_reach(db, catalog_public):
    s, conn = db
    # open access (app migration 008) opens SEC data only, never anyone's saved work
    conn.execute("UPDATE public_catalogs SET public = %s WHERE catalog_id = 'sec'",
                 (catalog_public,))
    ws = s.create("t-ws1", "Mine")
    item = s.add_item("t-ws1", ws["workspace_id"], "chart", "Rev", CHART)
    wid, iid = ws["workspace_id"], item["item_id"]
    theirs = s.create("t-ws2", "Theirs")["workspace_id"]
    assert s.rename("t-ws2", wid, "x") is None
    assert s.delete("t-ws2", wid) is False
    assert s.duplicate("t-ws2", wid, None) is None
    assert s.add_item("t-ws2", wid, "chart", "x", CHART) is None
    assert s.item("t-ws2", iid) is None
    assert s.update_item("t-ws2", iid, "x", CHART) is None
    assert s.duplicate_item("t-ws2", iid, theirs, None) is None
    assert s.duplicate_item("t-ws1", iid, theirs, None) is None  # not into someone else's
    assert s.delete_item("t-ws2", iid) is False
    assert s.mark_opened("t-ws2", iid) is None
    assert s.recent("t-ws2", 10) == []
    assert s.item("t-ws1", iid)["name"] == "Rev"
    assert s.list("t-ws1")[0]["name"] == "Mine"
    # the database itself refuses an item filed under another user's workspace
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                     " VALUES (%s, 't-ws2', 'chart', 'x', '{}')", (wid,))


def test_row_level_security_binds_the_table_owner(db):
    """The Query API's app-DB login owns these tables; FORCE makes the policy apply to it.
    Superusers bypass RLS, so this runs as the owner (CI migrates as a non-superuser)."""
    s, conn = db
    ws = s.create("t-ws1", "Mine")
    s.add_item("t-ws1", ws["workspace_id"], "chart", "Rev", CHART)
    s.read_filing("t-ws1", NVDA, ACC, None)
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'workspaces'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))

    def visible(user):
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        return [conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))
                             ).fetchone()[0] for t in TABLES]

    assert visible("t-ws1") == [1, 1, 1]
    assert visible("t-ws2") == [0, 0, 0]  # no WHERE needed: the policy filters
    assert visible("") == [0, 0, 0]
    assert conn.execute("UPDATE workspaces SET name = 'taken'").rowcount == 0
    assert conn.execute("DELETE FROM workspace_items").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO workspaces (user_id, name) VALUES ('t-ws1', 'planted')")
    # seeding a new user's starter workspace works as the owner (it sets app.user_id)
    with conn.cursor() as cur:
        seed(cur, "t-ws2", [{"name": "Starter", "items": [("comps", "C", COMPS)]}])
    assert visible("t-ws2") == [1, 1, 0]
    # foreign-key cascades bypass the policy: deleting a user still deletes their work
    conn.execute("SELECT set_config('app.user_id', '', true)")
    conn.execute("DELETE FROM users WHERE user_id = 't-ws1'")
    conn.execute("RESET ROLE")
    assert [count(conn, t, "t-ws1") for t in TABLES] == [0, 0, 0]


def test_duplicate_workspace_copies_items_in_order(db):
    s, _ = db
    ws = s.create("t-ws1", "Beverages")
    s.add_item("t-ws1", ws["workspace_id"], "chart", "A", CHART)
    s.add_item("t-ws1", ws["workspace_id"], "comps", "B", COMPS)
    copy = s.duplicate("t-ws1", ws["workspace_id"], None)
    assert copy["name"] == "Beverages (copy)"
    assert [i["name"] for i in copy["items"]] == ["A", "B"]
    assert copy["workspace_id"] != ws["workspace_id"]
    assert s.item("t-ws1", copy["items"][1]["item_id"])["state"] == COMPS
    named = s.duplicate("t-ws1", ws["workspace_id"], "Drinks")
    assert named["name"] == "Drinks"
    item = s.duplicate_item("t-ws1", copy["items"][0]["item_id"], ws["workspace_id"], None)
    assert item["name"] == "A (copy)" and item["workspace_id"] == ws["workspace_id"]


def test_caps(db, monkeypatch):
    s, _ = db
    monkeypatch.setattr(w, "MAX_WORKSPACES", 2)
    monkeypatch.setattr(w, "MAX_ITEMS_PER_WORKSPACE", 2)
    monkeypatch.setattr(w, "MAX_ITEMS", 3)
    a = s.create("t-ws1", "a")["workspace_id"]
    b = s.create("t-ws1", "b")["workspace_id"]
    with pytest.raises(LimitReached):
        s.create("t-ws1", "c")
    s.create("t-ws2", "theirs")  # per user
    s.add_item("t-ws1", a, "chart", "1", CHART)
    s.add_item("t-ws1", a, "chart", "2", CHART)
    with pytest.raises(LimitReached):
        s.add_item("t-ws1", a, "chart", "3", CHART)
    s.add_item("t-ws1", b, "chart", "3", CHART)
    with pytest.raises(LimitReached):
        s.add_item("t-ws1", b, "chart", "4", CHART)  # MAX_ITEMS across workspaces
    with pytest.raises(LimitReached):
        s.duplicate("t-ws1", a, None)


def test_unknown_user(db):
    s, _ = db
    with pytest.raises(UnknownUser):
        s.create("t-nobody", "x")
    with pytest.raises(UnknownUser):
        s.read_filing("t-nobody", NVDA, ACC, None)


def test_recent_items_and_filings(db, monkeypatch):
    s, _ = db
    ws = s.create("t-ws1", "W")
    a = s.add_item("t-ws1", ws["workspace_id"], "chart", "A", CHART)["item_id"]
    b = s.add_item("t-ws1", ws["workspace_id"], "comps", "B", COMPS)["item_id"]
    s.add_item("t-ws1", ws["workspace_id"], "chart", "never opened", CHART)
    for iid in (a, b, a):
        assert s.mark_opened("t-ws1", iid) is not None
    recent = s.recent("t-ws1", 10)
    assert [(r["name"], r["workspace_name"]) for r in recent] == [("A", "W"), ("B", "W")]
    assert s.recent("t-ws1", 1)[0]["name"] == "A"
    monkeypatch.setattr(w, "MAX_RECENT_FILINGS", 2)
    accs = [f"0001045810-26-00001{i}" for i in range(3)]
    for acc in accs:
        s.read_filing("t-ws1", NVDA, acc, "II-1A")
    s.read_filing("t-ws1", NVDA, accs[1], None)  # again: newest, keeps its section
    got = s.recent_filings("t-ws1", 10)
    assert [(r["accession_no"], r["section_key"]) for r in got] == [
        (accs[1], "II-1A"), (accs[2], "II-1A")]
    assert s.recent_filings("t-ws2", 10) == []


def test_starter_workspace_goes_with_the_user(db):
    s, conn = db
    with conn.cursor() as cur:
        seed(cur, "t-ws1", [{"name": "Starter", "items": [("chart", "A", CHART),
                                                           ("comps", "B", COMPS)]}])
    assert [(x["name"], [i["name"] for i in x["items"]]) for x in s.list("t-ws1")] == [
        ("Starter", ["A", "B"])]
    conn.execute("DELETE FROM users WHERE user_id = 't-ws1'")
    assert [count(conn, t, "t-ws1") for t in TABLES] == [0, 0, 0]


def test_state_and_name_checks_in_the_database(db):
    _, conn = db
    wid = conn.execute("INSERT INTO workspaces (user_id, name) VALUES ('t-ws1', 'w')"
                       " RETURNING workspace_id").fetchone()[0]
    for kind, name, state in (("notes", "x", "{}"), ("chart", "", "{}"),
                              ("chart", "x", "[]"),
                              ("chart", "x", '{"t": "' + "x" * 17000 + '"}')):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
                         " VALUES (%s, 't-ws1', %s, %s, %s::jsonb)", (wid, kind, name, state))


THESIS = validate_state("thesis", {"companyId": NVDA, "markdown": "# Why\n\nBecause.\n"})
MODEL = validate_state("model", {"companyId": NVDA, "assumptions": [
    {"growth": 10, "margin": 30, "note": "steady"}]})


def test_theses_and_models_are_owner_only(db):
    """App migration 009: a thesis or model is another workspace item; 004's forced RLS and
    the owner filter keep it from every other user."""
    s, conn = db
    wid = s.create("t-ws1", "Mine")["workspace_id"]
    theirs = s.create("t-ws2", "Theirs")["workspace_id"]
    t = s.add_item("t-ws1", wid, "thesis", "NVDA thesis", THESIS)["item_id"]
    m = s.add_item("t-ws1", wid, "model", "NVDA model", MODEL)["item_id"]
    for iid in (t, m):
        assert s.item("t-ws2", iid) is None
        assert s.update_item("t-ws2", iid, "taken", None) is None
        assert s.duplicate_item("t-ws2", iid, theirs, None) is None
        assert s.delete_item("t-ws2", iid) is False
        assert s.mark_opened("t-ws2", iid) is None
    assert s.item("t-ws1", t)["state"] == THESIS and s.item("t-ws1", m)["state"] == MODEL
    # the workspace lists a thesis without its state (large, like a report), a model with it
    got = {i["kind"]: i["state"] for i in s.get("t-ws1", wid)["items"]}
    assert got == {"thesis": None, "model": MODEL}
    assert s.get("t-ws2", wid) is None
    # and the policy alone hides them (no WHERE), as the table owner
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'workspace_items'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', 't-ws2', true)")
    assert conn.execute("SELECT count(*) FROM workspace_items WHERE kind IN"
                        " ('thesis', 'model')").fetchone()[0] == 0
    assert conn.execute("UPDATE workspace_items SET state = '{}'").rowcount == 0
    assert conn.execute("DELETE FROM workspace_items").rowcount == 0
    conn.execute("SELECT set_config('app.user_id', 't-ws1', true)")
    assert conn.execute("SELECT count(*) FROM workspace_items WHERE kind IN"
                        " ('thesis', 'model')").fetchone()[0] == 2
    conn.execute("RESET ROLE")


def test_a_thesis_keeps_the_versions_it_replaced_owner_only(db):
    """App migration 014 (M5): a save over a thesis keeps the replaced version in the same
    transaction, the newest MAX_VERSIONS; versions are owner-only by the forced policy and go
    with their thesis."""
    s, conn = db
    wid = s.create("t-ws1", "Mine")["workspace_id"]
    t = s.add_item("t-ws1", wid, "thesis", "NVDA thesis",
                   validate_state("thesis", {"markdown": "x" * 7269}))["item_id"]
    assert s.versions("t-ws1", t) == []
    s.update_item("t-ws1", t, None, validate_state("thesis", {"markdown": "y" * 1297}))
    s.update_item("t-ws1", t, "renamed", None)  # a rename isn't a version
    s.update_item("t-ws1", t, None, validate_state("thesis", {"markdown": "y" * 1297}))  # same
    listed = s.versions("t-ws1", t)
    assert [(v["name"], v["characters"]) for v in listed] == [("NVDA thesis", 7269)]
    assert s.version("t-ws1", t, listed[0]["version_id"])["state"] == {"markdown": "x" * 7269}
    assert s.versions("t-ws2", t) is None
    assert s.version("t-ws2", t, listed[0]["version_id"]) is None
    for n in range(w.MAX_VERSIONS + 3):
        s.update_item("t-ws1", t, None, validate_state("thesis", {"markdown": str(n)}))
    assert len(s.versions("t-ws1", t)) == w.MAX_VERSIONS
    # the policy alone hides them from another user, as the table owner
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'item_versions'").fetchone()[0]
    if not conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                        (owner,)).fetchone()[0]:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
        conn.execute("SELECT set_config('app.user_id', 't-ws2', true)")
        assert conn.execute("SELECT count(*) FROM item_versions").fetchone()[0] == 0
        assert conn.execute("DELETE FROM item_versions").rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("INSERT INTO item_versions (item_id, user_id, name, state, saved_at)"
                         " VALUES (%s, 't-ws1', 'x', '{}', now())", (t,))
        conn.execute("SELECT set_config('app.user_id', 't-ws1', true)")
        conn.execute("RESET ROLE")
    # deleting the thesis deletes its versions
    assert s.delete_item("t-ws1", t)
    assert count(conn, "item_versions", "t-ws1") == 0


def test_thesis_state_may_be_as_large_as_a_reports(db):
    _, conn = db
    wid = conn.execute("INSERT INTO workspaces (user_id, name) VALUES ('t-ws1', 'w')"
                       " RETURNING workspace_id").fetchone()[0]
    big = '{"markdown": "' + "x" * 120_000 + '"}'
    insert = ("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
              " VALUES (%s, 't-ws1', %s, 'x', %s::jsonb)")
    conn.execute(insert, (wid, "thesis", big))
    for kind, state in (("thesis", '{"markdown": "' + "x" * 131_100 + '"}'),
                        ("model", big), ("chart", big), ("essay", "{}")):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (wid, kind, state))


PROJECT = validate_project({"companyId": NVDA, "peers": [AAPL], "notes": "GPUs", "done": []})


def test_analysis_projects_are_owner_only_workspaces(db):
    """App migration 012: a project is a workspace with `project` set; existing workspaces keep
    NULL. 004's forced RLS covers the column: another user can't read, change or copy it."""
    s, conn = db
    plain = s.create("t-ws1", "Folder")
    assert plain["project"] is None
    p = s.create("t-ws1", "NVDA analysis", PROJECT)
    assert p["project"] == PROJECT
    s.add_item("t-ws1", p["workspace_id"], "model", "M", MODEL)
    assert {x["name"]: x["project"] for x in s.list("t-ws1")} == {
        "Folder": None, "NVDA analysis": PROJECT}
    assert s.get("t-ws1", p["workspace_id"])["project"] == PROJECT
    changed = {**PROJECT, "notes": "Data centers", "done": ["business"]}
    got = s.update("t-ws1", p["workspace_id"], None, changed)
    assert got["project"] == changed and got["name"] == "NVDA analysis"
    assert s.update("t-ws1", p["workspace_id"], "Nvidia", None)["project"] == changed
    with pytest.raises(NotAProject):
        s.update("t-ws1", plain["workspace_id"], None, PROJECT)
    assert s.get("t-ws1", plain["workspace_id"])["project"] is None
    copy = s.duplicate("t-ws1", p["workspace_id"], None)
    assert copy["project"] == changed and [i["kind"] for i in copy["items"]] == ["model"]
    # another user: the same None as a missing workspace
    assert s.update("t-ws2", p["workspace_id"], None, PROJECT) is None
    assert s.update("t-ws2", p["workspace_id"], "taken", None) is None
    assert s.get("t-ws2", p["workspace_id"]) is None
    assert s.duplicate("t-ws2", p["workspace_id"], None) is None
    assert s.get("t-ws1", p["workspace_id"])["project"] == changed
    # the policy alone hides the column's rows from another user, as the table owner
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'workspaces'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', 't-ws2', true)")
    assert conn.execute("SELECT count(*) FROM workspaces WHERE project IS NOT NULL"
                        ).fetchone()[0] == 0
    assert conn.execute("UPDATE workspaces SET project = '{}'").rowcount == 0
    conn.execute("SELECT set_config('app.user_id', 't-ws1', true)")
    assert conn.execute("SELECT count(*) FROM workspaces WHERE project IS NOT NULL"
                        ).fetchone()[0] == 2
    conn.execute("RESET ROLE")


def test_project_checks_in_the_database(db):
    _, conn = db
    insert = "INSERT INTO workspaces (user_id, name, project) VALUES ('t-ws1', 'p', %s::jsonb)"
    conn.execute(insert, ('{"companyId": "0001045810"}',))
    conn.execute(insert, (None,))  # a plain folder
    for bad in ("[]", '"x"', '{"notes": "' + "x" * 17000 + '"}'):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (bad,))
