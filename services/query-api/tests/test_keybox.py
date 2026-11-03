"""Envelope encryption of market-data keys (query_api.keybox): round trip, binding to the user
and provider, tampering, rotation, and that neither keys nor key material are ever printed.
Master keys are generated here; none is stored anywhere."""

import base64
import dataclasses
import pickle

import pytest

from query_api.keybox import (
    Keyring,
    KeyringError,
    Sealed,
    SealedKeyError,
    Secret,
    last4,
    new_master_key,
)

API_KEY = "test" * 9 + "4567"  # obviously fake: low entropy


@pytest.fixture
def ring():
    return Keyring.parse(new_master_key())


def test_round_trip_and_ciphertext_never_holds_the_key(ring):
    sealed = ring.seal("alice", "tiingo", API_KEY)
    assert sealed.key_id == "k1"
    assert len(sealed.wrapped_key) == 60 and len(sealed.nonce) == 12
    assert API_KEY.encode() not in sealed.ciphertext + sealed.wrapped_key
    assert ring.open_("alice", "tiingo", sealed).reveal() == API_KEY
    # a fresh data key and nonces per save
    again = ring.seal("alice", "tiingo", API_KEY)
    assert again.ciphertext != sealed.ciphertext and again.wrapped_key != sealed.wrapped_key


@pytest.mark.parametrize("user, provider", [("bob", "tiingo"), ("alice", "other")])
def test_a_ciphertext_moved_to_another_user_or_provider_fails(ring, user, provider):
    sealed = ring.seal("alice", "tiingo", API_KEY)
    with pytest.raises(SealedKeyError):
        ring.open_(user, provider, sealed)


@pytest.mark.parametrize("field", ["wrapped_key", "nonce", "ciphertext"])
def test_tampering_fails(ring, field):
    sealed = ring.seal("alice", "tiingo", API_KEY)
    raw = bytearray(getattr(sealed, field))
    raw[-1] ^= 1
    with pytest.raises(SealedKeyError):
        ring.open_("alice", "tiingo", dataclasses.replace(sealed, **{field: bytes(raw)}))


def test_another_master_key_cant_open(ring):
    sealed = ring.seal("alice", "tiingo", API_KEY)
    with pytest.raises(SealedKeyError):
        Keyring.parse(new_master_key()).open_("alice", "tiingo", sealed)


def test_rotation_new_key_first_old_still_opens_and_rewrap(ring):
    old_value = new_master_key()
    old = Keyring.parse(old_value)
    sealed = old.seal("alice", "tiingo", API_KEY)
    new_b64 = base64.b64encode(b"\x07" * 32).decode()
    both = Keyring.parse(f"k2:{new_b64}, {old_value}")
    assert both.current == "k2"
    assert both.open_("alice", "tiingo", sealed).reveal() == API_KEY
    moved = both.rewrap("alice", "tiingo", sealed)
    assert moved.key_id == "k2" and moved.ciphertext == sealed.ciphertext
    assert Keyring.parse(f"k2:{new_b64}").open_("alice", "tiingo", moved).reveal() == API_KEY
    with pytest.raises(SealedKeyError):  # the old key id is gone from a ring without it
        Keyring.parse(f"k2:{new_b64}").open_("alice", "tiingo", sealed)


@pytest.mark.parametrize("raw", [
    "nocolon", "k1:not-base64!!", "k1:" + base64.b64encode(b"short").decode(),
    "bad id:" + base64.b64encode(b"x" * 32).decode(),
    "k1:{0},k1:{0}".format(base64.b64encode(b"x" * 32).decode()),
])
def test_malformed_master_keys_are_refused_without_echoing_them(raw):
    with pytest.raises(KeyringError) as e:
        Keyring.parse(raw)
    b64 = raw.partition(":")[2]
    assert not b64 or b64 not in str(e.value)


def test_feature_off_without_the_env_var():
    assert Keyring.from_env({}) is None
    assert Keyring.from_env({"DATA_CONNECTIONS_KEY": "  "}) is None
    assert Keyring.from_env({"DATA_CONNECTIONS_KEY": new_master_key()}) is not None


def test_nothing_prints_key_material(ring):
    s = Secret(API_KEY)
    assert API_KEY not in repr(s) and API_KEY not in str(s) and API_KEY not in f"{s}"
    with pytest.raises(TypeError):
        pickle.dumps(s)
    sealed = ring.seal("alice", "tiingo", API_KEY)
    assert repr(sealed) == "Sealed(key_id='k1')"
    master = new_master_key()
    assert repr(Keyring.parse(master)) == "Keyring(current='k1', ids=['k1'])"
    assert master.partition(":")[2] not in repr(Keyring.parse(master))


def test_oversized_keys_are_refused(ring):
    with pytest.raises(ValueError):
        ring.seal("alice", "tiingo", "x" * 257)
    with pytest.raises(ValueError):
        ring.seal("alice", "tiingo", "")


def test_last4():
    assert last4(API_KEY) == "4567"
    assert last4("ab") == "ab"


def test_sealed_is_frozen(ring):
    sealed = ring.seal("alice", "tiingo", API_KEY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sealed.key_id = "x"  # type: ignore[misc]
    assert isinstance(sealed, Sealed)
