"""App database schema invariants that later steps rely on. Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

CDC_TABLES = ["users", "subscriptions", "company_grants", "plan_tiers", "public_catalogs"]


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("INSERT INTO users (user_id, email) VALUES ('t-user', 't@example.com')")
        c.execute("INSERT INTO organizations (org_id, name) VALUES ('t-org', 'Test')")
        yield c


@pytest.mark.parametrize("table", CDC_TABLES)
def test_cdc_tables_log_full_old_rows(conn, table):
    # a delete event must say who lost access to what, not just a surrogate key
    identity = conn.execute(
        "SELECT relreplident FROM pg_class WHERE oid = %s::regclass", (table,)
    ).fetchone()[0]
    assert identity == "f"


@pytest.mark.parametrize("user_id, org_id", [("t-user", "t-org"), (None, None)])
def test_grant_targets_exactly_one_user_or_org(conn, user_id, org_id):
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO company_grants (company_id, user_id, org_id)"
                     " VALUES ('0001045810', %s, %s)", (user_id, org_id))


def test_duplicate_grant_rejected(conn):
    conn.execute("INSERT INTO company_grants (company_id, user_id) VALUES ('c1', 't-user')")
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute("INSERT INTO company_grants (company_id, user_id) VALUES ('c1', 't-user')")


def test_deleting_a_user_removes_their_grants(conn):
    conn.execute("INSERT INTO company_grants (company_id, user_id) VALUES ('c1', 't-user')")
    conn.execute("DELETE FROM users WHERE user_id = 't-user'")
    assert conn.execute("SELECT count(*) FROM company_grants WHERE company_id = 'c1'"
                        ).fetchone()[0] == 0


def test_plans_grant_expected_tiers(conn):
    tiers = dict(conn.execute(
        "SELECT plan_id, array_agg(tier ORDER BY tier) FROM plan_tiers GROUP BY plan_id"
    ).fetchall())
    assert tiers == {"free": ["financials"], "pro": ["filings", "financials"]}


def test_public_catalog_ships_closed(conn):
    # app migration 008: deploying open access changes nothing until the row is flipped
    assert conn.execute("SELECT catalog_id, public FROM public_catalogs").fetchall() == [
        ("sec", False)]


# --- API keys (migration 019) ---------------------------------------------------------------

KEY_ROW = {"user_id": "t-user", "name": "script", "prefix": "trb_AbC-",
           "key_hash": b"\x01" * 32, "scopes": ["read"],
           "created_at": "2026-11-15T12:00:00Z", "expires_at": "2027-02-13T12:00:00Z"}


def insert_key(conn, **changes):
    row = {**KEY_ROW, **changes}
    conn.execute("INSERT INTO api_keys (user_id, name, prefix, key_hash, scopes, created_at,"
                 " expires_at) VALUES (%(user_id)s, %(name)s, %(prefix)s, %(key_hash)s,"
                 " %(scopes)s, %(created_at)s, %(expires_at)s)", row)


def test_api_keys_are_owner_only_and_not_streamed(conn):
    for table in ("api_keys", "api_key_usage"):
        rls, forced, identity = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity, relreplident FROM pg_class"
            " WHERE oid = %s::regclass", (table,)).fetchone()
        assert (rls, forced, identity) == (True, True, "d"), table
    connector = Path(__file__).parents[2] / "streaming" / "debezium" / "app-connector.json"
    included = json.loads(connector.read_text())["table.include.list"]
    assert "api_key" not in included


@pytest.mark.parametrize("changes", [
    {"user_id": "demo-0123456789abcdef"},       # a sandbox never holds a key
    {"name": ""}, {"name": "x" * 61}, {"name": " padded"}, {"name": "tab\there"},
    {"prefix": "trb_AbC"}, {"prefix": "abc_AbCd"}, {"prefix": "trb_AbC!"},
    {"key_hash": b"\x01" * 31},
    {"scopes": ["write"]}, {"scopes": ["write", "read"]}, {"scopes": []},
    {"scopes": ["read", "admin"]},
    {"expires_at": "2026-11-15T12:00:00Z"},     # not after it was made
    {"expires_at": "2027-11-17T12:00:00Z"},     # over 366 days
])
def test_api_key_rows_are_checked(conn, changes):
    if "user_id" in changes:
        conn.execute("INSERT INTO users (user_id, email) VALUES (%s, 'd@example.com')",
                     (changes["user_id"],))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        insert_key(conn, **changes)


def test_an_api_keys_hash_is_unique_and_it_goes_with_the_user(conn):
    insert_key(conn)
    insert_key(conn, key_hash=b"\x02" * 32, scopes=["read", "write"])
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        insert_key(conn)
    conn.execute("DELETE FROM users WHERE user_id = 't-user'")
    assert conn.execute("SELECT count(*) FROM api_keys WHERE user_id = 't-user'"
                        ).fetchone()[0] == 0


def test_sql_usage_is_owner_only_and_not_streamed(conn):
    """B3a (app 023): the SQL workspace's seconds per user per day grant nothing."""
    rls, forced, identity = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity, relreplident FROM pg_class"
        " WHERE oid = 'sql_usage'::regclass").fetchone()
    assert (rls, forced, identity) == (True, True, "d")
    connector = Path(__file__).parents[2] / "streaming" / "debezium" / "app-connector.json"
    assert "sql_usage" not in json.loads(connector.read_text())["table.include.list"]


def test_a_notebook_is_saved_work_up_to_64_kib(conn):
    conn.execute("INSERT INTO workspaces (workspace_id, user_id, name)"
                 " VALUES ('00000000-0000-0000-0000-00000000000a', 't-user', 'W')")
    insert = ("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state)"
              " VALUES ('00000000-0000-0000-0000-00000000000a', 't-user', 'notebook', 'N', %s)")
    conn.execute(insert, (json.dumps({"cells": [{"id": "a", "kind": "sql",
                                                 "sql": "x" * 60_000}]}),))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(insert, (json.dumps({"cells": [{"id": "a", "kind": "sql",
                                                     "sql": "x" * 66_000}]}),))
