"""ops.sec_take_token / ops.sec_report_throttle (db/migrations/019_sec_rate_limit.sql) against
Postgres. Skipped unless TEST_DATABASE_URL is set. Each test uses its own committed bucket row
(other connections must see it) and deletes it afterwards."""

import os
import threading
import time
import uuid

import pytest

from tributary_sec import PostgresStore, SharedRateLimiter

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def bucket():
    """make(rate, capacity) -> bucket name."""
    assert DSN
    names: list[str] = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        def make(rate: float, capacity: float = 2) -> str:
            name = f"test-{uuid.uuid4().hex[:12]}"
            conn.execute("INSERT INTO ops.sec_rate_limit (bucket, rate, capacity, tokens)"
                         " VALUES (%s, %s, %s, %s)", (name, rate, capacity, capacity))
            names.append(name)
            return name

        yield make
        conn.execute("DELETE FROM ops.sec_rate_limit WHERE bucket = ANY(%s)", (names,))


def store(name: str) -> PostgresStore:
    assert DSN
    return PostgresStore(DSN, bucket=name)


def test_migration_seeds_the_sec_bucket():
    assert DSN
    with psycopg.connect(DSN) as conn:
        rate, capacity = conn.execute(
            "SELECT rate, capacity FROM ops.sec_rate_limit WHERE bucket = 'sec'").fetchone()
        classes = dict(conn.execute("SELECT class, reserve FROM ops.sec_rate_classes"))
    assert rate + capacity <= 9   # most requests in any one-second window
    assert classes["live"] == 0 < classes["batch"] < classes["bulk"]


def test_reserve_is_left_for_live(bucket):
    s = store(bucket(rate=0.001))   # practically no refill during the test
    assert s.take("bulk").wait_s == 0
    g = s.take("bulk")
    assert g.wait_s > 100 and g.tokens == pytest.approx(1, abs=0.01)
    assert s.take("batch").wait_s > 0
    assert s.take("live").wait_s == 0
    assert s.take("live").wait_s > 0
    assert s.take("someone-else").wait_s > 0   # unknown class: largest reserve


def test_cooldown_is_shared_and_doubles(bucket):
    name = bucket(rate=100)
    a, b = store(name), store(name)
    assert a.report_throttle() == 10
    assert b.report_throttle() <= 10   # already cooling: in-flight report, not extended
    g = b.take("live")
    assert g.cooling and 9 < g.wait_s <= 10.1
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:  # pretend the cooldown is over
        conn.execute("UPDATE ops.sec_rate_limit SET cooldown_until = now() - interval '1s',"
                     " updated_at = now() - interval '1s' WHERE bucket = %s", (name,))
        assert a.take("live").wait_s == 0
        assert a.report_throttle() == 20
        throttles, = conn.execute("SELECT throttles FROM ops.sec_rate_limit_status"
                                  " WHERE bucket = %s", (name,)).fetchone()
    assert throttles == 2


def test_unknown_bucket_fails_so_callers_fall_back():
    s = store("no-such-bucket")
    with pytest.raises(psycopg.Error):
        s.take("live")
    t = [0.0]
    lim = SharedRateLimiter(s, "live", clock=lambda: t[0], sleep=lambda d: None)
    lim.wait()
    assert lim.in_fallback


def test_concurrent_callers_share_one_rate(bucket):
    rate, capacity, seconds = 20.0, 2.0, 3.0
    name = bucket(rate=rate, capacity=capacity)
    grants: list[tuple[str, float]] = []
    fell_back: list[bool] = []
    lock = threading.Lock()
    start = time.monotonic()

    def run(cls: str) -> None:
        lim = SharedRateLimiter(store(name), cls, max_sleep=0.2)
        while time.monotonic() - start < seconds:
            lim.wait()
            with lock:
                grants.append((cls, time.monotonic()))
                fell_back.append(lim.in_fallback)

    threads = [threading.Thread(target=run, args=(c,))
               for c in ("live", "batch", "bulk", "bulk", "bulk", "bulk")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not any(fell_back)
    times = sorted(t for _, t in grants)
    elapsed = times[-1] - start
    assert len(times) <= capacity + rate * elapsed + 1
    assert len(times) >= 0.8 * rate * seconds
    for i, x in enumerate(times):   # any one-second window: at most capacity + rate (+ skew)
        assert sum(1 for y in times[i:] if y < x + 1.0) <= capacity + rate + 2
    by_class = {c: sum(1 for k, _ in grants if k == c) for c in ("live", "batch", "bulk")}
    # greedy live and batch callers outrank bulk
    assert by_class["live"] > by_class["bulk"] / 4
    assert by_class["live"] + by_class["batch"] > by_class["bulk"]
