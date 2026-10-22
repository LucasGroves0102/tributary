"""Reports (kind `report`, app migration 005): the routes agent-api saves them through, their
validation, and that the generic item routes can't write one. In-memory store
(test_workspaces.FakeWorkspaces); test_reports_db.py runs the real one."""

import copy

import pytest

from query_api.reports import MAX_MARKDOWN, MAX_REPORT_BODY, validate_report
from query_api.workspaces import MAX_BODY_BYTES, StateError

from .test_api import NVDA
from .test_workspaces import CHART, COMPS, new_item, new_workspace, world  # noqa: F401

ACC = "0001045810-26-000012"


def report_state(workspace_id, item_id, **over):
    state = {
        "workspace_id": workspace_id,
        "markdown": "# Semis\n\n## Summary\n\nRevenue was $46.7B (10-Q filed 2026-08-27, "
                    f"{ACC}).\n\n## Revenue\n\n[[embed:item-1]]\n\nAs of 2026-10-21.",
        "items": [{"ref": "item-1", "item_id": item_id, "kind": "chart", "name": "Rev",
                   "state": CHART}],
        "citations": [{"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27",
                       "company_id": NVDA,
                       "url": "https://www.sec.gov/Archives/edgar/data/1045810/x.htm",
                       "sections": []}],
        "verification": {"ok": True, "figures": [{"text": "$46.7B", "status": "verified"}],
                         "unverified_accessions": [], "used_data": True, "freshness": True},
        "as_of": "2026-10-21T08:00:00+00:00",
        "written_at": "2026-10-21T09:00:00+00:00",
        "model": "claude-haiku-5-5",
        "questions": 3,
        "stop": "end_turn",
    }
    state.update(over)
    return state


@pytest.fixture
def saved(world, token):  # noqa: F811
    client, ws, _ = world
    wid = new_workspace(client, token)
    item = new_item(client, token, wid)
    return client, ws, wid, item["item_id"]


def post_report(client, token, wid, state, user="alice", name="Report: Semis"):
    return client.post(f"/v1/workspaces/{wid}/reports", json={"name": name, "state": state},
                       headers=token(user))


def test_get_workspace_with_states(saved, token):
    client, _, wid, iid = saved
    r = client.get(f"/v1/workspaces/{wid}", headers=token("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Semis" and body["items"][0]["item_id"] == iid
    assert body["items"][0]["state"]["layers"][0]["companyId"] == NVDA
    # another user's workspace and a missing one answer the same
    other = client.get(f"/v1/workspaces/{wid}", headers=token("bob"))
    missing = client.get("/v1/workspaces/00000000-0000-4000-8000-000000000000",
                         headers=token("bob"))
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_save_open_and_regenerate_a_report(saved, token):
    client, _, wid, iid = saved
    r = post_report(client, token, wid, report_state(wid, iid))
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["kind"] == "report" and report["state"]["verification"]["ok"] is True
    listed = client.get(f"/v1/workspaces/{wid}", headers=token("alice")).json()["items"]
    assert [i["kind"] for i in listed] == ["chart", "report"]
    assert listed[1]["state"] is None  # large: fetched one at a time
    opened = client.post(f"/v1/workspace-items/{report['item_id']}/open", headers=token("alice"))
    assert opened.json()["state"]["markdown"].startswith("# Semis")
    again = report_state(wid, iid, markdown="# Semis v2\n\n[[embed:item-1]]")
    r = client.put(f"/v1/workspaces/{wid}/reports/{report['item_id']}", json={"state": again},
                   headers=token("alice"))
    assert r.status_code == 200 and r.json()["state"]["markdown"].startswith("# Semis v2")
    # only a report in that workspace can be written over
    r = client.put(f"/v1/workspaces/{wid}/reports/{iid}", json={"state": again},
                   headers=token("alice"))
    assert r.status_code == 404
    # renaming works like any item; the generic route can't write its state
    r = client.put(f"/v1/workspace-items/{report['item_id']}", json={"name": "Semis note"},
                   headers=token("alice"))
    assert r.status_code == 200 and r.json()["name"] == "Semis note"
    r = client.put(f"/v1/workspace-items/{report['item_id']}", json={"state": again},
                   headers=token("alice"))
    assert r.status_code == 422 and "written by the assistant" in r.json()["detail"]


def test_reports_are_owner_only(saved, token):
    client, ws, wid, iid = saved
    assert post_report(client, token, wid, report_state(wid, iid), user="bob").status_code == 404
    report = post_report(client, token, wid, report_state(wid, iid)).json()
    r = client.put(f"/v1/workspaces/{wid}/reports/{report['item_id']}",
                   json={"state": report_state(wid, iid)}, headers=token("bob"))
    assert r.status_code == 404
    assert client.get(f"/v1/workspace-items/{report['item_id']}",
                      headers=token("bob")).status_code == 404
    assert ws.items[report["item_id"]]["user_id"] == "alice"


def test_the_generic_item_route_cannot_create_a_report(saved, token):
    client, _, wid, iid = saved
    r = client.post(f"/v1/workspaces/{wid}/items",
                    json={"kind": "report", "name": "x", "state": report_state(wid, iid)},
                    headers=token("alice"))
    assert r.status_code == 422


@pytest.mark.parametrize("change, message", [
    ({"markdown": "# x\n\n[[embed:item-2]]\n\n[[embed:item-1]]"}, "unknown items"),
    ({"markdown": "# x, nothing embedded"}, "never embedded"),
    ({"markdown": "# x\n\n[[embed:item-1]]\x00"}, "control characters"),
    ({"markdown": "x" * (MAX_MARKDOWN + 1) + "\n[[embed:item-1]]"}, "at most"),
    ({"model": "haiku; drop table"}, "pattern"),
    ({"questions": 0}, "greater than"),
    ({"user_id": "bob"}, "Extra inputs"),
    ({"stop": "whatever"}, "stop"),
])
def test_report_state_is_strict(saved, token, change, message):
    client, _, wid, iid = saved
    r = post_report(client, token, wid, report_state(wid, iid, **change))
    assert r.status_code in (413, 422) and (r.status_code == 413 or message in r.text), r.text


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s["items"][0].update(state={"layers": []}), "item-1"),
    (lambda s: s["items"][0].update(kind="notes"), "kind"),
    (lambda s: s["items"][0].update(name="a\x07b"), "control"),
    (lambda s: s["items"].append({**s["items"][0], "ref": "item-2"}), "repeat"),
    (lambda s: s["citations"][0].update(url="javascript:alert(1)"), "url"),
    (lambda s: s["citations"][0].update(url="https://evil.example/x"), "url"),
    (lambda s: s["citations"][0].update(accession_no="../../x"), "accession_no"),
    (lambda s: s["citations"][0].update(sections=["<img>"]), "sections"),
    (lambda s: s["verification"]["figures"].append({"text": "$1B", "status": "trusted"}),
     "status"),
])
def test_report_parts_are_validated(saved, token, mutate, message):
    client, _, wid, iid = saved
    state = report_state(wid, iid)
    mutate(state)
    r = post_report(client, token, wid, state)
    assert r.status_code == 422 and message in r.text, r.text


def test_verification_ok_is_recomputed_not_trusted():
    state = report_state("00000000-0000-4000-8000-000000000001",
                         "00000000-0000-4000-8000-000000000002")
    state["verification"] = {"ok": True, "figures": [{"text": "$9.9B", "status": "unverified"}],
                             "unverified_accessions": [], "used_data": True, "freshness": True}
    assert validate_report(state)["verification"]["ok"] is False
    state["verification"]["figures"] = []
    state["verification"]["freshness"] = False
    assert validate_report(state)["verification"]["ok"] is False
    state["verification"]["unverified_accessions"] = [ACC]
    state["verification"]["freshness"] = True
    assert validate_report(state)["verification"]["ok"] is False


def test_embedded_states_are_validated_like_saved_ones():
    state = report_state("00000000-0000-4000-8000-000000000001",
                         "00000000-0000-4000-8000-000000000002")
    state["items"][0] = {**state["items"][0], "kind": "comps", "state": COMPS}
    assert validate_report(state)["items"][0]["state"]["target"] == NVDA
    bad = copy.deepcopy(state)
    bad["items"][0]["state"] = {**COMPS, "peers": [NVDA]}  # the target as its own peer
    with pytest.raises(StateError):
        validate_report(bad)


def test_report_bodies_have_their_own_size_cap(saved, token):
    client, _, wid, iid = saved
    state = report_state(wid, iid)
    big = report_state(wid, iid, markdown="x" * (MAX_BODY_BYTES + 100) + "\n[[embed:item-1]]")
    assert post_report(client, token, wid, big).status_code == 201  # above the item cap
    r = client.post(f"/v1/workspaces/{wid}/reports", content=b"x",
                    headers={**token("alice"), "Content-Length": str(MAX_REPORT_BODY + 1),
                             "Content-Type": "application/json"})
    assert r.status_code == 413
    # the generic routes keep the small cap
    r = client.post(f"/v1/workspaces/{wid}/items", content=b"x",
                    headers={**token("alice"), "Content-Length": str(MAX_BODY_BYTES + 1),
                             "Content-Type": "application/json"})
    assert r.status_code == 413
    assert post_report(client, token, wid, state).status_code == 201


def test_nginx_keeps_the_report_routes_off_the_public_site():
    """Only agent-api writes reports (straight to the Query API); the public proxy 404s them."""
    import re
    from pathlib import Path

    conf = (Path(__file__).parents[2] / "web/deploy/nginx.conf").read_text()
    blocked = [re.compile(m) for m in re.findall(r"location ~ (\S+) \{\s*return 404;", conf)]
    for path in ("/api/query/v1/workspaces/abc/reports",
                 "/api/query/v1/workspaces/abc/reports/def",
                 "/api/query//v1//workspaces/abc//reports"):
        assert any(b.search(path) for b in blocked), path
    for path in ("/api/query/v1/workspaces/abc", "/api/query/v1/workspaces/abc/items"):
        assert not any(b.search(path) for b in blocked), path
