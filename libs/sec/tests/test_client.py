import httpx
import pytest
from prometheus_client import REGISTRY

from tributary_sec import RateLimiter, sec_client


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def test_sends_user_agent_and_waits_on_limiter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["User-Agent"]
        return httpx.Response(200)

    limiter = CountingLimiter()
    client = sec_client("Tributary test@example.com", limiter=limiter)
    client._transport = httpx.MockTransport(handler)
    client.get("https://www.sec.gov/a")
    client.get("https://www.sec.gov/b")
    assert seen["ua"] == "Tributary test@example.com"
    assert limiter.calls == 2


def sec_requests(host, status):
    return REGISTRY.get_sample_value("tributary_sec_requests_total",
                                     {"host": host, "status": status}) or 0


def test_counts_requests_by_host_and_status():
    statuses = iter([200, 403])
    client = sec_client("Tributary test@example.com", limiter=CountingLimiter())
    client._transport = httpx.MockTransport(lambda _req: httpx.Response(next(statuses)))
    ok, forbidden = sec_requests("data.sec.gov", "200"), sec_requests("data.sec.gov", "403")
    client.get("https://data.sec.gov/a")
    client.get("https://data.sec.gov/b")
    assert sec_requests("data.sec.gov", "200") == ok + 1
    assert sec_requests("data.sec.gov", "403") == forbidden + 1


def test_counts_transport_errors():
    def fail(request):
        raise httpx.ConnectError("refused", request=request)

    client = sec_client("Tributary test@example.com", limiter=CountingLimiter())
    client._transport = httpx.MockTransport(fail)
    before = sec_requests("www.sec.gov", "transport_error")
    with pytest.raises(httpx.ConnectError):
        client.get("https://www.sec.gov/a")
    assert sec_requests("www.sec.gov", "transport_error") == before + 1


class ThrottleLimiter(CountingLimiter):
    def __init__(self) -> None:
        super().__init__()
        self.throttles = 0

    def throttled(self) -> None:
        self.throttles += 1


@pytest.mark.parametrize("status,body,throttle", [
    (429, b"", True),
    (403, b"<title>SEC.gov | Request Rate Threshold Exceeded</title>", True),
    (403, b"<title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title>",
     False),
    (403, b"<Error><Code>AccessDenied</Code></Error>", False),  # missing Archives file
    (200, b"Request Rate Threshold Exceeded", False),
    (500, b"", False),
])
def test_reports_throttles_to_the_limiter(status, body, throttle):
    limiter = ThrottleLimiter()
    client = sec_client("Tributary test@example.com", limiter=limiter)
    client._transport = httpx.MockTransport(lambda _req: httpx.Response(status, content=body))
    client.get("https://www.sec.gov/a")
    assert limiter.throttles == (1 if throttle else 0)


def test_streamed_throttle_page_stays_readable():
    limiter = ThrottleLimiter()
    client = sec_client("Tributary test@example.com", limiter=limiter)
    client._transport = httpx.MockTransport(
        lambda _req: httpx.Response(403, content=b"Request Rate Threshold Exceeded"))
    with client.stream("GET", "https://www.sec.gov/a.zip") as resp:
        assert b"".join(resp.iter_bytes()) == b"Request Rate Threshold Exceeded"
    assert limiter.throttles == 1


def test_default_limiter_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("SEC_LIMITER_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = sec_client("Tributary test@example.com")
    assert isinstance(client.limiter, RateLimiter)
