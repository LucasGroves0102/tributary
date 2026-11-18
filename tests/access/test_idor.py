"""The IDOR matrix: every owner-only and account route (access_routes.PROBES), as every identity
that isn't Ann, against Ann's things, with the public catalog open and closed (conftest).

For each probe and identity:
- Another user signed in (Ben), a demo sandbox, Ben's read key, write key and connected app:
  the answer for Ann's thing is the answer for a thing nobody has (status and body, ids
  normalized: no existence oracle), and a refusal (`deny`: 403 or 404 unless said); a key or
  app that can't use the route gets the route's 403 first. Nothing of Ann's appears in any
  answer, and nothing of hers changed (every row of hers, every user-keyed table, around RLS).
- Ann's own read key, write key and app: a route the credential may not use is the 403 and
  changes nothing (keys and apps never reach account routes, read scope never writes, even the
  owner's); one it may use works.
- Ann signed in: the probe works (the control: the request is well-formed and would reach her
  thing, so the refusals above are about whose it is).

Every test runs in savepoints rolled back after it."""

import io
import json
from typing import Any

import pytest

from access_routes import PROBES, SEC, UPLOAD, Probe
from access_world import ACC, ANNS_HOME, CO2, World, fill, normalized, refs
from query_api.api_keys import KEY_ROUTES, READ_ROUTES
from query_api.oauth import NOT_FOR_APPS

OTHERS = ("ben", "sandbox", "ben_read", "ben_write", "ben_app")
ANNS_KEYS = ("ann_read", "ann_write", "ann_app")
KEY_REFUSED = "API keys can't use this route: sign in to Tributary"


def reaches(cred: str, key: tuple[str, str]) -> bool:
    """Whether this credential may use the route at all (api_keys.KEY_ROUTES, oauth)."""
    scope = KEY_ROUTES.get(key)
    if cred.endswith("_read"):
        return scope == "read"
    if cred.endswith("_write"):
        return scope in ("read", "write")
    if cred.endswith("_app"):
        return key in READ_ROUTES
    return True


def send(world: World, cred: str, probe: Probe, names: dict[str, str]) -> Any:
    url = probe.route.format(**{k: fill(v, names) for k, v in probe.path.items()})
    kw: dict[str, Any] = {}
    if probe.query:
        kw["params"] = fill(probe.query, names)
    if probe.json is not None:
        kw["json"] = fill(probe.json, names)
    if probe.form is not None:
        kw["files"] = {"file": (UPLOAD[0], io.BytesIO(UPLOAD[1]), UPLOAD[2])}
        kw["data"] = fill(probe.form, names)
    if probe.content is not None:
        kw["content"] = probe.content
        kw["headers"] = {"Content-Type": "text/plain"}
    return world.call(cred, probe.method, url, **kw)


def answer(r: Any, *names: dict[str, str]) -> tuple[int, Any]:
    text = normalized(r.text, *names)
    try:
        return r.status_code, json.loads(text)
    except ValueError:
        return r.status_code, text


def leaked(world: World, r: Any) -> list[str]:
    return sorted(m for m in world.markers if m in r.text)


def changed(world: World) -> list[str]:
    now = world.fingerprint()
    return [t for t in now if now[t] != world.baseline[t]]


def refused_for(cred: str, r: Any) -> bool:
    if r.status_code != 403:
        return False
    detail = r.json().get("detail", "")
    if cred.endswith("_app"):
        return detail == NOT_FOR_APPS
    return detail == KEY_REFUSED or detail.startswith("this API key can't ")


def probe_id(p: Probe) -> str:
    return p.id()


@pytest.mark.parametrize("cred", OTHERS)
@pytest.mark.parametrize("probe", PROBES, ids=probe_id)
def test_nobody_else_reaches_anns_things(world: World, probe: Probe, cred: str) -> None:
    with world.rollback():
        r = send(world, cred, probe, refs(world, "ann"))
        assert r.status_code < 500, (r.status_code, r.text)
        assert not leaked(world, r), (leaked(world, r), r.text)
        if not reaches(cred, probe.key):
            assert refused_for(cred, r), (r.status_code, r.text)
        elif probe.names_anns:
            assert r.status_code in probe.deny, (r.status_code, r.text)
        assert not changed(world), changed(world)
    if reaches(cred, probe.key) and probe.names_anns:
        with world.rollback():
            mine, nobodys = refs(world, "ann"), refs(world, "missing")
            missing = send(world, cred, probe, nobodys)
            assert answer(r, mine) == answer(missing, nobodys), "an existence oracle"


@pytest.mark.parametrize("cred", ANNS_KEYS)
@pytest.mark.parametrize("probe", PROBES, ids=probe_id)
def test_keys_and_apps_reach_only_their_routes_even_the_owners(world: World, probe: Probe,
                                                                cred: str) -> None:
    with world.rollback():
        r = send(world, cred, probe, refs(world, "owner"))
        if reaches(cred, probe.key):
            assert r.status_code in probe.owner, (r.status_code, r.text)
        else:
            assert refused_for(cred, r), (r.status_code, r.text)
            assert not changed(world), changed(world)


@pytest.mark.parametrize("probe", PROBES, ids=probe_id)
def test_the_owner_reaches_her_own(world: World, probe: Probe) -> None:
    """The control: signed in, Ann's request works, so a refusal above is about whose it is."""
    with world.rollback():
        r = send(world, "ann", probe, refs(world, "owner"))
        assert r.status_code in probe.owner, (r.status_code, r.text)


# --- SEC data routes: never an answer with something of Ann's in it ----------------------------

SEC_PATH = {"company_id": "@co", "accession_no": ACC, "section_key": "1A",
            "candidate_id": CO2}
SEC_QUERY = {("GET", "/v1/companies/search"): {"q": "TAC"},
             ("GET", "/v1/search"): {"q": "stores"}}
SEC_BODY = {
    ("POST", "/v1/companies/{company_id}/model"): {},
    ("POST", "/v1/formulas/validate"): {"formula": "mine.stores.sales", "default_company": "@co"},
    ("POST", "/v1/formulas/evaluate"): {"formula": "mine.stores.sales", "default_company": "@co"},
    ("POST", "/v1/sheets/values"): {"requests": [{"company": "TACA", "metric": "revenue"}]},
}


def sec_probes() -> list[Probe]:
    out = []
    for method, route in sorted(SEC):
        path = {k: v for k, v in SEC_PATH.items() if "{" + k + "}" in route}
        out.append(Probe(method, route, path=path, query=SEC_QUERY.get((method, route), {}),
                         json=SEC_BODY.get((method, route))))
    return out


@pytest.mark.parametrize("cred", ("ben", "sandbox", "ben_read", "ben_app"))
@pytest.mark.parametrize("probe", sec_probes(), ids=probe_id)
def test_sec_routes_never_answer_with_anns_things(world: World, probe: Probe, cred: str) -> None:
    """Company data is the two-layer part (test_rls.py and the rest); what this checks is that
    nothing private rides along with it (a dataset, a metric, a holding, a note)."""
    with world.rollback():
        r = send(world, cred, probe, refs(world, "ann"))
        assert r.status_code < 500, (r.status_code, r.text)
        assert not leaked(world, r), (leaked(world, r), r.text)
        assert not changed(world), changed(world)


@pytest.mark.parametrize("cred", ("ben", "sandbox"))
def test_a_preference_carries_no_marker_so_it_is_compared(world: World, cred: str) -> None:
    """Ann's Home layout holds nothing a marker could find: anyone else's reads as the default
    (null), hers as she saved it."""
    with world.rollback():
        assert world.call(cred, "GET", "/v1/views").json()["views"]["home"] is None
        assert world.call("ann", "GET", "/v1/views").json()["views"]["home"] == ANNS_HOME
