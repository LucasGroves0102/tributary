"""Revoke access in the app database and time until every derived store reflects it.
Needs the running stack; skipped unless E2E=1.

TARGET_SECONDS is the BRD target (under 30 s). GUARD_SECONDS is a regression guard: the
pipeline takes well under a second (report.md), so a measurement above it means a slow path
came back. PROPAGATION_REPEATS runs each scenario that many times (CI: 5); every measurement
goes to PROPAGATION_RESULTS when set (conftest.py)."""

import os
import time
import uuid
from collections.abc import Callable
from functools import partial

import pytest

psycopg = pytest.importorskip("psycopg")
tributary_authz = pytest.importorskip("tributary_authz")
pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="E2E=1 not set")

TARGET_SECONDS = 30
GUARD_SECONDS = 3
REPEATS = range(max(1, int(os.environ.get("PROPAGATION_REPEATS", "1"))))
NVDA = "0001045810"
APPLE = "0000320193"


@pytest.fixture(scope="module")
def stack():
    fga = tributary_authz.FgaClient.from_env()
    with psycopg.connect(os.environ["APP_DATABASE_URL"], autocommit=True) as app, \
            psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as analytics:
        yield fga, app, analytics


def wait_until(condition: Callable[[], bool], timeout: float = TARGET_SECONDS) -> float:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if condition():
            return time.perf_counter() - start
        time.sleep(0.05)
    pytest.fail(f"not propagated within {timeout}s")


def access_state(fga, analytics, user, relation, tier, company=NVDA):
    allowed = fga.check(f"user:{user}", relation, f"company:{company}")
    row = analytics.execute(
        "SELECT 1 FROM authz.user_access WHERE user_id = %s AND company_id = %s AND tier = %s",
        (user, company, tier)).fetchone() is not None
    return allowed, row


def within_targets(seconds: float) -> bool:
    return seconds < TARGET_SECONDS and seconds < GUARD_SECONDS


@pytest.mark.parametrize("run", REPEATS)
def test_revoked_grant(stack, record, run):
    fga, app, analytics = stack
    state = partial(access_state, fga, analytics, "alice", "can_read_financials", "financials")
    assert state() == (True, True), "precondition: alice can read Nvidia financials"
    app.execute("DELETE FROM company_grants WHERE user_id = 'alice' AND company_id = %s", (NVDA,))
    try:
        revoke_s = wait_until(lambda: state() == (False, False))
    finally:
        app.execute("INSERT INTO company_grants (company_id, user_id) VALUES (%s, 'alice')"
                    " ON CONFLICT DO NOTHING", (NVDA,))
    grant_s = wait_until(lambda: state() == (True, True))
    print(f"\nrevoked grant: gone everywhere in {revoke_s:.2f}s; re-grant in {grant_s:.2f}s")
    record("revoked_grant", revoke_s)
    record("regrant", grant_s)
    assert within_targets(revoke_s)


@pytest.mark.parametrize("run", REPEATS)
def test_plan_downgrade_removes_filings_only(stack, record, run):
    fga, app, analytics = stack
    filings = partial(access_state, fga, analytics, "bob", "can_read_filings", "filings")
    financials = partial(access_state, fga, analytics, "bob", "can_read_financials", "financials")
    assert filings() == (True, True), "precondition: bob (Pro) can read Nvidia filings"
    app.execute("UPDATE subscriptions SET plan_id = 'free' WHERE user_id = 'bob'")
    try:
        downgrade_s = wait_until(lambda: filings() == (False, False))
        assert financials() == (True, True)
    finally:
        app.execute("UPDATE subscriptions SET plan_id = 'pro' WHERE user_id = 'bob'")
    wait_until(lambda: filings() == (True, True))
    print(f"\nPro → Free: filings gone everywhere in {downgrade_s:.2f}s")
    record("pro_to_free", downgrade_s)
    assert within_targets(downgrade_s)


def user_state(fga, analytics, user):
    """(OpenFGA tuples naming the user, authz.user_access + authz.user_tiers rows, can read
    Nvidia financials)."""
    tuples = {t for t in fga.read_all() if t.user == f"user:{user}"}
    rows = analytics.execute(
        "SELECT (SELECT count(*) FROM authz.user_access WHERE user_id = %s)"
        "     + (SELECT count(*) FROM authz.user_tiers WHERE user_id = %s)",
        (user, user)).fetchone()[0]
    return tuples, rows, fga.check(f"user:{user}", "can_read_financials", f"company:{NVDA}")


def counts(state):
    tuples, rows, allowed = state
    return len(tuples), rows, allowed


@pytest.mark.parametrize("run", REPEATS)
def test_deleted_user_loses_everything(stack, record, run):
    """Deleting a user (cascading to their subscription and grants) removes every tuple and
    user_access row derived from them. Uses its own user, so the demo users are untouched."""
    fga, app, analytics = stack
    user = f"e2e-deleted-{uuid.uuid4().hex[:8]}"
    state = partial(user_state, fga, analytics, user)
    app.execute("INSERT INTO users (user_id, email, org_id) VALUES (%s, %s, 'acme')",
                (user, f"{user}@example.com"))
    try:
        app.execute("INSERT INTO subscriptions (user_id, plan_id) VALUES (%s, 'pro')", (user,))
        app.execute("INSERT INTO company_grants (company_id, user_id) VALUES (%s, %s)",
                    (APPLE, user))
        # acme (NVDA, MSFT) + direct AAPL, both tiers: 6 user_access + 2 user_tiers rows;
        # member, 2 x subscriber, viewer + granted (AAPL)
        wait_until(lambda: counts(state()) == (5, 8, True))
    finally:
        app.execute("DELETE FROM users WHERE user_id = %s", (user,))
    delete_s = wait_until(lambda: state() == (set(), 0, False))
    print(f"\ndeleted user: gone everywhere in {delete_s:.2f}s")
    record("deleted_user", delete_s)
    assert within_targets(delete_s)
    # the org's other members keep their access
    assert access_state(fga, analytics, "dave", "can_read_financials", "financials") \
        == (True, True)


# Open access: the public catalog switch (app migration 008) ----------------------------------

CATALOG_CO = "9000000099"  # a throwaway company with one stored document: in catalog:sec
MEMBER = tributary_authz.Tuple("catalog:sec", "catalog", f"company:{CATALOG_CO}")


@pytest.fixture(scope="module")
def catalog_company(stack):
    """A company authz-sync puts in catalog:sec (authz.catalog_companies(): it has a stored
    document). authz-sync re-reads the catalog every CATALOG_REFRESH_SECONDS (CI: 1)."""
    fga, _, analytics = stack
    analytics.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                      " VALUES (%s, 'E2E Catalog Co', %s) ON CONFLICT DO NOTHING",
                      (CATALOG_CO, CATALOG_CO))
    analytics.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)"
                      " ON CONFLICT DO NOTHING", (CATALOG_CO, CATALOG_CO))
    analytics.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
        " url, content_gzip, content_sha256, size_bytes) VALUES (%s, %s, '8-K', '2026-01-02',"
        " 'e2e.htm', 'https://example.invalid/e2e.htm', '\\x00', 'e2e', 1)"
        " ON CONFLICT DO NOTHING", (f"{CATALOG_CO}-26-000001", CATALOG_CO))
    wait_until(lambda: MEMBER in fga.read_all(), timeout=120)
    return CATALOG_CO


def catalog_state(fga, analytics):
    """(carol, Pro with no grants, can read the catalog company's filings in OpenFGA;
    authz.public_access holds it)."""
    allowed = fga.check("user:carol", "can_read_filings", f"company:{CATALOG_CO}")
    row = analytics.execute("SELECT 1 FROM authz.public_access"
                            " WHERE company_id = %s AND tier = 'filings'",
                            (CATALOG_CO,)).fetchone() is not None
    return allowed, row


@pytest.mark.parametrize("run", REPEATS)
def test_opening_and_closing_the_catalog(stack, catalog_company, record, run):
    """One app-DB row opens every catalog company to every user, and closing it is the same
    one row: both must reach OpenFGA and authz.public_access like any revocation."""
    fga, app, analytics = stack
    state = partial(catalog_state, fga, analytics)
    assert state() == (False, False), "precondition: the catalog ships closed"
    app.execute("UPDATE public_catalogs SET public = true WHERE catalog_id = 'sec'")
    try:
        open_s = wait_until(lambda: state() == (True, True))
    finally:
        app.execute("UPDATE public_catalogs SET public = false WHERE catalog_id = 'sec'")
    close_s = wait_until(lambda: state() == (False, False))
    # a private grant never depended on the catalog
    assert access_state(fga, analytics, "alice", "can_read_financials", "financials") \
        == (True, True)
    print(f"\ncatalog: open everywhere in {open_s:.2f}s, closed again in {close_s:.2f}s")
    record("catalog_open", open_s)
    record("catalog_close", close_s)
    assert within_targets(close_s) and within_targets(open_s)

