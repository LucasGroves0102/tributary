"""Envelope encryption for users' market-data API keys (app migration 011, data_connections).

A master key never touches the database: it comes from the environment variable
DATA_CONNECTIONS_KEY (the tributary Secret's optional `data-connections-key`). Without it the
"Connect your data" feature is off (`Keyring.from_env` returns None, no routes exist).

Format of DATA_CONNECTIONS_KEY: one or more `<key id>:<base64 of 32 random bytes>`, separated by
commas, newest first. The first key encrypts; every listed key decrypts (rotation: put a new
key first, keep the old one listed until no row names it). A key id is 1-16 of [A-Za-z0-9_-].

Per row (`seal`):
  data key   = 32 random bytes (a new one per save)
  ciphertext = AES-256-GCM(data key, nonce, api key, aad)           -> nonce, ciphertext
  wrapped    = wnonce + AES-256-GCM(master[key id], wnonce, data key, aad)  -> wrapped_key
  aad        = "tributary data-connection v1\\0" + user id + "\\0" + provider
Associated data binds the ciphertext to its user and provider: moved to another user's row it
fails authentication instead of decrypting. Nonces are 96-bit random (a fresh data key per row
makes nonce reuse moot for the inner layer; the master key wraps one data key per save).

`open_` returns the key as a `Secret`, whose repr/str never show it; call `.reveal()` only at
the moment of the provider call. Nothing here logs."""

import base64
import binascii
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV = "DATA_CONNECTIONS_KEY"
KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
NONCE_BYTES = 12
MAX_KEY_BYTES = 256
AAD_PREFIX = b"tributary data-connection v1\x00"


class KeyringError(ValueError):
    """DATA_CONNECTIONS_KEY is malformed (the message never contains key material)."""


class SealedKeyError(Exception):
    """A stored key can't be opened: unknown key id, or tampered / moved ciphertext."""


class Secret:
    """A value that must not be printed: repr and str are a placeholder."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __reduce__(self):  # never pickled into a cache or a log handler by accident
        raise TypeError("Secret can't be pickled")


@dataclass(frozen=True)
class Sealed:
    key_id: str
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes

    def __repr__(self) -> str:
        return f"Sealed(key_id={self.key_id!r})"


def aad(user_id: str, provider: str) -> bytes:
    return AAD_PREFIX + user_id.encode() + b"\x00" + provider.encode()


def new_master_key() -> str:
    """A fresh `k1:<base64>` value (for tests and the owner's generation command)."""
    return "k1:" + base64.b64encode(secrets.token_bytes(32)).decode()


class Keyring:
    def __init__(self, keys: list[tuple[str, bytes]]) -> None:
        if not keys:
            raise KeyringError("no master key")
        self._keys = dict(keys)
        if len(self._keys) != len(keys):
            raise KeyringError("a key id appears twice")
        self.current = keys[0][0]

    def __repr__(self) -> str:
        return f"Keyring(current={self.current!r}, ids={sorted(self._keys)!r})"

    @classmethod
    def parse(cls, raw: str) -> "Keyring":
        keys = []
        for i, part in enumerate(p.strip() for p in raw.split(",")):
            if not part:
                continue
            key_id, sep, b64 = part.partition(":")
            if not sep or not KEY_ID.fullmatch(key_id):
                raise KeyringError(f"entry {i + 1}: expected <key id>:<base64 of 32 bytes>")
            try:
                material = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                raise KeyringError(f"entry {i + 1} ({key_id}): not base64") from None
            if len(material) != 32:
                raise KeyringError(f"entry {i + 1} ({key_id}): must be 32 bytes, "
                                   f"got {len(material)}")
            keys.append((key_id, material))
        return cls(keys)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Keyring | None":
        """None (feature off) when DATA_CONNECTIONS_KEY is unset or empty; a malformed value
        raises KeyringError at start rather than silently turning the feature off."""
        raw = env.get(ENV, "").strip()
        return cls.parse(raw) if raw else None

    def seal(self, user_id: str, provider: str, api_key: str) -> Sealed:
        plain = api_key.encode()
        if not plain or len(plain) > MAX_KEY_BYTES:
            raise ValueError("API key must be 1-256 bytes")
        ad = aad(user_id, provider)
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(nonce, plain, ad)
        wnonce = os.urandom(NONCE_BYTES)
        wrapped = wnonce + AESGCM(self._keys[self.current]).encrypt(wnonce, data_key, ad)
        return Sealed(self.current, wrapped, nonce, ciphertext)

    def open_(self, user_id: str, provider: str, sealed: Sealed) -> Secret:
        master = self._keys.get(sealed.key_id)
        if master is None:
            raise SealedKeyError("unknown master key id")
        ad = aad(user_id, provider)
        try:
            w = bytes(sealed.wrapped_key)
            data_key = AESGCM(master).decrypt(w[:NONCE_BYTES], w[NONCE_BYTES:], ad)
            plain = AESGCM(data_key).decrypt(bytes(sealed.nonce), bytes(sealed.ciphertext), ad)
        except (InvalidTag, ValueError):
            raise SealedKeyError("stored key failed authentication") from None
        return Secret(plain.decode())

    def rewrap(self, user_id: str, provider: str, sealed: Sealed) -> Sealed:
        """The same ciphertext with its data key wrapped by the current master key (rotation
        without re-encrypting the API key)."""
        master = self._keys.get(sealed.key_id)
        if master is None:
            raise SealedKeyError("unknown master key id")
        ad = aad(user_id, provider)
        w = bytes(sealed.wrapped_key)
        try:
            data_key = AESGCM(master).decrypt(w[:NONCE_BYTES], w[NONCE_BYTES:], ad)
        except InvalidTag:
            raise SealedKeyError("stored key failed authentication") from None
        wnonce = os.urandom(NONCE_BYTES)
        wrapped = wnonce + AESGCM(self._keys[self.current]).encrypt(wnonce, data_key, ad)
        return Sealed(self.current, wrapped, sealed.nonce, sealed.ciphertext)


def last4(api_key: str) -> str:
    """The last four alphanumeric characters, what the UI may show."""
    return re.sub(r"[^A-Za-z0-9]", "", api_key)[-4:]
