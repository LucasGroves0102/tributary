import pytest

from tributary_sec.ratelimit import RateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_spaces_calls():
    t = FakeTime()
    limiter = RateLimiter(4, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        limiter.wait()
    assert t.slept == pytest.approx([0.25, 0.25])


def test_no_sleep_when_idle_long_enough():
    t = FakeTime()
    limiter = RateLimiter(4, clock=t.clock, sleep=t.sleep)
    limiter.wait()
    t.now += 1.0
    limiter.wait()
    assert t.slept == []
