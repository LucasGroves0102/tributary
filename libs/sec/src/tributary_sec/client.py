"""HTTP client for SEC endpoints: declared User-Agent and rate limiting on every request."""

from typing import Any

import httpx
from prometheus_client import Counter

from tributary_sec.ratelimit import RateLimiter

# SEC's limit is 10 requests/second per IP across everything we run; each process takes half.
MAX_REQUESTS_PER_SECOND = 5

REQUESTS = Counter("tributary_sec_requests_total",
                   "SEC requests by host and final HTTP status, or transport_error",
                   ["host", "status"])


class SecClient(httpx.Client):
    """httpx.Client that counts every request in REQUESTS."""

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            response = super().send(request, **kwargs)
        except httpx.TransportError:
            REQUESTS.labels(request.url.host, "transport_error").inc()
            raise
        REQUESTS.labels(request.url.host, str(response.status_code)).inc()
        return response


def sec_client(
    user_agent: str,
    limiter: RateLimiter | None = None,
    timeout: float = 20.0,
) -> httpx.Client:
    """The SEC returns 403 without a declared User-Agent ("<app> <contact email>")."""
    limiter = limiter or RateLimiter(MAX_REQUESTS_PER_SECOND)
    return SecClient(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
        event_hooks={"request": [lambda _req: limiter.wait()]},
    )
