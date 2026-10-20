import httpx
import pytest
from prometheus_client import REGISTRY

from tributary_sec import sec_client


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
