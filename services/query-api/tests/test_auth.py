import time

import jwt
import pytest

from query_api.auth import Unauthenticated, check_secret, mint, verify

SECRET = "s3cret-" + "x" * 32


def test_round_trip():
    assert verify(mint("alice", SECRET), SECRET) == "alice"


def test_wrong_secret():
    with pytest.raises(Unauthenticated):
        verify(mint("alice", "other-" + "y" * 32), SECRET)


def test_expired():
    with pytest.raises(Unauthenticated):
        verify(mint("alice", SECRET, ttl_seconds=-1), SECRET)


def test_missing_expiry():
    token = jwt.encode({"sub": "alice"}, SECRET, algorithm="HS256")
    with pytest.raises(Unauthenticated):
        verify(token, SECRET)


def test_unsigned_token_rejected():
    # the classic "alg: none" forgery
    token = jwt.encode({"sub": "bob", "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(Unauthenticated):
        verify(token, SECRET)


def test_short_secret_refused_at_startup():
    with pytest.raises(ValueError, match="at least 32 bytes"):
        check_secret("short")
