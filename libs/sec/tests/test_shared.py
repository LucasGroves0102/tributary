import heapq
import random

import pytest

from tributary_sec import MemoryBucket, PostgresStore, RateLimiter, SharedRateLimiter
from tributary_sec.shared import Grant, limiter_from_env


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


# --- the bucket algorithm (MemoryBucket, twin of ops.sec_take_token) ---------------------------

def test_bucket_bursts_capacity_then_refills_at_rate():
    t = FakeTime()
    b = MemoryBucket(rate=7, capacity=2, clock=t.clock)
    assert b.take("live").wait_s == 0
    assert b.take("live").wait_s == 0
    g = b.take("live")
    assert g.wait_s == pytest.approx(1 / 7)
    t.now += 1 / 7
    assert b.take("live").wait_s == 0


def test_lower_classes_leave_their_reserve_for_live():
    t = FakeTime()
    b = MemoryBucket(rate=7, capacity=2, clock=t.clock)
    assert b.take("bulk").wait_s == 0          # 2 -> 1
    assert b.take("bulk").wait_s == pytest.approx(1 / 7)  # needs 2
    assert b.take("batch").wait_s == pytest.approx(0.5 / 7)  # needs 1.5
    assert b.take("live").wait_s == 0          # needs 1: the reserve is live's
    assert b.take("live").wait_s == pytest.approx(1 / 7)


def test_unknown_class_gets_the_largest_reserve():
    t = FakeTime()
    b = MemoryBucket(rate=7, capacity=2, clock=t.clock)
    b.take("live")
    assert b.take("crawler").wait_s == pytest.approx(1 / 7)


def test_cooldown_stops_every_class_and_doubles_while_throttles_continue():
    t = FakeTime()
    b = MemoryBucket(rate=7, capacity=2, clock=t.clock)
    assert b.report_throttle() == 10
    assert b.report_throttle() == pytest.approx(10)   # in-flight reports don't extend it
    g = b.take("live")
    assert g.cooling and g.wait_s == pytest.approx(10 + 1 / 7)
    t.now += 10
    assert b.take("live").wait_s == pytest.approx(1 / 7)  # bucket refills from empty
    t.now += 1 / 7
    assert b.take("live").wait_s == 0
    assert b.report_throttle() == 20
    t.now += 21
    assert b.report_throttle() == 40
    last = 0.0
    for _ in range(10):
        t.now += 700
        last = b.report_throttle()
    assert last == 600
    t.now += 1801
    assert b.report_throttle() == 10   # quiet for 30 minutes: strikes reset


def simulate(bucket_rate, live_arrivals, bulk_callers, duration, seed=1, batch_callers=0):
    """Discrete-event run of SharedRateLimiter's loop: greedy batch/bulk callers plus live
    requests arriving at given times. Returns grant times per caller and live latencies."""
    t = FakeTime()
    bucket = MemoryBucket(rate=bucket_rate, capacity=2, clock=t.clock)
    rng = random.Random(seed)
    events: list[tuple[float, int, str, str, float]] = []  # (at, seq, caller, class, arrival)
    seq = 0

    def push(at, caller, cls, arrival):
        nonlocal seq
        heapq.heappush(events, (at, seq, caller, cls, arrival))
        seq += 1

    for a in live_arrivals:
        push(a, "live", "live", a)
    for i in range(bulk_callers):
        push(0.0, f"bulk{i}", "bulk", 0.0)
    for i in range(batch_callers):
        push(0.0, f"batch{i}", "batch", 0.0)
    grants: dict[str, list[float]] = {}
    latencies: list[float] = []
    while events:
        at, _, caller, cls, arrival = heapq.heappop(events)
        if at > duration:
            break
        t.now = at
        g = bucket.take(cls)
        if g.wait_s <= 0:
            grants.setdefault(caller, []).append(at)
            if cls == "live":
                latencies.append(at - arrival)
            else:
                push(at, caller, cls, at)  # greedy: next request right away
        else:
            push(at + min(g.wait_s * (1 + 0.1 * rng.random()), 1.0), caller, cls, arrival)
    return grants, latencies


def max_in_window(times, window=1.0):
    times = sorted(times)
    j, best = 0, 0
    for i, x in enumerate(times):
        while times[j] <= x - window:
            j += 1
        best = max(best, i - j + 1)
    return best


def test_total_rate_stays_under_the_sec_limit():
    live = [k * 0.05 for k in range(200)]  # a 10 s burst of 20 req/s from live
    grants, _ = simulate(7, live, bulk_callers=3, duration=60, batch_callers=1)
    every = [x for ts in grants.values() for x in ts]
    assert max_in_window(every) <= 9
    assert len(every) / 60 == pytest.approx(7, abs=0.1)


def test_live_is_not_starved_by_bulk():
    live = [5.0 + 10 * k for k in range(10)]  # one live request every 10 s
    grants, latencies = simulate(7, live, bulk_callers=4, duration=100)
    assert len(latencies) == 10
    assert max(latencies) == 0   # the reserve is always there for live
    bulk = sum(len(v) for k, v in grants.items() if k.startswith("bulk"))
    assert bulk / 100 == pytest.approx(7, rel=0.05)   # bulk gets the rest, less a little jitter


def test_live_burst_takes_the_whole_rate_while_bulk_waits():
    live = [10.0 + k * 0.01 for k in range(70)]  # 70 live requests at once
    grants, latencies = simulate(7, live, bulk_callers=2, duration=30)
    assert max(latencies) <= 70 / 7 + 0.5   # served at the full rate, as if bulk weren't there
    bulk_during = [x for k, v in grants.items() if k.startswith("bulk") for x in v
                   if 10.5 < x < 19.5]
    assert bulk_during == []


def test_batch_goes_before_bulk():
    grants, _ = simulate(7, [], bulk_callers=2, duration=30, batch_callers=1)
    batch = len(grants.get("batch0", []))
    bulk = sum(len(v) for k, v in grants.items() if k.startswith("bulk"))
    assert batch > 0.9 * (batch + bulk)


def test_callers_of_one_class_share_fairly():
    grants, _ = simulate(7, [], bulk_callers=3, duration=300)
    counts = [len(grants[f"bulk{i}"]) for i in range(3)]
    assert sum(counts) / 300 == pytest.approx(7, rel=0.05)
    assert min(counts) > 0.8 * max(counts)


# --- SharedRateLimiter (the client side) -----------------------------------------------------

class ScriptedStore:
    def __init__(self, answers):
        self.answers = list(answers)
        self.takes = 0
        self.throttles = 0

    def take(self, caller_class):
        self.takes += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return Grant(a, 1.0, self.takes)

    def report_throttle(self):
        self.throttles += 1
        a = self.answers.pop(0) if self.answers else 30.0
        if isinstance(a, Exception):
            raise a
        return a


def limiter(store, t, **kw):
    fallback = RateLimiter(2, clock=t.clock, sleep=t.sleep)
    return SharedRateLimiter(store, "bulk", fallback=fallback, clock=t.clock, sleep=t.sleep,
                             rand=lambda: 0.0, **kw)


def test_granted_without_sleeping():
    t = FakeTime()
    store = ScriptedStore([0.0])
    limiter(store, t).wait()
    assert t.slept == [] and store.takes == 1


def test_sleeps_the_wait_capped_and_asks_again():
    t = FakeTime()
    store = ScriptedStore([0.25, 12.0, 0.0])
    limiter(store, t).wait()
    assert t.slept == [0.25, 1.0]
    assert store.takes == 3


def test_jitter_spreads_waits():
    t = FakeTime()
    lim = SharedRateLimiter(ScriptedStore([0.5, 0.0]), "bulk", clock=t.clock, sleep=t.sleep,
                            rand=lambda: 1.0)
    lim.wait()
    assert t.slept == [pytest.approx(0.55)]


def test_store_failure_falls_back_to_the_local_limit_then_recovers():
    t = FakeTime()
    store = ScriptedStore([OSError("db down"), 0.0])
    lim = limiter(store, t, retry_after=30)
    for _ in range(3):
        lim.wait()
    assert lim.in_fallback
    assert t.slept == pytest.approx([0.5, 0.5])   # 2 req/s, never unlimited
    assert store.takes == 1                        # not retried before retry_after
    t.now += 30
    lim.wait()
    assert not lim.in_fallback and store.takes == 2


def test_throttle_reports_a_shared_cooldown():
    t = FakeTime()
    store = ScriptedStore([])
    lim = limiter(store, t)
    lim.throttled()
    assert store.throttles == 1


def test_throttle_without_the_store_pauses_locally():
    t = FakeTime()
    store = ScriptedStore([OSError("db down")])
    lim = limiter(store, t)
    lim.throttled()
    assert lim.in_fallback
    lim.wait()
    assert t.slept == [pytest.approx(60)]


def test_rejects_unknown_class():
    with pytest.raises(ValueError):
        SharedRateLimiter(ScriptedStore([]), "vip")


def test_shared_limiter_over_memory_bucket_spaces_requests():
    t = FakeTime()
    bucket = MemoryBucket(rate=7, capacity=2, clock=t.clock)
    lim = SharedRateLimiter(bucket, "live", clock=t.clock, sleep=t.sleep, rand=lambda: 0.0)
    for _ in range(16):
        lim.wait()
    assert t.now == pytest.approx(14 / 7)


# --- configuration -----------------------------------------------------------------------------

def test_env_without_database_uses_the_local_limit():
    lim = limiter_from_env({})
    assert isinstance(lim, RateLimiter)


def test_env_with_database_uses_the_shared_bucket():
    lim = limiter_from_env({"SEC_LIMITER_DATABASE_URL": "postgresql://x/y",
                            "SEC_CALLER_CLASS": "live"})
    assert isinstance(lim, SharedRateLimiter)
    assert lim.caller_class == "live"
    assert isinstance(lim._store, PostgresStore)


def test_env_defaults_to_batch_and_database_url():
    lim = limiter_from_env({"DATABASE_URL": "postgresql://x/y"})
    assert isinstance(lim, SharedRateLimiter) and lim.caller_class == "batch"


def test_env_rejects_unknown_class():
    with pytest.raises(ValueError):
        limiter_from_env({"SEC_CALLER_CLASS": "fast"})
