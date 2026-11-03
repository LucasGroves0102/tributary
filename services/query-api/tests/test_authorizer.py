"""FgaAuthorizer against a fake OpenFGA client: which calls it makes, and what they mean."""

import httpx
import pytest

from query_api.authorizer import CATALOG, Access, AuthzUnavailable, FgaAuthorizer
from tributary_authz import FgaError


class FakeFga:
    """Answers like OpenFGA for authz/model.fga, from a few facts; records every call."""

    def __init__(self, public=False, tiers=(), granted=(), can_read=None, fail=None):
        self.public, self.tiers, self.granted = public, set(tiers), list(granted)
        self.can_read = can_read or {}  # relation → company objects (catalog closed)
        self.fail, self.calls = fail, []

    def check(self, user, relation, object):
        self.calls.append(("check", relation, object))
        if self.fail:
            raise self.fail
        if object == CATALOG:
            return self.public
        return object in self.can_read.get(relation, [])

    def list_objects(self, user, relation, type):
        self.calls.append(("list", relation, type))
        if relation == "subscriber":
            return [f"tier:{t}" for t in sorted(self.tiers)]
        if relation == "granted":
            return [f"company:{c}" for c in self.granted]
        if self.public:
            raise AssertionError("never ListObjects can_read_* over a public catalog")
        return self.can_read.get(relation, [])


def test_closed_catalog_lists_what_the_user_can_read():
    fga = FakeFga(can_read={"can_read_financials": ["company:1", "company:2"],
                            "can_read_filings": ["company:1"]})
    access = FgaAuthorizer(fga).access("alice")  # type: ignore[arg-type]
    assert access == Access(frozenset(), {"1": {"financials", "filings"}, "2": {"financials"}})
    assert fga.calls == [("check", "reader", CATALOG),
                         ("list", "can_read_financials", "company"),
                         ("list", "can_read_filings", "company")]


def test_public_catalog_is_one_check_and_two_small_lists():
    fga = FakeFga(public=True, tiers={"financials", "filings"}, granted=["9"])
    access = FgaAuthorizer(fga).access("carol")  # type: ignore[arg-type]
    assert access == Access(frozenset({"financials", "filings"}),
                            {"9": {"financials", "filings"}})
    assert fga.calls == [("check", "reader", CATALOG), ("list", "subscriber", "tier"),
                         ("list", "granted", "company")]


def test_public_catalog_without_tiers_reads_nothing():
    fga = FakeFga(public=True, tiers=(), granted=["9"])
    assert FgaAuthorizer(fga).access("erin") == Access(frozenset(), {})  # type: ignore[arg-type]


def test_unknown_tier_objects_are_ignored():
    fga = FakeFga(public=True, tiers={"financials", "beta"})
    assert FgaAuthorizer(fga).access("x").public == {"financials"}  # type: ignore[arg-type]


def test_can_is_a_plain_check():
    fga = FakeFga(public=True, can_read={"can_read_filings": ["company:1"]})
    assert FgaAuthorizer(fga).can("carol", "filings", "1") is True  # type: ignore[arg-type]
    assert fga.calls == [("check", "can_read_filings", "company:1")]


@pytest.mark.parametrize("error", [FgaError("check failed (500)"), httpx.ConnectError("down")])
def test_unreachable_fails_closed(error):
    authorizer = FgaAuthorizer(FakeFga(fail=error))  # type: ignore[arg-type]
    with pytest.raises(AuthzUnavailable):
        authorizer.access("alice")
    with pytest.raises(AuthzUnavailable):
        authorizer.can("alice", "financials", "1")
