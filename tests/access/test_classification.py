"""Every route is classified and every private one probed (no database): a route added later
fails here until access_routes.py says what it serves and how to reach Ann's thing through it
(README.md). And agent-api: every route refuses API keys and connected apps' tokens before it
does anything."""

import inspect
import re
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from access_routes import ACCOUNT, OWNER, PROBES, PUBLIC, SEC
from access_world import FULL_APP_PARAMS, enumerated_app
from query_api.api_keys import KEY_ROUTES, PUBLIC_ROUTES, READ_ROUTES
from query_api.app import create_app

# path parameters that name something public (a company, a view or provider name), not an id
NATURAL = {"company_id", "view", "provider"}
CLASSES = {"public": PUBLIC, "sec": SEC, "owner": OWNER, "account": ACCOUNT}


def signed_in(dependant: Any) -> bool:
    """Whether the route (or anything it depends on) takes the caller's credential."""
    return any(getattr(d.call, "__name__", "") == "current_user" or signed_in(d)
               for d in dependant.dependencies)


def app_routes() -> dict[tuple[str, str], APIRoute]:
    out = {}
    for r in enumerated_app().routes:
        if isinstance(r, APIRoute):
            for m in r.methods:
                out[(m, r.path)] = r
    return out


def test_every_create_app_feature_is_on_in_the_suite():
    """A feature added to create_app must be switched on in access_world.full_app, or its
    routes would never be enumerated here."""
    assert set(inspect.signature(create_app).parameters) == set(FULL_APP_PARAMS)


def test_every_route_is_classified_once():
    routes = set(app_routes())
    classified = PUBLIC | SEC | OWNER | ACCOUNT
    assert sum(map(len, CLASSES.values())) == len(classified), "a route in two classes"
    assert routes - classified == set(), "classify these in tests/access/access_routes.py"
    assert classified - routes == set(), "classified but gone: remove them"


def test_only_public_routes_take_no_credential():
    routes = app_routes()
    for key, route in routes.items():
        assert signed_in(route.dependant) == (key not in PUBLIC), key


def test_keys_and_apps_never_reach_account_routes():
    """Account routes are sign-in only; a connected app reads only SEC data and owner reads."""
    assert ACCOUNT.isdisjoint(KEY_ROUTES)
    assert READ_ROUTES <= SEC | OWNER
    assert set(KEY_ROUTES) <= SEC | OWNER
    assert PUBLIC_ROUTES <= PUBLIC


def test_every_private_route_and_id_is_probed():
    """Each owner-only and account route has a probe, and each id it takes (path or query) is
    named in one: Ann's thing, or Ben's beside hers."""
    routes = app_routes()
    probed: dict[tuple[str, str], list] = {}
    for p in PROBES:
        assert p.key in routes, f"no such route: {p.key}"
        assert p.key in OWNER | ACCOUNT | SEC, p.key
        probed.setdefault(p.key, []).append(p)
    for key in sorted(OWNER | ACCOUNT):
        assert key in probed, f"no probe for {key}"
        for param in re.findall(r"{(\w+)}", key[1]):
            if param in NATURAL:
                continue
            assert any(str(p.path.get(param, "")).startswith("@") for p in probed[key]), \
                (key, param)


# --- agent-api ----------------------------------------------------------------------------------

def _boom(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("reached past the credential check")


def agent_app() -> Any:
    from tributary_agent.server import Limits
    from tributary_agent.server import create_app as agent_create_app

    params = set(inspect.signature(agent_create_app).parameters)
    on = {"make_agent": _boom, "who_is": _boom,
          "limits": Limits(per_hour=10, per_ip_per_hour=10, daily_budget_usd=1),
          "take_quota": _boom, "daily_count": _boom, "make_reporter": _boom,
          "make_checker": _boom, "refund_quota": _boom}
    # every feature on, so every route exists (a new one fails here until it's passed)
    assert params - {"client_ip_header"} == set(on), params ^ set(on)
    return agent_create_app(**on)


# Prometheus and health: no user data (nginx keeps /metrics off the public site)
AGENT_PUBLIC = {("GET", "/health"), ("GET", "/metrics")}


@pytest.mark.parametrize("token", ["trb_" + "A" * 43, "tro_" + "A" * 43, "trr_" + "A" * 43])
def test_agent_api_refuses_keys_and_app_tokens_on_every_route(token):
    app = agent_app()
    client = TestClient(app)
    routes = [(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods]
    assert len(routes) >= 5
    for method, path in routes:
        if (method, path) in AGENT_PUBLIC:
            continue
        r = client.request(method, path, json={}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403 and "sign in" in r.json()["detail"], (method, path, r.text)
