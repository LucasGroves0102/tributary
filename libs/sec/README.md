# tributary-sec

Shared SEC HTTP client for every service that calls EDGAR or data.sec.gov.

`sec_client(user_agent)` returns an `httpx.Client` that sends the declared User-Agent and
waits on a rate limiter before every request, so callers can't forget either.
