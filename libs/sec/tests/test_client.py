import httpx

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
