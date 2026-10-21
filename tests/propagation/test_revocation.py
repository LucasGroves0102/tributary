"""Revoke access in the app database and time until every derived store reflects it.
Needs the running stack; skipped unless E2E=1."""

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


def test_revoked_grant(stack):
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
    assert revoke_s < TARGET_SECONDS


def test_plan_downgrade_removes_filings_only(stack):
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
    assert downgrade_s < TARGET_SECONDS


def user_state(fga, analytics, user):
    """(OpenFGA tuples naming the user, authz.user_access rows, can read Nvidia financials)."""
    tuples = {t for t in fga.read_all() if t.user == f"user:{user}"}
    rows = analytics.execute("SELECT count(*) FROM authz.user_access WHERE user_id = %s",
                             (user,)).fetchone()[0]
    return tuples, rows, fga.check(f"user:{user}", "can_read_financials", f"company:{NVDA}")


def counts(state):
    tuples, rows, allowed = state
    return len(tuples), rows, allowed


def test_deleted_user_loses_everything(stack):
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
        # acme (NVDA, MSFT) + direct AAPL, both tiers: 6 rows; member, 2 x subscriber, viewer
        wait_until(lambda: counts(state()) == (4, 6, True))
    finally:
        app.execute("DELETE FROM users WHERE user_id = %s", (user,))
    delete_s = wait_until(lambda: state() == (set(), 0, False))
    print(f"\ndeleted user: gone everywhere in {delete_s:.2f}s")
    assert delete_s < TARGET_SECONDS
    # the org's other members keep their access
    assert access_state(fga, analytics, "dave", "can_read_financials", "financials") \
        == (True, True)
