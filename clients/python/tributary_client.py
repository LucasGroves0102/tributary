"""Tributary's Python client: one file, the standard library only (Python 3.10+).

    from tributary_client import Tributary

    t = Tributary()  # the key from TRIBUTARY_API_KEY (make one in Tributary's Settings)
    nvda = t.search("NVDA")[0]["company_id"]
    for row in t.financials(nvda, metrics=["revenue"], quarters=8)["rows"]:
        print(row["period_end"], row["value"], row["citation"]["accession_no"])

Every method is one Query API route; `get`, `post`, `put`, `patch` and `delete` reach any route
your key may use (the "Using the API" page lists them, `/v1/openapi-keys.json` is the same list
for machines). A read key reads data and your saved work; a write key can also create and edit
your saved work and watchlist.

Over a limit (429: 60 requests a minute, 10,000 a day per key) the client waits for the API's
Retry-After and tries again, up to `retries` times when the wait is at most `max_wait` seconds;
any other refusal raises TributaryError with the HTTP status and the API's detail.

The key goes only in the Authorization header, only to `base_url` (https, or http to this
machine), and the client never follows a redirect, which would carry it elsewhere. Nothing here
prints it: repr() and errors show its first 8 characters at most."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = ["DEFAULT_BASE_URL", "Tributary", "TributaryError"]
__version__ = "0.1.0"

DEFAULT_BASE_URL = "https://tributary.lucasgroves.dev/api/query"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
USER_AGENT = f"tributary-client/{__version__} (Python)"


class TributaryError(Exception):
    """A request the API refused or couldn't answer. `status`: the HTTP status (None when the
    API wasn't reached); `detail`: the API's reason; `retry_after`: seconds, on a 429."""

    def __init__(self, status: int | None, detail: str, retry_after: int | None = None):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a 3xx is raised as an error: the key never follows it


def _query(params: Mapping[str, Any] | None) -> str:
    pairs: list[tuple[str, str]] = []
    for name, value in (params or {}).items():
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for v in values:
            pairs.append((name, "true" if v is True else "false" if v is False else str(v)))
    return urllib.parse.urlencode(pairs)


class Tributary:
    """A client for one API key. `key` defaults to TRIBUTARY_API_KEY, `base_url` to
    TRIBUTARY_BASE_URL or Tributary's own site."""

    def __init__(self, key: str | None = None, base_url: str | None = None, *,
                 timeout: float = 30, retries: int = 2, max_wait: float = 60,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        key = key if key is not None else os.environ.get("TRIBUTARY_API_KEY", "")
        if not key.startswith("trb_") or len(key) != 47:
            raise ValueError("an API key is needed (trb_..., 47 characters): pass key= or set "
                             "TRIBUTARY_API_KEY")
        base = (base_url or os.environ.get("TRIBUTARY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        url = urllib.parse.urlsplit(base)
        if not url.hostname or not (url.scheme == "https" or (
                url.scheme == "http" and url.hostname in LOCAL_HOSTS)):
            raise ValueError("base_url must be https:// (http:// only to this machine): "
                             "the key travels with every request")
        self._key = key
        self.base_url = base
        self.timeout, self.retries, self.max_wait, self._sleep = timeout, retries, max_wait, sleep
        self._open = urllib.request.build_opener(_NoRedirects()).open

    def __repr__(self) -> str:
        return f"Tributary(base_url={self.base_url!r}, key={self._key[:8]}...)"

    # --- any route ------------------------------------------------------------------------

    def request(self, method: str, path: str, params: Mapping[str, Any] | None = None,
                body: Any = None) -> Any:
        """One route: `path` like "/v1/me". JSON answers come back parsed, CSV as text."""
        if not path.startswith("/"):
            raise ValueError('path starts with "/", e.g. "/v1/me"')
        query = _query(params)
        url = self.base_url + path + (f"?{query}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, method=method.upper())
            req.add_header("Authorization", f"Bearer {self._key}")
            req.add_header("Accept", "application/json, text/csv")
            req.add_header("User-Agent", USER_AGENT)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with self._open(req, timeout=self.timeout) as r:
                    raw = r.read()
                    kind = r.headers.get("Content-Type", "")
            except urllib.error.HTTPError as e:
                error = self._error(e)
                wait = error.retry_after
                if (e.code == 429 and attempt < self.retries and wait is not None
                        and wait <= self.max_wait):
                    self._sleep(wait)
                    continue
                raise error from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                raise TributaryError(None, f"couldn't reach {self.base_url}: {reason}") from None
            if not raw:
                return None
            return json.loads(raw) if "json" in kind else raw.decode("utf-8")
        raise AssertionError("unreachable")

    def _error(self, e: urllib.error.HTTPError) -> TributaryError:
        try:
            body = e.read()
        except OSError:
            body = b""
        detail: Any = None
        try:
            detail = json.loads(body).get("detail")
        except (ValueError, AttributeError):
            pass
        if not isinstance(detail, str):
            detail = json.dumps(detail) if detail else (e.reason or f"HTTP {e.code}")
        retry = e.headers.get("Retry-After") if e.headers else None
        if 300 <= e.code < 400:
            detail = "the API answered with a redirect, which the client doesn't follow"
        return TributaryError(e.code, str(detail).replace(self._key, self._key[:8] + "..."),
                              int(retry) if retry and retry.isdigit() else None)

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params, body if body is not None else {})

    def put(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("PUT", path, params, body)

    def patch(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("PATCH", path, params, body)

    def delete(self, path: str, **params: Any) -> Any:
        return self.request("DELETE", path, params)

    # --- the routes scripts use most (read) -------------------------------------------------

    def me(self) -> dict:
        """Who the key belongs to, what they can read, and the key's own scopes and expiry."""
        return self.get("/v1/me")

    def search(self, q: str, limit: int = 5) -> list[dict]:
        """Companies by ticker, CIK or name, best match first."""
        return self.get("/v1/companies/search", q=q, limit=limit)["results"]

    def financials(self, company_id: str, metrics: Sequence[str] | None = None,
                   quarters: int = 4, view: str = "quarter", basis: str = "fiscal",
                   csv: bool = False, **params: Any) -> Any:
        """Figures with their citation (filing, tag) and freshness; csv=True: the CSV text."""
        return self.get(f"/v1/companies/{company_id}/financials", metrics=list(metrics or []),
                        quarters=quarters, view=view, basis=basis,
                        format="csv" if csv else None, **params)

    def segments(self, company_id: str, **params: Any) -> dict:
        return self.get(f"/v1/companies/{company_id}/segments", **params)

    def filings(self, company_id: str, limit: int = 8) -> dict:
        """Parsed filings, newest first, with their sections (no text)."""
        return self.get(f"/v1/companies/{company_id}/filings", limit=limit)

    def section(self, company_id: str, section_key: str, **params: Any) -> dict:
        """A section's text and citation: 10-K "1A", 10-Q "II-1A", 8-K "2.02", "EX-99.1"."""
        return self.get(f"/v1/companies/{company_id}/sections/{section_key}", **params)

    def search_text(self, q: str, **params: Any) -> dict:
        """Full-text search over filing sections."""
        return self.get("/v1/search", q=q, **params)

    def screener(self, **params: Any) -> Any:
        return self.get("/v1/screener", **params)

    def evaluate(self, formula: str, default_company: str | None = None,
                 **body: Any) -> dict:
        """A formula's series, e.g. "NVDA.revenue / NVDA.revenue[-4q] - 1"."""
        return self.post("/v1/formulas/evaluate", {
            "formula": formula, **({"default_company": default_company}
                                   if default_company else {}), **body})

    def watchlist(self, **params: Any) -> dict:
        return self.get("/v1/watchlist", **params)

    def workspaces(self) -> list[dict]:
        return self.get("/v1/workspaces")["workspaces"]

    # --- your saved work (write) -----------------------------------------------------------

    def watch(self, company_id: str) -> dict:
        return self.put(f"/v1/watchlist/{company_id}")

    def unwatch(self, company_id: str) -> dict:
        return self.delete(f"/v1/watchlist/{company_id}")

    def create_workspace(self, name: str) -> dict:
        return self.post("/v1/workspaces", {"name": name})

    def add_item(self, workspace_id: str, kind: str, name: str, state: dict) -> dict:
        """A chart, comps set, screen, thesis or model in one of your workspaces."""
        return self.post(f"/v1/workspaces/{workspace_id}/items",
                         {"kind": kind, "name": name, "state": state})
