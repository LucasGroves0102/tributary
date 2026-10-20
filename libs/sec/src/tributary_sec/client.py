"""HTTP client for SEC endpoints: declared User-Agent and the shared rate limit on every
request."""

from typing import Any, Protocol

import httpx
from prometheus_client import Counter

from tributary_sec.shared import limiter_from_env

REQUESTS = Counter("tributary_sec_requests_total",
                   "SEC requests by host and final HTTP status, or transport_error",
                   ["host", "status"])

# The SEC's 403 page when an IP goes over 10 requests/second. Other 403s (missing User-Agent,
# missing files on www.sec.gov/Archives, days without a daily index) are not throttles.
RATE_EXCEEDED_MARKER = b"request rate threshold exceeded"


class Limiter(Protocol):
    def wait(self) -> None: ...


def is_throttle(response: httpx.Response) -> bool:
    """429, or the SEC's "Request Rate Threshold Exceeded" 403 page."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    response.read()  # error pages are small; a streamed caller can still iterate it afterwards
    return RATE_EXCEEDED_MARKER in response.content.lower()


class SecClient(httpx.Client):
    """httpx.Client that counts every request in REQUESTS and reports throttles to the
    limiter (if it has throttled()), which starts a cooldown."""

    def __init__(self, limiter: Limiter, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.limiter = limiter

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            response = super().send(request, **kwargs)
        except httpx.TransportError:
            REQUESTS.labels(request.url.host, "transport_error").inc()
            raise
        REQUESTS.labels(request.url.host, str(response.status_code)).inc()
        throttled = getattr(self.limiter, "throttled", None)
        if throttled is not None and is_throttle(response):
            throttled()
        return response


def sec_client(
    user_agent: str,
    limiter: Limiter | None = None,
    timeout: float = 20.0,
) -> SecClient:
    """The SEC returns 403 without a declared User-Agent ("<app> <contact email>").

    The default limiter is the shared one (`limiter_from_env`): SEC_CALLER_CLASS and
    SEC_LIMITER_DATABASE_URL / DATABASE_URL from the environment."""
    limiter = limiter or limiter_from_env()
    return SecClient(
        limiter,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout,
        event_hooks={"request": [lambda _req: limiter.wait()]},
    )
