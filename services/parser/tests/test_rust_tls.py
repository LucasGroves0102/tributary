"""The Rust parser service connects with libpq's sslmode semantics: for every sslmode, it
succeeds or fails exactly where psycopg (libpq) does, against a server that rejects plaintext.

Needs PARSER_RS_BIN, TLS_TEST_DATABASE_URL (a TLS-only server whose certificate is for
`localhost`, signed by TLS_TEST_ROOT_CERT) and TLS_TEST_OTHER_CA (an unrelated CA). CI starts
that server (ci.yml, python job)."""

import os
import subprocess
import sys
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

psycopg = pytest.importorskip("psycopg")
RUST = os.environ.get("PARSER_RS_BIN")
DSN = os.environ.get("TLS_TEST_DATABASE_URL")
ROOT = os.environ.get("TLS_TEST_ROOT_CERT")
OTHER = os.environ.get("TLS_TEST_OTHER_CA")
pytestmark = pytest.mark.skipif(not (RUST and DSN and ROOT and OTHER),
                                reason="PARSER_RS_BIN or TLS_TEST_* not set")
TLS_ENV = ("PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY")


def url(host: str = "localhost", **params: str) -> str:
    parts = urlsplit(DSN or "")
    netloc = parts.netloc.replace("localhost", host)
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in TLS_ENV}
    return env | {"HOME": "/nonexistent"} | extra  # no ~/.postgresql/root.crt


@pytest.fixture(scope="module", autouse=True)
def bronze():
    with psycopg.connect(url(sslmode="require"), autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        c.execute("CREATE TABLE IF NOT EXISTS bronze.filing_documents"
                  " (accession_no text PRIMARY KEY, filed date)")


def rust_connects(conn: str, env: dict[str, str]) -> tuple[bool, str]:
    out = subprocess.run([RUST or "", "--reparse-all"], capture_output=True, text=True,
                         timeout=60, env=env | {"DATABASE_URL": conn, "LOG_LEVEL": "WARNING"})
    return out.returncode == 0, out.stderr


def libpq_connects(conn: str, env: dict[str, str]) -> bool:
    script = "import psycopg, sys; psycopg.connect(sys.argv[1]).close()"
    out = subprocess.run([sys.executable, "-c", script, conn], capture_output=True, timeout=60,
                         env=env)
    return out.returncode == 0


CASES = [
    # (id, host, url params, extra env, expected to connect)
    ("default-is-prefer", "localhost", {}, {}, True),
    ("prefer", "localhost", {"sslmode": "prefer"}, {}, True),
    ("prefer-root", "127.0.0.1", {"sslmode": "prefer", "sslrootcert": "ROOT"}, {}, True),
    # libpq retries in plaintext after a failed verification; the server rejects plaintext
    ("prefer-wrong-root", "localhost", {"sslmode": "prefer", "sslrootcert": "OTHER"}, {},
     False),
    ("disable", "localhost", {"sslmode": "disable"}, {}, False),
    ("require", "localhost", {"sslmode": "require"}, {}, True),
    ("require-wrong-root", "localhost", {"sslmode": "require", "sslrootcert": "OTHER"}, {},
     False),
    ("verify-ca", "127.0.0.1", {"sslmode": "verify-ca", "sslrootcert": "ROOT"}, {}, True),
    ("verify-ca-wrong-root", "localhost", {"sslmode": "verify-ca", "sslrootcert": "OTHER"}, {},
     False),
    ("verify-ca-no-root", "localhost", {"sslmode": "verify-ca"}, {}, False),
    ("verify-full", "localhost", {"sslmode": "verify-full", "sslrootcert": "ROOT"}, {}, True),
    ("verify-full-wrong-host", "127.0.0.1", {"sslmode": "verify-full", "sslrootcert": "ROOT"},
     {}, False),
    ("env-disable", "localhost", {}, {"PGSSLMODE": "disable"}, False),
    ("env-verify-full", "localhost", {},
     {"PGSSLMODE": "verify-full", "PGSSLROOTCERT": "ROOT"}, True),
    ("url-beats-env", "localhost", {"sslmode": "require"}, {"PGSSLMODE": "disable"}, True),
]


def resolve(values: dict[str, str]) -> dict[str, str]:
    files = {"ROOT": ROOT or "", "OTHER": OTHER or ""}
    return {k: files.get(v, v) for k, v in values.items()}


@pytest.mark.parametrize(("host", "params", "extra", "expected"),
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_rust_matches_libpq(host, params, extra, expected):
    conn, env = url(host, **resolve(params)), clean_env(**resolve(extra))
    assert libpq_connects(conn, env) is expected, "libpq disagrees with the expectation"
    ok, stderr = rust_connects(conn, env)
    assert ok is expected, stderr
