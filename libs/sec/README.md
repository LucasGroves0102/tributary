# tributary-sec

Shared SEC HTTP client for every service that calls EDGAR or data.sec.gov (poller,
company-loader, facts-fetcher, doc-fetcher).

`sec_client(user_agent, limiter=None, timeout=20.0)` returns an `httpx.Client` that sends the
declared User-Agent (`"<app> <contact email>"`; the SEC returns 403 without one) and waits on a
rate limiter before every request, so callers can't forget either.

`acceptance_time(s)` corrects submissions' `acceptanceDateTime` (data.sec.gov API and
`submissions.zip`), which is labelled UTC but runs ahead of it by the US Eastern offset.

```python
from tributary_sec import sec_client

with sec_client(os.environ["SEC_USER_AGENT"]) as client:
    resp = client.get("https://data.sec.gov/submissions/CIK0001045810.json")
```

## Shared rate limit

The SEC allows 10 requests/second per IP across everything hermes runs. Every process takes
its requests from one token bucket in Postgres (`ops.sec_take_token`,
`db/migrations/019_sec_rate_limit.sql`): one row locked per call, database clock, so no new
infrastructure and every pod agrees. Defaults: 7 tokens/second, capacity 2, so at most 9
requests in any one-second window and 7/s sustained. Tune with
`UPDATE ops.sec_rate_limit` / `ops.sec_rate_classes`; callers pick it up on their next request.

- **Priorities.** Each caller has a class, `SEC_CALLER_CLASS`: `live` (poller, doc-fetcher
  for new filings), `batch` (nightly jobs) or `bulk` (crawls). A class only takes a token if
  its reserve (0, 0.5, 1) is left afterwards, so batch and bulk always leave a token for live,
  and stop entirely while live keeps the bucket low. Bulk still gets the full rate when nothing
  else is asking. Callers of one class race for tokens with jittered waits, which shares them
  evenly.
- **Cooldown.** A 429, or a 403 "Request Rate Threshold Exceeded" page (other 403s, like a
  missing User-Agent or Archives file, are not throttles), calls `ops.sec_report_throttle`:
  the bucket empties and every class waits 10 s, doubling up to 600 s while throttles continue
  within 30 minutes. The response is still returned to the caller.
- **Fallback.** If Postgres can't be reached (or the migration isn't applied), the process
  uses a local 2 req/s limit and retries the bucket every 30 s; a throttle then pauses that
  process for 60 s. Without any database URL the client uses the local limit only.

Config (env): `SEC_CALLER_CLASS` (default `batch`; the Helm chart sets `live` for services and
`batch` for jobs, overridden by `secClass`), `SEC_LIMITER_DATABASE_URL` (default
`DATABASE_URL`). In Helm, `uses: sec` sets both and opens egress to host Postgres; with
`secLimiter.dedicatedLogin` the URL is a login in the `sec_limiter` group (migration 022), which
can run the two bucket functions and read nothing else.

`ops.sec_rate_limit_status` (granted to `ops_reader`) shows the bucket without taking a token.

## Metrics

- `tributary_sec_requests_total{host, status}`: every request, by final HTTP status or
  `transport_error`.
- `tributary_sec_limiter_granted_total{caller_class, mode}` (`shared` or `local` fallback),
  `tributary_sec_limiter_waits_total{caller_class}`,
  `tributary_sec_limiter_wait_seconds_total{caller_class}`.
- `tributary_sec_limiter_throttles_total{caller_class}`, `tributary_sec_limiter_cooling`.
- `tributary_sec_limiter_fallback{caller_class}` (1 while local), and
  `tributary_sec_limiter_fallbacks_total{caller_class}`.
- `tributary_sec_limiter_shared_tokens` and `tributary_sec_limiter_shared_granted` (the
  global granted count as read from Postgres: `rate()` it for the shared request rate).

Tests: `pytest libs/sec` (fake clock, sleep and bucket; no network). `test_shared_db.py` runs
the SQL functions under concurrency when `TEST_DATABASE_URL` is set.
