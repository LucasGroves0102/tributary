# tributary-sec

Shared SEC HTTP client for every service that calls EDGAR or data.sec.gov (poller,
company-loader, facts-fetcher, doc-fetcher).

`sec_client(user_agent, limiter=None, timeout=20.0)` returns an `httpx.Client` that sends the
declared User-Agent (`"<app> <contact email>"`; the SEC returns 403 without one) and waits on a
rate limiter before every request, so callers can't forget either. The default limiter allows
5 requests/second per process, half of the SEC's 10/second per IP. `RateLimiter` enforces a
minimum interval between calls and takes an injectable clock and sleep.

Every request is counted in `tributary_sec_requests_total{host, status}`, where `status` is
the final HTTP status or `transport_error`.

```python
from tributary_sec import sec_client

with sec_client(os.environ["SEC_USER_AGENT"]) as client:
    resp = client.get("https://data.sec.gov/submissions/CIK0001045810.json")
```

Tests: `pytest libs/sec` (fake clock and sleep; no network).
