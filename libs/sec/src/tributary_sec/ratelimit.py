"""Client-side rate limiter. SEC allows at most 10 requests/second; stay well under."""

import time
from collections.abc import Callable

# Pause after a throttle response when there's no shared bucket to coordinate the cooldown.
LOCAL_COOLDOWN_SECONDS = 60.0


class RateLimiter:
    """Enforces a minimum interval between calls to wait(), in one process. Used on its own
    when there's no shared bucket, and as SharedRateLimiter's fallback."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        cooldown: float = LOCAL_COOLDOWN_SECONDS,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._cooldown = cooldown
        self._next_allowed = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        now = self._clock()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval

    def throttled(self) -> None:
        """The SEC said we're too fast: hold the next request for the cooldown."""
        self._next_allowed = max(self._next_allowed, self._clock() + self._cooldown)
