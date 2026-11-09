"""Webhook rules (query_api/webhooks.py): what a URL may be, which addresses are refused (after
the lookup, and again at send time), that a redirect is never followed, the size and signing
rules. No network: the resolver and the connection are injected."""

import hashlib
import hmac
import json
import socket

import pytest

from query_api import webhooks
from query_api.webhooks import (
    MAX_BODY,
    MAX_RESPONSE,
    HttpsSender,
    Sent,
    WebhookRefused,
    body_of,
    headers,
    mask,
    parse_url,
    public,
    resolve,
    signature,
    suggest_format,
    vet,
    webhook_secret,
)

JWT = "test-secret-" + "x" * 32
HOOK = "3f2b8c1e-7a4d-4e2b-9c1a-0d5e6f7a8b9c"


def answers(*addresses):
    """A resolver that answers these addresses for any name."""
    def resolver(host, port, type=None):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (a, port)) for a in addresses]
    return resolver


@pytest.mark.parametrize("url, code", [
    ("http://169.254.169.254/latest/meta-data/", "scheme"),
    ("http://hooks.example.com/x", "scheme"),
    ("ftp://example.com/x", "scheme"),
    ("https://169.254.169.254/latest/meta-data/", "private_address"),
    ("https://[fd00:ec2::254]/", "private_address"),
    ("https://127.0.0.1/hook", "private_address"),
    ("https://10.43.0.10/hook", "private_address"),
    ("https://192.168.1.1/hook", "private_address"),
    ("https://172.16.5.4/hook", "private_address"),
    ("https://100.64.0.1/hook", "private_address"),
    ("https://0.0.0.0/hook", "private_address"),
    ("https://[::1]/hook", "private_address"),
    ("https://[::ffff:169.254.169.254]/hook", "private_address"),
    ("https://[::ffff:127.0.0.1]/hook", "private_address"),
    ("https://[fe80::1]/hook", "private_address"),
    ("https://[64:ff9b::a9fe:a9fe]/hook", "private_address"),
    ("https://[2002:a9fe:a9fe::1]/hook", "private_address"),
    ("https://localhost/hook", "private_address"),
    ("https://query-api.tributary.svc/v1/me", "private_address"),
    ("https://kubernetes.default.svc.cluster.local/", "private_address"),
    ("https://metadata.google.internal/computeMetadata/v1/", "private_address"),
    ("https://printer.local/x", "private_address"),
    ("https://intranet/x", "private_address"),
    ("https://user:pw@hooks.example.com/x", "userinfo"),
    ("https://hooks.example.com@169.254.169.254/x", "userinfo"),
    ("https://hooks.example.com:8443/x", "port"),
    ("https://hooks.example.com:22/x", "port"),
    ("https://hooks.example.com/x#frag", "url"),
    ("https://hooks.exa mple.com/x", "url"),
    ("https://", "host"),
    ("https://" + "a" * 2050 + ".com/", "url"),
])
def test_urls_refused_before_any_lookup(url, code):
    with pytest.raises(WebhookRefused) as e:
        parse_url(url)
    assert e.value.code == code
    assert url not in str(e.value)  # the message never repeats the URL (it can carry a token)


def test_a_public_https_url_is_taken_apart():
    t = parse_url("https://Discord.com/api/webhooks/123/abc?wait=true")
    assert (t.host, t.port, t.path) == ("discord.com", 443, "/api/webhooks/123/abc?wait=true")
    assert parse_url("https://hooks.example.com:443").path == "/"


@pytest.mark.parametrize("address, ok", [
    ("93.184.216.34", True), ("2606:4700:4700::1111", True), ("8.8.8.8", True),
    ("169.254.169.254", False), ("169.254.0.1", False), ("127.0.0.53", False),
    ("10.0.0.1", False), ("100.100.100.200", False), ("224.0.0.1", False),
    ("255.255.255.255", False), ("::", False), ("::1", False), ("fc00::1", False),
    ("::ffff:10.0.0.1", False), ("::ffff:8.8.8.8", True), ("::169.254.169.254", False),
    ("2001:db8::1", False), ("2001::1", False), ("not an address", False),
])
def test_public_addresses(address, ok):
    assert public(address) is ok


def test_a_name_that_resolves_to_a_private_address_is_refused():
    target = parse_url("https://hooks.example.com/x")
    with pytest.raises(WebhookRefused) as e:
        resolve(target, answers("169.254.169.254"))
    assert e.value.code == "private_address"


def test_any_private_answer_refuses_the_name():
    # a name answering one public and one private address could be connected to either
    with pytest.raises(WebhookRefused):
        vet("https://hooks.example.com/x", answers("93.184.216.34", "10.0.0.7"))


def test_a_name_that_doesnt_resolve_is_refused():
    def fails(*_a, **_k):
        raise socket.gaierror("no such name")
    with pytest.raises(WebhookRefused) as e:
        vet("https://nowhere.example.com/x", fails)
    assert e.value.code == "unresolvable"


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.read_sizes = []

    def read(self, n=None):
        self.read_sizes.append(n)
        return b"x" * (n or 0)


class FakeConnection:
    """Records what the sender does; answers `status`."""
    made: list["FakeConnection"] = []

    def __init__(self, target, address, timeout, status=200, fail=None):
        self.target, self.address, self.timeout = target, address, timeout
        self.status, self.fail = status, fail
        self.requests, self.sock, self.closed = [], None, False
        self.response = FakeResponse(status)
        FakeConnection.made.append(self)

    def connect(self):
        if self.fail:
            raise self.fail

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def opener(status=200, fail=None):
    FakeConnection.made = []
    return lambda target, address, timeout: FakeConnection(target, address, timeout, status, fail)


def test_send_connects_to_the_address_it_checked():
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener(204))
    sent = sender.send("https://hooks.example.com/x?y=1", b"{}", {"X": "1"})
    assert sent == Sent(True, 204)
    (conn,) = FakeConnection.made
    assert conn.address == "93.184.216.34" and conn.target.host == "hooks.example.com"
    assert conn.requests == [("POST", "/x?y=1", b"{}", {"X": "1"})]
    assert conn.response.read_sizes == [MAX_RESPONSE] and conn.closed


def test_send_checks_again_so_a_rebound_name_is_refused():
    # public when the webhook was saved, private now (DNS rebinding): nothing is sent
    assert vet("https://hooks.example.com/x", answers("93.184.216.34"))
    sender = HttpsSender(resolver=answers("169.254.169.254"), opener=opener())
    assert sender.send("https://hooks.example.com/x", b"{}", {}) == Sent(False, None,
                                                                         "private_address")
    assert FakeConnection.made == []


def test_a_redirect_is_never_followed():
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener(302))
    assert sender.send("https://hooks.example.com/x", b"{}", {}) == Sent(False, 302, "redirect")
    assert len(FakeConnection.made) == 1 and len(FakeConnection.made[0].requests) == 1


@pytest.mark.parametrize("status, code", [(500, "http_500"), (404, "http_404"),
                                          (429, "http_429")])
def test_an_error_status_fails_with_a_code(status, code):
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener(status))
    assert sender.send("https://hooks.example.com/x", b"{}", {}) == Sent(False, status, code)


@pytest.mark.parametrize("status", [999, 600, 42])
def test_a_status_outside_http_is_kept_as_nothing(status):
    # the delivery log's CHECK takes 100-599: anything else would fail the write and retry forever
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener(status))
    assert sender.send("https://hooks.example.com/x", b"{}", {}) == Sent(False, None,
                                                                         "bad_response")


@pytest.mark.parametrize("error, code", [(TimeoutError(), "timeout"),
                                         (ConnectionRefusedError(), "connect")])
def test_connection_failures_are_codes(error, code):
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener(fail=error))
    assert sender.send("https://hooks.example.com/x", b"{}", {}) == Sent(False, None, code)


def test_a_body_over_the_cap_isnt_sent():
    sender = HttpsSender(resolver=answers("93.184.216.34"), opener=opener())
    assert sender.send("https://hooks.example.com/x", b"x" * (MAX_BODY + 1), {}).error == \
        "too_large"
    assert FakeConnection.made == []


def test_the_real_opener_pins_the_address_and_checks_the_name():
    conn = webhooks._open(parse_url("https://hooks.example.com/x"), "93.184.216.34", 3)
    assert conn.host == "hooks.example.com" and conn._address == "93.184.216.34"
    assert conn.port == 443 and conn.timeout == 3


def test_secret_is_derived_never_the_jwt_secret_and_differs_per_webhook():
    s1 = webhook_secret(JWT, HOOK, b"a" * 16)
    assert s1 == webhook_secret(JWT, HOOK, b"a" * 16)
    assert s1 != webhook_secret(JWT, HOOK, b"b" * 16)
    assert s1 != webhook_secret(JWT, "0" * 8 + HOOK[8:], b"a" * 16)
    assert JWT not in s1 and len(s1) == 43


def test_signature_is_hmac_of_timestamp_and_body():
    body = b'{"type":"test"}'
    h = headers("s3cret-for-tests", "d-1", "test", body, now=lambda: 1_800_000_000)
    expected = hmac.new(b"s3cret-for-tests", b"1800000000." + body, hashlib.sha256).hexdigest()
    assert h["X-Tributary-Signature"] == f"v1={expected}" == signature("s3cret-for-tests",
                                                                        1_800_000_000, body)
    assert h["X-Tributary-Timestamp"] == "1800000000" and h["X-Tributary-Delivery"] == "d-1"


def test_mask_hides_a_discord_token():
    url = "https://discord.com/api/webhooks/1234567890/" + "t" * 60
    assert mask(url) == "https://discord.com/…tttt"
    assert "1234567890" not in mask(url)


def test_formats_and_bodies():
    assert suggest_format("https://discord.com/api/webhooks/1/x") == "discord"
    assert suggest_format("https://hooks.slack.com/services/T/B/x") == "slack"
    assert suggest_format("https://example.com/hook") == "json"
    discord = json.loads(body_of("discord", {"a": 1}, "@everyone NVDA filed"))
    assert discord["allowed_mentions"] == {"parse": []}
    slack = json.loads(body_of("slack", {"a": 1}, "<!channel> & co"))
    assert slack["text"] == "&lt;!channel&gt; &amp; co"
    assert json.loads(body_of("json", {"a": 1}, "ignored")) == {"a": 1}
