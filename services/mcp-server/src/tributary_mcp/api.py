"""The Query API, called as the key's user: the only data source the MCP server has.

Every call sends the caller's own `Authorization: Bearer trb_...` (from the MCP request carrying the
tool call) and the request id, nothing else that identifies anyone. The Query API decides who that
is, refuses routes and scopes a key can't use, and applies OpenFGA and row-level security, exactly
as for the web app (query_api/api_keys.py). This server holds no credentials of its own.

Answers other than 200 become `ApiError` with a message written for the model: what happened and
what to do (401: the key; 403: no access, don't answer from elsewhere; 404; 422: what to change;
429: when to try again, from Retry-After; 5xx and no answer: Tributary is unavailable)."""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("tributary_mcp.api")

DETAIL_CHARS = 1000  # of the Query API's `detail` in an error message
USER_AGENT = "tributary-mcp-server"


@dataclass(frozen=True)
class Caller:
    """Who a call is made for: the request's Authorization value and its request id."""
    authorization: str
    request_id: str | None = None


class ApiError(Exception):
    """A Query API answer that isn't a success. `str()` is the message for the model; it never
    holds the key."""

    def __init__(self, message: str, status: int | None, outcome: str) -> None:
        super().__init__(message)
        self.status, self.outcome = status, outcome


def detail_text(detail: Any) -> str:
    """The Query API's `detail` in words: a string as is; FastAPI's validation list as
    "where: what"; the formula routes' {"errors": [...]} as each message and hint."""
    if isinstance(detail, str):
        text = detail
    elif isinstance(detail, list):
        text = "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', [])[1:]) or 'request'}: {d.get('msg', '')}"
            if isinstance(d, dict) else str(d) for d in detail)
    elif isinstance(detail, dict) and isinstance(detail.get("errors"), list):
        text = "; ".join(
            f"{e.get('message', '')}{' (' + e['hint'] + ')' if e.get('hint') else ''}"
            if isinstance(e, dict) else str(e) for e in detail["errors"])
    else:
        text = json.dumps(detail, default=str)
    return text[:DETAIL_CHARS]


def refusal(status: int, detail: str, retry_after: str | None) -> ApiError:
    """The message a model gets for a Query API status."""
    said = f": {detail}" if detail else ""
    if status == 401:
        return ApiError("Tributary refused the API key (unknown, expired or revoked). Tell the "
                        "user to make a new key in Tributary's Settings, API keys, and update "
                        "this connection.", status, "refused_key")
    if status == 403:
        return ApiError(f"Permission denied{said}. The user's plan, grants or API key scope don't "
                        "allow this; say so, and don't answer it from other sources.", status,
                        "denied")
    if status == 404:
        return ApiError(f"Not found{said}.", status, "not_found")
    if status == 422:
        return ApiError(f"Tributary didn't take these arguments{said}. Change them and try again.",
                        status, "invalid")
    if status == 429:
        wait = retry_after if retry_after and retry_after.isdigit() else None
        when = f"Try again in {wait} seconds (Retry-After: {wait})." if wait else "Try again later."
        return ApiError(f"Rate limit reached for this API key{said}. {when}", status, "limited")
    if status >= 500:
        return ApiError(f"Tributary is unavailable right now (status {status}{said}). Try again in "
                        "a moment, or ask for less.", status, "unavailable")
    return ApiError(f"Tributary answered with status {status}{said}.", status, "error")


class QueryApi:
    """Async calls to the Query API as the caller. `http` carries the base URL and timeouts (tests
    pass one over httpx.MockTransport); the caller's headers are added per request, never kept."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    def _headers(self, caller: Caller) -> dict[str, str]:
        headers = {"Authorization": caller.authorization, "User-Agent": USER_AGENT}
        if caller.request_id:
            headers["X-Request-ID"] = caller.request_id
        return headers

    async def request(self, caller: Caller, method: str, path: str, *,
                      params: dict[str, Any] | None = None, body: Any = None) -> Any:
        """The JSON answer of one call (`path` is built only from validated values), or ApiError."""
        try:
            r = await self.http.request(method, path, params=_query(params), json=body,
                                        headers=self._headers(caller))
        except httpx.TimeoutException:
            raise ApiError("Tributary took too long to answer. Try again, or ask for less.", None,
                           "unavailable") from None
        except httpx.HTTPError as e:
            log.warning("query api unreachable: %s", type(e).__name__,
                        extra={"request_id": caller.request_id})
            raise ApiError("Tributary's data service isn't answering right now. Try again in a "
                           "moment.", None, "unavailable") from None
        if r.status_code == 200 or r.status_code == 201:
            try:
                return r.json()
            except ValueError:
                raise ApiError("Tributary sent an answer that isn't JSON.", r.status_code,
                               "error") from None
        try:
            detail = detail_text(r.json().get("detail", ""))
        except (ValueError, AttributeError):
            detail = ""
        raise refusal(r.status_code, detail, r.headers.get("retry-after"))

    async def get(self, caller: Caller, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request(caller, "GET", path, params=params)

    async def post(self, caller: Caller, path: str, body: Any) -> Any:
        return await self.request(caller, "POST", path, body=body)

    async def key_check(self, authorization: str, request_id: str | None) -> bool | None:
        """Does the Query API take this key? GET /v1/me (one request of the key's limit). True:
        yes; False: 401; None: it couldn't say (another status, unreachable)."""
        try:
            r = await self.http.get("/v1/me", headers=self._headers(
                Caller(authorization, request_id)))
        except httpx.HTTPError:
            return None
        if r.status_code == 401:
            return False
        return True if r.status_code == 200 else None


def _query(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Query parameters without the ones left unset; booleans as the API reads them."""
    if not params:
        return None
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == []:
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else v
    return out
