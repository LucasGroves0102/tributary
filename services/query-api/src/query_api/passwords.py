"""Password hashing for owner-created accounts (query_api.password_accounts): stdlib scrypt
(hashlib.scrypt, no extra dependency) with a random 16-byte salt per password.

A stored hash names its algorithm and parameters, so they can be raised later without
invalidating anyone: `scrypt$n=32768,r=8,p=1$<salt b64>$<hash b64>`. verify() reads the
parameters from the hash (bounded, so a bad row can't make one verification eat the pod's
memory); needs_rehash() says when a hash is weaker than the current parameters, and sign-in
then stores a fresh one.

Memory: scrypt needs about 128 * r * n bytes, 32 MiB at n=2**15, r=8, which is exactly
OpenSSL's default `maxmem` limit (and over it with the bookkeeping), so every call passes an
explicit maxmem. Callers bound how many run at once (password_accounts.SCRYPT_SLOTS).

This module is pure: no I/O, no logging. A password never leaves the function it's passed to."""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

ALGORITHM = "scrypt"
SALT_BYTES = 16
KEY_BYTES = 32
MIN_LENGTH = 12
MAX_LENGTH = 256
# Owner-created accounts' user ids: "pw-<username>"; nothing else in the app database starts
# with it (sandboxes "demo-", GitHub "gh-", named demo users alice/bob/carol/dave).
USER_PREFIX = "pw-"
USERNAME = re.compile(r"^[a-z0-9_.-]{3,32}$")


@dataclass(frozen=True)
class Params:
    n: int = 2**15
    r: int = 8
    p: int = 1

    @property
    def maxmem(self) -> int:
        # 128 * r * n for the V array plus 128 * r * p for B, with headroom for OpenSSL's own
        return 2 * 128 * self.r * (self.n + self.p) + 1024 * 1024


CURRENT = Params()
# What verify() accepts from a stored hash: up to 16x today's cost, never less than n=2**10.
MAX_N, MAX_R, MAX_P = 2**19, 16, 4

_ENCODED = re.compile(r"^scrypt\$n=(\d+),r=(\d+),p=(\d+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$")


class PasswordRejected(ValueError):
    """The password breaks the length policy (the message never contains it)."""


def is_password_user(user_id: str) -> bool:
    return user_id.startswith(USER_PREFIX)


def user_id_for(username: str) -> str:
    return USER_PREFIX + username


def normalize_username(raw: str) -> str | None:
    """The canonical username (trimmed, lower case), or None if it can't be one."""
    name = raw.strip().lower()
    return name if USERNAME.match(name) else None


def check_policy(password: str) -> None:
    """Raises PasswordRejected unless the password is 12-256 characters."""
    if not isinstance(password, str):
        raise PasswordRejected("the password must be text")
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(f"the password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordRejected(f"the password must be at most {MAX_LENGTH} characters")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _derive(password: str, salt: bytes, params: Params, length: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=params.n, r=params.r,
                          p=params.p, maxmem=params.maxmem, dklen=length)


def hash_password(password: str, params: Params = CURRENT, salt: bytes | None = None) -> str:
    """The encoded hash of a password that meets the policy (check_policy)."""
    check_policy(password)
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    key = _derive(password, salt, params, KEY_BYTES)
    return f"{ALGORITHM}$n={params.n},r={params.r},p={params.p}${_b64(salt)}${_b64(key)}"


def parse(encoded: str) -> tuple[Params, bytes, bytes]:
    """(params, salt, key) of an encoded hash; ValueError if it isn't one we'd verify."""
    m = _ENCODED.match(encoded or "")
    if not m:
        raise ValueError("not a scrypt hash")
    n, r, p = (int(m.group(i)) for i in (1, 2, 3))
    if n < 2**10 or n > MAX_N or n & (n - 1) or not 1 <= r <= MAX_R or not 1 <= p <= MAX_P:
        raise ValueError("scrypt parameters out of range")
    salt, key = base64.b64decode(m.group(4)), base64.b64decode(m.group(5))
    if len(salt) < SALT_BYTES or not 16 <= len(key) <= 64:
        raise ValueError("scrypt salt or key length out of range")
    return Params(n, r, p), salt, key


def verify(password: str, encoded: str) -> bool:
    """Whether `password` matches the encoded hash (constant-time comparison). A password
    outside the length policy never matches, without hashing an unbounded input."""
    try:
        params, salt, key = parse(encoded)
    except ValueError:
        return False
    if not isinstance(password, str) or not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        # still do the work, so a policy miss costs the same time as a wrong password
        _derive("x" * MIN_LENGTH, salt, params, len(key))
        return False
    return hmac.compare_digest(_derive(password, salt, params, len(key)), key)


def needs_rehash(encoded: str, params: Params = CURRENT) -> bool:
    try:
        old, _, _ = parse(encoded)
    except ValueError:
        return True
    return old.n < params.n or old.r < params.r or old.p < params.p


_dummy: dict[str, str] = {}


def dummy_hash() -> str:
    """A hash of a random password, made once: unknown usernames are verified against it so
    they take as long as known ones."""
    if "h" not in _dummy:
        _dummy["h"] = hash_password(secrets.token_urlsafe(24))
    return _dummy["h"]
