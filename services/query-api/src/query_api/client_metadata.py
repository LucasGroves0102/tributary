"""Client ID metadata documents (the MCP authorization spec's preferred client registration;
draft-ietf-oauth-client-id-metadata-document): an app's `client_id` is the https URL of a JSON
document it publishes (`client_id`, `client_name`, `redirect_uris`), which the authorization
server fetches (query_api/oauth.py). The URL comes from an unauthenticated request, so this is
SSRF-sensitive like webhooks.py, and fetches go through the same rules:

- URL (`document_url`): https only, a host name (no literal address), port 443, a path other
  than "/", no user info, fragment or dot segments, at most MAX_URL characters; webhooks.py's
  `parse_url` then refuses names that only make sense inside a network.
- Address: webhooks.py's `vet`: the name resolved as an absolute name, every answer a public
  address, and the connection made to the address just checked (no second lookup), TLS verified
  against the name.
- One GET, no redirects (a 3xx is refused), a 200 with a JSON content type, at most
  MAX_DOCUMENT bytes read, the whole fetch (lookup included) inside TOTAL_TIMEOUT on a worker
  thread whose socket is cut when time is up.
- `Cache-Control: max-age` is returned for the caller's cache (oauth.py clamps it); nothing here
  caches or logs the document. A failure is a short code (DocumentRefused), never the URL.

`parse_document` checks the document: JSON, `client_id` exactly the URL it came from, 1 to 10
`redirect_uris` each passing oauth.py's redirect rule, a `client_name` (else the host is the
name). An app identified this way is a public client (PKCE, no secret): any authentication
method the document names is ignored."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from query_api.webhooks import (
    CONNECT_TIMEOUT,
    Opener,
    Resolver,
    Target,
    WebhookRefused,
    _abort,
    _open,
    parse_url,
    vet,
)

MAX_URL = 2048
MAX_DOCUMENT = 8 * 1024
TOTAL_TIMEOUT = 5.0
MAX_REDIRECT_URIS = 10
NAME_MAX = 100
USER_AGENT = "Tributary-OAuth/1"
_MAX_AGE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d{1,9})\s*(?:,|$)", re.I)


class DocumentRefused(Exception):
    """A document that can't be used. `code` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Fetched:
    body: bytes
    max_age: int | None  # seconds, from Cache-Control


@dataclass(frozen=True)
class Document:
    name: str
    redirect_uris: tuple[str, ...]
    client_uri: str | None


class DocumentFetcher(Protocol):
    def fetch(self, url: str) -> Fetched: ...


def document_url(value: str) -> bool:
    """Whether a client_id is a metadata document URL this server will fetch (the rules above
    that need no lookup); False for anything else (a registered client's id, a bad URL)."""
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > MAX_URL:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if (parts.fragment or "@" in parts.netloc or port not in (None, 443) or not parts.hostname
            or parts.path in ("", "/") or not (parts.path + parts.query).isascii()):
        return False
    if any(seg in (".", "..") for seg in parts.path.split("/")):
        return False
    try:
        target = parse_url(value)  # webhooks.py: no names that only make sense inside a network
    except WebhookRefused:
        return False
    try:
        ipaddress.ip_address(target.host)
    except ValueError:
        return True
    return False  # a literal address names no publisher


def max_age_of(cache_control: str | None) -> int | None:
    m = _MAX_AGE.search(cache_control or "")
    return int(m.group(1)) if m else None


def clean_name(value: object, fallback: str) -> str:
    """A name to show: control and format characters dropped, spaces collapsed, at most
    NAME_MAX characters; `fallback` when nothing is left."""
    if not isinstance(value, str):
        return fallback
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c
                   for c in value)
    text = " ".join(text.split())[:NAME_MAX].strip()
    return text or fallback


def parse_document(url: str, raw: bytes, redirect_ok: Callable[[str], bool]) -> Document:
    """The document's client, or DocumentRefused. `redirect_ok(uri)` is oauth.py's redirect rule
    (True for a redirect URI it accepts)."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise DocumentRefused("not_json") from None
    if not isinstance(doc, dict):
        raise DocumentRefused("not_json")
    if doc.get("client_id") != url:
        raise DocumentRefused("client_id")
    uris = doc.get("redirect_uris")
    if (not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECT_URIS
            or not all(isinstance(u, str) and redirect_ok(u) for u in uris)):
        raise DocumentRefused("redirect_uris")
    host = urlsplit(url).hostname or "app"
    client_uri = doc.get("client_uri")
    if not (isinstance(client_uri, str) and client_uri.startswith("https://")
            and len(client_uri) <= MAX_URL):
        client_uri = None
    return Document(clean_name(doc.get("client_name"), host), tuple(dict.fromkeys(uris)),
                    client_uri)


class HttpsDocumentFetcher:
    """The one way a metadata document is fetched (the rules above). `resolver` and `opener` are
    injected in tests; nothing here retries or caches."""

    def __init__(self, resolver: Resolver = socket.getaddrinfo, opener: Opener = _open,
                 total_timeout: float = TOTAL_TIMEOUT) -> None:
        self._resolve, self._open, self._total = resolver, opener, total_timeout

    def fetch(self, url: str) -> Fetched:
        if not document_url(url):
            raise DocumentRefused("url")
        state: dict = {"conn": None, "cancelled": False, "result": None}
        lock = threading.Lock()

        def work() -> None:
            try:
                target, address = vet(url, self._resolve)
            except WebhookRefused as e:
                state["result"] = DocumentRefused(e.code)
                return
            except Exception:
                state["result"] = DocumentRefused("unresolvable")
                return
            with lock:
                if state["cancelled"]:
                    return
                state["conn"] = conn = self._open(target, address, CONNECT_TIMEOUT)
            state["result"] = _get(conn, target, lambda: state["cancelled"])

        worker = threading.Thread(target=work, name="client-metadata-fetch", daemon=True)
        worker.start()
        worker.join(self._total)
        if worker.is_alive() or state["result"] is None:
            with lock:
                state["cancelled"] = True
                conn = state["conn"]
            if conn is not None:
                _abort(conn)
                worker.join(1.0)
            raise DocumentRefused("timeout")
        result = state["result"]
        if isinstance(result, DocumentRefused):
            raise result
        return result


def _get(conn: http.client.HTTPConnection, target: Target,
         cancelled: Callable[[], bool]) -> "Fetched | DocumentRefused":
    """One GET on the worker thread; never raises."""
    try:
        conn.connect()
        if cancelled():
            return DocumentRefused("timeout")
        conn.request("GET", target.path, headers={"Accept": "application/json",
                                                  "User-Agent": USER_AGENT})
        response = conn.getresponse()
        status = response.status
        kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        cache = response.getheader("Cache-Control")
        body = response.read(MAX_DOCUMENT + 1)
    except TimeoutError:
        return DocumentRefused("timeout")
    except ssl.SSLError:
        return DocumentRefused("tls")
    except (http.client.HTTPException, OSError, UnicodeError, ValueError):
        return DocumentRefused("connect")
    except Exception:
        return DocumentRefused("fetch_failed")
    finally:
        conn.close()
    if 300 <= status < 400:
        return DocumentRefused("redirect")
    if status != 200:
        return DocumentRefused("status")
    if kind != "application/json" and not kind.endswith("+json"):
        return DocumentRefused("content_type")
    if len(body) > MAX_DOCUMENT:
        return DocumentRefused("too_large")
    return Fetched(body, max_age_of(cache))
