"""App database schema invariants that later steps rely on. Skipped unless
APP_TEST_DATABASE_URL is set; every test is rolled back."""

import os

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

CDC_TABLES = ["users", "subscriptions", "company_grants", "plan_tiers"]


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
