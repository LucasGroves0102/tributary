from authz_sync.state import AppState
from tributary_authz import Tuple


def ev(op, before=None, after=None):
    return {"op": op, "before": before, "after": after}


def seeded() -> AppState:
    """The demo seed (db/app/seed.sql), as Debezium snapshot events."""
    s = AppState()
    for plan, tier in [("free", "financials"), ("pro", "financials"), ("pro", "filings")]:
        s.apply("plan_tiers", ev("r", after={"plan_id": plan, "tier": tier}))
    for user, org in [("alice", None), ("bob", "acme"), ("carol", None), ("dave", "acme")]:
        s.apply("users", ev("r", after={"user_id": user, "org_id": org}))
    for user, plan in [("alice", "free"), ("bob", "pro"), ("carol", "pro"), ("dave", "free")]:
        s.apply("subscriptions", ev("r", after={"user_id": user, "plan_id": plan}))
    grants = [(1, "nvda", "alice", None), (2, "aapl", "alice", None),
              (3, "nvda", None, "acme"), (4, "msft", None, "acme")]
    for gid, company, user, org in grants:
        s.apply("company_grants", ev("r", after={
            "grant_id": gid, "company_id": company, "user_id": user, "org_id": org}))
    return s


def test_seed_access():
    assert seeded().desired_access() == {
        ("alice", "nvda", "financials"), ("alice", "aapl", "financials"),
        ("bob", "nvda", "financials"), ("bob", "nvda", "filings"),
        ("bob", "msft", "financials"), ("bob", "msft", "filings"),
        ("dave", "nvda", "financials"), ("dave", "msft", "financials"),
    }  # carol: Pro but no grants → nothing


def test_seed_tuples():
    t = seeded().desired_tuples()
    assert Tuple("user:alice", "viewer", "company:nvda") in t
    assert Tuple("organization:acme#member", "viewer", "company:msft") in t
    assert Tuple("user:bob", "member", "organization:acme") in t
    assert Tuple("user:bob", "subscriber", "tier:filings") in t
    assert Tuple("user:alice", "subscriber", "tier:filings") not in t
    assert Tuple("tier:filings", "filings_tier", "company:aapl") in t


def test_revoking_a_grant():
    s = seeded()
    s.apply("company_grants", ev("d", before={
        "grant_id": 1, "company_id": "nvda", "user_id": "alice", "org_id": None}))
    assert Tuple("user:alice", "viewer", "company:nvda") not in s.desired_tuples()
    assert ("alice", "nvda", "financials") not in s.desired_access()
    assert ("alice", "aapl", "financials") in s.desired_access()


def test_leaving_an_organization():
    s = seeded()
    s.apply("users", ev("u", before={"user_id": "dave", "org_id": "acme"},
                        after={"user_id": "dave", "org_id": None}))
    assert Tuple("user:dave", "member", "organization:acme") not in s.desired_tuples()
    assert not any(u == "dave" for u, _, _ in s.desired_access())


def test_downgrade_drops_filings_only():
    s = seeded()
    s.apply("subscriptions", ev("u", before={"user_id": "bob", "plan_id": "pro"},
                                after={"user_id": "bob", "plan_id": "free"}))
    bob = {(c, t) for u, c, t in s.desired_access() if u == "bob"}
    assert bob == {("nvda", "financials"), ("msft", "financials")}


def test_events_in_any_order_converge():
    # a subscription and grant arrive before the user and the plan's tiers
    s = AppState()
    s.apply("subscriptions", ev("c", after={"user_id": "eve", "plan_id": "pro"}))
    s.apply("company_grants", ev("c", after={
        "grant_id": 9, "company_id": "tsla", "user_id": "eve", "org_id": None}))
    assert s.desired_access() == set()
    s.apply("users", ev("c", after={"user_id": "eve", "org_id": None}))
    s.apply("plan_tiers", ev("c", after={"plan_id": "pro", "tier": "filings"}))
    assert s.desired_access() == {("eve", "tsla", "filings")}


def test_watchlists_are_not_consumed():
    import pytest
    with pytest.raises(ValueError, match="watchlists"):
        AppState().apply("watchlists", ev("c", after={"user_id": "alice"}))
