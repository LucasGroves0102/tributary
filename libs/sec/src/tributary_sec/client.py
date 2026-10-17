"""HTTP client for SEC endpoints: declared User-Agent and rate limiting on every request."""

import httpx

from tributary_sec.ratelimit import RateLimiter

# SEC's limit is 10 requests/second per IP across everything we run; each process takes half.
MAX_REQUESTS_PER_SECOND = 5


def sec_client(
    user_agent: str,
    limiter: RateLimiter | None = None,
    timeout: float = 20.0,
) -> httpx.Client:
    """The SEC returns 403 without a declared User-Agent ("<app> <contact email>")."""
    limiter = limiter or RateLimiter(MAX_REQUESTS_PER_SECOND)
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
        event_hooks={"request": [lambda _req: limiter.wait()]},
    )
