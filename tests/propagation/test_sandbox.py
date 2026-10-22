"""A demo sandbox ("Try the demo") end to end: AppSandboxes writes an ordinary user, plan and
grants to the app database; authz-sync must grant them in OpenFGA and authz.user_access, and
the cleanup's deletion of the expired user must revoke everything, each within the BRD target.
Needs the running stack; skipped unless E2E=1."""

import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")
tributary_authz = pytest.importorskip("tributary_authz")
sandbox = pytest.importorskip("query_api.sandbox")
pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="E2E=1 not set")

TARGET_SECONDS = 30
NVDA, APPLE, MSFT = "0001045810", "0000320193", "0000789019"
TSLA = "0001318605"  # never granted to the sandbox


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


def wait_until(condition, timeout=TARGET_SECONDS):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if condition():
            return time.perf_counter() - start
        time.sleep(0.05)
    pytest.fail(f"not propagated within {timeout}s")


def test_sandbox_is_granted_then_revoked_by_cleanup():
    fga = tributary_authz.FgaClient.from_env()
    companies = [NVDA, APPLE, MSFT]
    user = f"demo-e2e{uuid.uuid4().hex[:8]}"
    # expires a few seconds from now, so cleanup at the real time deletes only expired sandboxes
    now = datetime.now(UTC)
    new = sandbox.NewSandbox(user, companies, [NVDA], now - timedelta(hours=1),
                             now + timedelta(seconds=3))

    def state():
        readable = {c for c in companies + [TSLA]
                    if fga.check(f"user:{user}", "can_read_filings", f"company:{c}")}
        rows = analytics.execute("SELECT count(*) FROM authz.user_access WHERE user_id = %s",
                                 (user,)).fetchone()[0]
        return readable, rows

    with psycopg.connect(os.environ["APP_DATABASE_URL"], autocommit=True) as app, \
            psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as analytics:
        sandboxes = sandbox.AppSandboxes(OneConnection(app))
        sandboxes.create(new, f"e2e-{user}", 1000, 100000)
        try:
            # pro plan: both tiers on each of the three companies, nothing else
            grant_s = wait_until(lambda: state() == (set(companies), 6))
        finally:
            time.sleep(max(0.0, (new.expires_at - datetime.now(UTC)).total_seconds() + 0.1))
            counts = sandboxes.delete_expired(datetime.now(UTC))
        assert counts["users"] >= 1
        revoke_s = wait_until(lambda: state() == (set(), 0))
    print(f"\nsandbox: granted in {grant_s:.2f}s, revoked by cleanup in {revoke_s:.2f}s")
    assert grant_s < TARGET_SECONDS and revoke_s < TARGET_SECONDS
