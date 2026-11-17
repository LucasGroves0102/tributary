# Python client

`tributary_client.py`: Tributary's API from Python in one file, the standard library only (Python
3.10+). Copy it beside your script; there is nothing to install. Make a key in Tributary's
Settings, API keys (shown once), and put it in `TRIBUTARY_API_KEY`.

```python
from tributary_client import Tributary

t = Tributary()  # TRIBUTARY_API_KEY; TRIBUTARY_BASE_URL or Tributary's own site
nvda = t.search("NVDA")[0]["company_id"]
for row in t.financials(nvda, metrics=["revenue"], quarters=8)["rows"]:
    print(row["period_end"], row["value"], row["citation"]["accession_no"])

risks = t.get(f"/v1/companies/{nvda}/sections/1A", form="10-K")  # any route the key may use
t.watch(nvda)  # a write key only
```

- Every method is one Query API route ([API keys](../../services/query-api/README.md#api-keys)
  lists what a read and a write key reach); `get`, `post`, `put`, `patch` and `delete` reach any of
  them. JSON comes back parsed, `format=csv` as text. The machine-readable list is
  `GET /api/query/v1/openapi-keys.json`, and the web app's "Using the API" page (`#/api`) renders it.
- Over a limit (429: 60 requests a minute, 10,000 a day per key) it waits for `Retry-After` and
  tries again, `retries` times (2) when the wait is at most `max_wait` seconds (60). Any other
  refusal raises `TributaryError` with `status`, `detail` (the API's words) and `retry_after`.
- The key goes only in the `Authorization` header, only to `base_url`, which must be https (or http
  to this machine); a redirect is never followed (it would carry the key elsewhere). `repr()` and
  errors show the key's first 8 characters at most.

In a spreadsheet instead: Tributary's Excel add-in and Google Sheets script (the "Using the API"
page, "In your spreadsheet") take the same key. Their route, `POST /v1/sheets/values`, carries up to
200 figures in one request and is open to this client too:
`t.post("/v1/sheets/values", {"requests": [{"company": "NVDA", "metric": "revenue"}]})`.

Tests: `tests/test_tributary_client.py`, against a fake API on a local port (no network), run by
the root `make test` (`pytest` collects `clients/`). Packaging it (`pip install tributary-client`)
is for later.
