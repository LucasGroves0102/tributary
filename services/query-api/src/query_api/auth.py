"""Caller identity: an HS256 JWT whose `sub` is the app user_id. The user is never taken
from request parameters, so neither a client nor the agent's model can claim another user."""

import argparse
import os
import time

import jwt

ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32  # RFC 7518 §3.2: HS256 keys at least as long as the hash


class Unauthenticated(Exception):
    pass


def check_secret(secret: str) -> str:
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError(f"QUERY_API_JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
    return secret


def verify(token: str, secret: str) -> str:
    """The user_id in a valid, unexpired token signed with `secret`."""
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM],
                            options={"require": ["sub", "exp"]})
    except jwt.PyJWTError as e:
        raise Unauthenticated(str(e)) from None
    return claims["sub"]


def mint(user_id: str, secret: str, ttl_seconds: int = 3600, now: int | None = None) -> str:
    """A token for user_id, valid ttl_seconds from `now` (default: the current time)."""
    now = int(time.time()) if now is None else now
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + ttl_seconds}, secret,
                      algorithm=ALGORITHM)


def mint_cli() -> None:
    """Dev helper: print a token for a demo user (make token USER=alice)."""
    ap = argparse.ArgumentParser(description=mint_cli.__doc__)
    ap.add_argument("user_id")
    ap.add_argument("--hours", type=float, default=8)
    args = ap.parse_args()
    print(mint(args.user_id, os.environ["QUERY_API_JWT_SECRET"], int(args.hours * 3600)))
