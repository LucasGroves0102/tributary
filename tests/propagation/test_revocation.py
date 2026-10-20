"""Revoke access in the app database and time until every derived store reflects it.
Needs the running stack; skipped unless E2E=1."""

import os
import time
from collections.abc import Callable
from functools import partial

import pytest

psycopg = pytest.importorskip("psycopg")
tributary_authz = pytest.importorskip("tributary_authz")
pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="E2E=1 not set")

TARGET_SECONDS = 30
NVDA = "0001045810"


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
