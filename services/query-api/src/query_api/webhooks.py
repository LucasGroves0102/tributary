"""Webhooks for filing alerts (F6): a URL a user gives, where their alerts are also sent (Discord,
Slack or any HTTPS endpoint). The URL is the user's input and the server makes the request, so
this module is SSRF-sensitive; everything that sends goes through `HttpsSender`, here.

Rules (the spec; README "Filing alerts"; test_webhooks.py):

- URL: `https://` only; a host name or a literal address (no IPv6 zone), no user info, no
  fragment, port 443 only, an ASCII path and query (percent-encode the rest), at most MAX_URL
  characters; stored as `normalized` (scheme and host in lower case). Names that only make sense
  inside a network (localhost and `*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`,
  `*.svc`, `*.cluster.local`, a name without a dot) are refused before any lookup.
- Addresses: the name is resolved as an absolute name (a trailing dot: the pod's search
  domains never apply, so a cluster service's short name doesn't resolve), every A and AAAA
  answer, and refused when ANY answer isn't a public address: private, loopback, link-local
  (169.254.0.0/16 and fe80::/10, where cloud metadata lives: 169.254.169.254), shared
  (100.64.0.0/10), reserved, multicast, unspecified, site-local (fec0::/10), IPv4-mapped or
  -compatible IPv6 of any of those, NAT64 (64:ff9b::/96), 6to4 (2002::/16) and Teredo
  (2001::/32). Checked when the webhook is saved and again before every send; the
  connection then goes to the address just checked (no second lookup, so a name can't answer
  public once and private the next time: DNS rebinding). TLS is verified against the name.
- Redirects are never followed: a 3xx is a failed delivery ("redirect"). Delivered = a 2xx.
- Size and time: a request body of at most MAX_BODY bytes; at most MAX_RESPONSE bytes of the
  answer are read (and dropped); CONNECT_TIMEOUT for each socket operation, and a hard
  TOTAL_TIMEOUT for the whole send, the name's lookup and address check included: it all runs on
  a worker thread and the socket is shut down when time is up, so a slow resolver, an endpoint
  trickling bytes or endless 100 Continues can't hold the caller longer ("dns_timeout" when the
  name hadn't resolved by then, else "timeout"; after the deadline nothing is connected or sent).
  Any failure is a short code; nothing raises out of `send`.
- Signed: `X-Tributary-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>` with
  `X-Tributary-Timestamp` (Unix seconds), `X-Tributary-Delivery` (the delivery's id) and
  `X-Tributary-Event`. A receiver recomputes it with its secret and rejects old timestamps.
- The secret is never stored: `webhook_secret` derives it from the Query API's JWT secret, the
  webhook's id and its random `secret_salt` (HMAC-SHA256 with its own label, so it says nothing
  about the JWT secret). It is shown once, when the webhook is created or rotated; nothing logs
  or returns it otherwise. Rotating the JWT secret changes every webhook's secret.
- Never logged: the URL (a Discord or Slack URL carries its token: `mask` shows the host and the
  last 4 characters), the secret, the payload or a response body. Failures are short codes.
"""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

MAX_URL = 2048
MAX_BODY = 16 * 1024
MAX_RESPONSE = 4 * 1024
CONNECT_TIMEOUT = 3.0
TOTAL_TIMEOUT = 8.0
PORT = 443
USER_AGENT = "Tributary-Webhooks/1"
FORMATS = ("json", "discord", "slack")
SECRET_LABEL = b"tributary webhook secret v1\x00"

_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".svc",
                      ".cluster.local", ".intranet", ".corp")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                   r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_6TO4 = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


class WebhookRefused(ValueError):
    """A URL or address that may not be sent to. `code` is short and safe to store; the message
    is for the user and never repeats the URL."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Target:
    host: str   # lower case, IDNA-encoded; an IPv6 literal without brackets
    port: int
    path: str   # path and query, at least "/"


def parse_url(url: str) -> Target:
    """The URL's parts, or WebhookRefused: the rules above that need no lookup."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL:
        raise WebhookRefused("url", f"give an https:// URL of at most {MAX_URL} characters")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise WebhookRefused("url", "the URL has spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebhookRefused("url", "that isn't a valid URL") from None
    if parts.scheme.lower() != "https":
        raise WebhookRefused("scheme", "only https:// URLs can receive alerts")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise WebhookRefused("userinfo", "the URL can't carry a user name or password")
    if parts.fragment:
        raise WebhookRefused("url", "the URL can't have a #fragment")
    if port not in (None, PORT):
        raise WebhookRefused("port", "alerts are sent to port 443 only")
    if "%" in parts.netloc:
        raise WebhookRefused("host", "the host can't carry a % (an IPv6 zone)")
    if not (parts.path + parts.query).isascii():
        raise WebhookRefused("url", "percent-encode the URL's path (it has non-ASCII characters)")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise WebhookRefused("host", "the URL has no host")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise WebhookRefused("host", "the host name isn't valid") from None
        if (not _HOST.match(host) or host == "localhost"
                or any(host.endswith(s) for s in _INTERNAL_SUFFIXES)):
            raise WebhookRefused("private_address", "that host is on a private network")
    else:
        refuse_address(str(literal))
        host = str(literal)
    path = parts.path or "/"
    if parts.query:
        path += f"?{parts.query}"
    return Target(host, PORT, path)


def normalized(url: str) -> str:
    """The URL as stored and sent: scheme and host in lower case, path and query as given."""
    parts = urlsplit(url)
    return urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))


def public(address: str) -> bool:
    """Whether an address may be sent to: a public unicast address (the rules above)."""
    if "%" in address:  # a zone: link-scoped by definition
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in _NAT64 or ip in _NAT64_LOCAL or ip in _6TO4 or ip in _TEREDO:
            return False
        mapped = ip.ipv4_mapped
        if mapped is None and ip.packed[:12] == bytes(12) and int(ip) > 1:
            mapped = ipaddress.IPv4Address(ip.packed[12:])  # ::a.b.c.d (IPv4-compatible)
        if mapped is not None:
            return public(str(mapped))
        if ip.is_site_local:
            return False
    return bool(ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local
                                      or ip.is_multicast or ip.is_reserved
                                      or ip.is_unspecified))


def refuse_address(address: str) -> None:
    if not public(address):
        raise WebhookRefused("private_address",
                             "that address is private, local or reserved; alerts go only to "
                             "public internet addresses")


Resolver = Callable[..., list[tuple[Any, ...]]]


def resolve(target: Target, resolver: Resolver = socket.getaddrinfo) -> list[str]:
    """Every address the host answers (a literal is its own answer), each checked; the first is
    the one to connect to. WebhookRefused when any answer isn't public or nothing answers."""
    try:
        ipaddress.ip_address(target.host)
        addresses = [target.host]
    except ValueError:
        try:  # absolute: no search domain (a cluster's service names) is ever tried
            infos = resolver(target.host + ".", target.port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            raise WebhookRefused("unresolvable", "that host name doesn't resolve") from None
        addresses = list(dict.fromkeys(str(i[4][0]) for i in infos))
    if not addresses:
        raise WebhookRefused("unresolvable", "that host name doesn't resolve")
    for a in addresses:
        refuse_address(a)
    return addresses


def vet(url: str, resolver: Resolver = socket.getaddrinfo) -> tuple[Target, str]:
    """parse_url + resolve: the target and the checked address to connect to."""
    target = parse_url(url)
    return target, resolve(target, resolver)[0]


def mask(url: str) -> str:
    """What the user sees of a saved URL: scheme and host, then the last 4 characters."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
    except ValueError:
        host = "?"
    tail = url[-4:] if len(url) > 12 else ""
    return f"https://{host}/…{tail}"


def suggest_format(url: str) -> str:
    """discord or slack by the URL's host, else json."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "json"
    host = (parts.hostname or "").lower()
    if host in ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com") \
            and parts.path.startswith("/api/webhooks/"):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    return "json"


# --- secrets and signatures -------------------------------------------------------------------

def new_salt() -> bytes:
    return secrets.token_bytes(16)


def webhook_secret(jwt_secret: str, webhook_id: str, salt: bytes) -> str:
    """The webhook's signing secret: derived, never stored (module docstring)."""
    mac = hmac.new(jwt_secret.encode(), SECRET_LABEL + uuid.UUID(webhook_id).bytes + salt,
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def signature(secret: str, timestamp: int, body: bytes) -> str:
    return "v1=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                            hashlib.sha256).hexdigest()


def body_of(fmt: str, event: dict, text: str) -> bytes:
    """The request body for a webhook's format: our JSON event, or the short text a Discord or
    Slack channel shows (no mentions: a company name can't ping anyone)."""
    if fmt == "discord":
        payload: dict[str, Any] = {"username": "Tributary", "content": text[:1900],
                                   "allowed_mentions": {"parse": []}}
    elif fmt == "slack":
        payload = {"text": text.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;")[:3000]}
    else:
        payload = event
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if len(raw) > MAX_BODY:  # our own event is bounded; this only trims a runaway text
        payload = {**payload, "content" if fmt == "discord" else "text": text[:500]} \
            if fmt != "json" else {k: v for k, v in event.items() if k != "figures"}
        raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return raw


def headers(secret: str, delivery_id: str, event: str, body: bytes,
            now: Callable[[], float] = time.time) -> dict[str, str]:
    ts = int(now())
    return {"Content-Type": "application/json", "User-Agent": USER_AGENT,
            "X-Tributary-Event": event, "X-Tributary-Delivery": delivery_id,
            "X-Tributary-Timestamp": str(ts), "X-Tributary-Signature": signature(secret, ts, body)}


# --- sending ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sent:
    ok: bool
    status: int | None = None
    error: str | None = None  # a short code ('timeout', 'redirect', 'http_500', ...)


class Sender(Protocol):
    def send(self, url: str, body: bytes, headers: dict[str, str]) -> Sent: ...


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to `host` at an address already checked: no second lookup, TLS verified against
    the name (SNI and certificate)."""

    def __init__(self, host: str, address: str, port: int, timeout: float,
                 context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._ctx = context

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.raw = sock  # so a deadline can cut the TLS handshake too
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


Opener = Callable[[Target, str, float], http.client.HTTPConnection]


def _open(target: Target, address: str, timeout: float) -> http.client.HTTPConnection:
    return _PinnedHTTPS(target.host, address, target.port, timeout, ssl.create_default_context())


def _abort(conn: http.client.HTTPConnection) -> None:
    """Cut a send that ran out of time: shut the sockets down so the worker's blocked read ends."""
    for sock in (getattr(conn, "raw", None), conn.sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    conn.close()


class HttpsSender:
    """The one way a webhook is called (the rules above). `resolver` and `opener` are injected
    in tests; nothing here retries (the job's delivery log does, with backoff)."""

    def __init__(self, resolver: Resolver = socket.getaddrinfo, opener: Opener = _open,
                 total_timeout: float = TOTAL_TIMEOUT) -> None:
        self._resolve, self._open, self._total = resolver, opener, total_timeout

    def send(self, url: str, body: bytes, headers: dict[str, str]) -> Sent:
        """The whole send, the name's lookup and address check included, runs on a worker
        thread inside the hard total timeout: a slow resolver can't hold the job longer than an
        endpoint can (review 2026-11-09). Past the deadline: "dns_timeout" if the name hadn't
        resolved yet, else "timeout"; nothing is connected or sent after it."""
        if len(body) > MAX_BODY:
            return Sent(False, None, "too_large")
        attempt = _Attempt()

        def work() -> None:
            attempt.result = attempt.run(lambda: vet(url, self._resolve), self._open,
                                         body, headers)
        worker = threading.Thread(target=work, name="webhook-send", daemon=True)
        worker.start()
        worker.join(self._total)
        if worker.is_alive() or attempt.result is None:
            resolved, conn = attempt.cancel()
            if conn is not None:
                _abort(conn)
                worker.join(1.0)  # a cut socket ends the worker's read at once
            # a lookup can't be cut: its daemon thread ends with the resolver's own timeout,
            # and opens nothing then (_Attempt)
            return Sent(False, None, "timeout" if resolved else "dns_timeout")
        return attempt.result


class _Attempt:
    """One send's state between HttpsSender.send and its worker: once cancelled (the deadline
    passed), the worker opens no connection and sends nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancelled = False
        self.resolved = False
        self.conn: http.client.HTTPConnection | None = None
        self.result: Sent | None = None

    def cancel(self) -> tuple[bool, http.client.HTTPConnection | None]:
        with self._lock:
            self.cancelled = True
            return self.resolved, self.conn

    def run(self, check: Callable[[], tuple[Target, str]], opener: Opener, body: bytes,
            headers: dict[str, str]) -> Sent:
        try:
            target, address = check()
        except WebhookRefused as e:
            return Sent(False, None, e.code)
        except Exception:  # a resolver that fails some other way: nothing raises out of send
            return Sent(False, None, "unresolvable")
        with self._lock:
            if self.cancelled:
                return Sent(False, None, "dns_timeout")
            self.resolved = True
            self.conn = conn = opener(target, address, CONNECT_TIMEOUT)
        return _exchange(conn, target, body, headers, lambda: self.cancelled)


def _exchange(conn: http.client.HTTPConnection, target: Target, body: bytes,
              headers: dict[str, str], cancelled: Callable[[], bool] = lambda: False) -> Sent:
    """One POST and its status (on HttpsSender's worker thread); never raises. Nothing is sent
    once `cancelled()` (the deadline cut a connect that was still under way)."""
    try:
        conn.connect()
        if cancelled():
            return Sent(False, None, "timeout")
        conn.request("POST", target.path, body=body, headers=headers)
        response = conn.getresponse()
        status = response.status
        response.read(MAX_RESPONSE)  # bounded; the rest is never read
    except TimeoutError:
        return Sent(False, None, "timeout")
    except ssl.SSLError:
        return Sent(False, None, "tls")
    except http.client.HTTPException:
        return Sent(False, None, "bad_response")
    except OSError:
        return Sent(False, None, "connect")
    except (UnicodeError, ValueError):
        return Sent(False, None, "bad_url")
    except Exception:
        return Sent(False, None, "send_failed")
    finally:
        conn.close()
    if not 100 <= status <= 599:  # not an HTTP status: nothing of it is kept
        return Sent(False, None, "bad_response")
    if 200 <= status < 300:
        return Sent(True, status)
    if 300 <= status < 400:
        return Sent(False, status, "redirect")
    return Sent(False, status, f"http_{status}")
