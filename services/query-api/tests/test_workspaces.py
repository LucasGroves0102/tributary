"""Saved work (workspaces, items, recent): the routes with an in-memory store that keeps
AppWorkspaces' rules (test_workspaces_db.py runs the real one). Adversarial cases: guessing
ids, another user's ids, a user id in the body, malformed ids and payloads, oversized bodies,
and that a saved item never grants access to data."""

import copy
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.workspaces import (
    MAX_BODY_BYTES,
    MAX_ITEMS_PER_WORKSPACE,
    MAX_RECENT_FILINGS,
    MAX_WORKSPACES,
    LimitReached,
    StateError,
    UnknownUser,
    copy_name,
    validate_state,
)

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer, FakeStore

AAPL = "0000320193"

ACC = "0001045810-26-000012"
CHART = {"title": "NVDA revenue", "range": 3, "align": "calendar", "layers": [
    {"kind": "metric", "companyId": NVDA, "metric": "revenue", "transform": "ttm",
     "type": "line", "axis": "L"}]}
COMPS = {"target": NVDA, "peers": [AAPL, TSLA], "cols": ["revenue_ttm", "operating_margin_ttm"]}
SCREEN = {"industry": "software", "margin": 20, "sort": "-revenue_ttm"}
SAVED_CHART = validate_state("chart", CHART)  # as stored: defaults filled in


class FakeWorkspaces:
    """AppWorkspaces' rules in memory: everything is keyed by owner, other users' rows are
    invisible, caps as in workspaces.py."""

    def __init__(self, users=("alice", "bob")):
        self.users = set(users)
        self.workspaces: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.reads: dict[str, list[dict]] = {}
        self.tick = 0

    def _now(self):
        self.tick += 1
        return datetime(2026, 10, 21, tzinfo=UTC) + timedelta(seconds=self.tick)

    def _ws(self, user_id, workspace_id):
        w = self.workspaces.get(workspace_id)
        return w if w and w["user_id"] == user_id else None

    def _it(self, user_id, item_id):
        i = self.items.get(item_id)
        return i if i and i["user_id"] == user_id else None

    @staticmethod
    def _summary(i):
        return {k: i[k] for k in ("item_id", "workspace_id", "kind", "name", "created_at",
                                  "updated_at", "opened_at")}

    @staticmethod
    def _public(w, items):
        return {**{k: w[k] for k in ("workspace_id", "name", "created_at", "updated_at")},
                "items": items}

    def _room(self, user_id, workspaces=0, items=0, workspace_id=None):
        mine = [w for w in self.workspaces.values() if w["user_id"] == user_id]
        if len(mine) + workspaces > MAX_WORKSPACES:
            raise LimitReached(f"you can have at most {MAX_WORKSPACES} workspaces")
        here = [i for i in self.items.values() if i["workspace_id"] == workspace_id]
        if workspace_id and len(here) + items > MAX_ITEMS_PER_WORKSPACE:
            raise LimitReached(f"a workspace holds at most {MAX_ITEMS_PER_WORKSPACE} items")

    def list(self, user_id):
        return [self._public(w, [self._summary(i) for i in self.items.values()
                                 if i["workspace_id"] == w["workspace_id"]])
                for w in self.workspaces.values() if w["user_id"] == user_id]

    def create(self, user_id, name):
        if user_id not in self.users:
            raise UnknownUser(user_id)
        self._room(user_id, workspaces=1)
        now = self._now()
        w = {"workspace_id": str(uuid.uuid4()), "user_id": user_id, "name": name,
             "created_at": now, "updated_at": now}
        self.workspaces[w["workspace_id"]] = w
        return self._public(w, [])

    def rename(self, user_id, workspace_id, name):
        w = self._ws(user_id, workspace_id)
        if not w:
            return None
        w["name"] = name
        return self._public(w, [])

    def delete(self, user_id, workspace_id):
        if not self._ws(user_id, workspace_id):
            return False
        del self.workspaces[workspace_id]
        self.items = {k: i for k, i in self.items.items() if i["workspace_id"] != workspace_id}
        return True

    def duplicate(self, user_id, workspace_id, name):
        w = self._ws(user_id, workspace_id)
        if not w:
            return None
        new = self.create(user_id, name or copy_name(w["name"], 100))
        for i in [i for i in self.items.values() if i["workspace_id"] == workspace_id]:
            self.add_item(user_id, new["workspace_id"], i["kind"], i["name"], i["state"])
        return self.list(user_id)[-1]

    def add_item(self, user_id, workspace_id, kind, name, state):
        if not self._ws(user_id, workspace_id):
            return None
        self._room(user_id, items=1, workspace_id=workspace_id)
        now = self._now()
        i = {"item_id": str(uuid.uuid4()), "workspace_id": workspace_id, "user_id": user_id,
             "kind": kind, "name": name, "state": copy.deepcopy(state), "created_at": now,
             "updated_at": now, "opened_at": None}
        self.items[i["item_id"]] = i
        return {**self._summary(i), "state": i["state"]}

    def get(self, user_id, workspace_id):
        w = self._ws(user_id, workspace_id)
        if not w:
            return None
        return self._public(w, [{**self._summary(i),
                                 "state": None if i["kind"] == "report" else i["state"]}
                                for i in self.items.values()
                                if i["workspace_id"] == workspace_id])

    def update_report(self, user_id, workspace_id, item_id, state):
        i = self._it(user_id, item_id)
        if not i or i["kind"] != "report" or i["workspace_id"] != workspace_id:
            return None
        i["state"] = state
        i["updated_at"] = self._now()
        return {**self._summary(i), "state": i["state"]}

    def item(self, user_id, item_id):
        i = self._it(user_id, item_id)
        if not i:
            return None
        return {**self._summary(i), "state": i["state"],
                "workspace_name": self.workspaces[i["workspace_id"]]["name"]}

    def update_item(self, user_id, item_id, name, state):
        i = self._it(user_id, item_id)
        if not i:
            return None
        i["name"] = name or i["name"]
        i["state"] = state if state is not None else i["state"]
        i["updated_at"] = self._now()
        return {**self._summary(i), "state": i["state"]}

    def duplicate_item(self, user_id, item_id, workspace_id, name):
        i = self._it(user_id, item_id)
        if not i:
            return None
        return self.add_item(user_id, workspace_id or i["workspace_id"], i["kind"],
                             name or copy_name(i["name"], 120), i["state"])

    def delete_item(self, user_id, item_id):
        if not self._it(user_id, item_id):
            return False
        del self.items[item_id]
        return True

    def mark_opened(self, user_id, item_id):
        i = self._it(user_id, item_id)
        if not i:
            return None
        i["opened_at"] = self._now()
        return i["opened_at"]

    def recent(self, user_id, limit):
        mine = [i for i in self.items.values() if i["user_id"] == user_id and i["opened_at"]]
        mine.sort(key=lambda i: i["opened_at"], reverse=True)
        return [{**self._summary(i), "workspace_name":
                 self.workspaces[i["workspace_id"]]["name"]} for i in mine[:limit]]

    def read_filing(self, user_id, company_id, accession_no, section_key):
        if user_id not in self.users:
            raise UnknownUser(user_id)
        reads = [r for r in self.reads.get(user_id, []) if r["accession_no"] != accession_no]
        reads.insert(0, {"company_id": company_id, "accession_no": accession_no,
                         "section_key": section_key, "read_at": self._now()})
        self.reads[user_id] = reads[:MAX_RECENT_FILINGS]

    def recent_filings(self, user_id, limit):
        return self.reads.get(user_id, [])[:limit]


class MetaStore(FakeStore):
    def filings_meta(self, user_id, accession_nos):
        self.calls.append(("filings_meta", user_id, tuple(accession_nos)))
        return {a: {"form": "10-Q", "filed": date(2026, 8, 26), "period_end": date(2026, 7, 26)}
                for a in accession_nos}

    def profiles(self, user_id, ids):
        return {c: {"name": f"Company {c[-4:]}", "sic": None, "industry": None,
                    "exchanges": None} for c in ids}


@pytest.fixture
def world():
    store, ws = MetaStore(), FakeWorkspaces()
    # alice reads NVDA financials only; bob reads AAPL
    authz = FakeAuthorizer({"alice": {NVDA: {"financials"}}, "bob": {AAPL: {"financials"}}})
    return TestClient(create_app(authz, store, SECRET, workspaces=ws)), ws, store


def new_workspace(client, token, user="alice", name="Semis"):
    r = client.post("/v1/workspaces", json={"name": name}, headers=token(user))
    assert r.status_code == 201, r.text
    return r.json()["workspace_id"]


def new_item(client, token, workspace_id, user="alice", kind="chart", state=CHART, name="Rev"):
    r = client.post(f"/v1/workspaces/{workspace_id}/items",
                    json={"kind": kind, "name": name, "state": state}, headers=token(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_save_list_open_update_rename_delete(world, token):
    client, ws, _ = world
    wid = new_workspace(client, token)
    item = new_item(client, token, wid)
    assert item["state"] == SAVED_CHART and item["kind"] == "chart"
    assert SAVED_CHART["layers"][0]["hidden"] is False
    listed = client.get("/v1/workspaces", headers=token("alice")).json()["workspaces"]
    assert [w["name"] for w in listed] == ["Semis"]
    assert [i["name"] for i in listed[0]["items"]] == ["Rev"]
    assert "state" not in listed[0]["items"][0]  # the list carries no states
    opened = client.post(f"/v1/workspace-items/{item['item_id']}/open", headers=token("alice"))
    assert opened.status_code == 200 and opened.json()["state"] == SAVED_CHART
    assert opened.json()["workspace_name"] == "Semis"
    changed = {**CHART, "range": 5}
    r = client.put(f"/v1/workspace-items/{item['item_id']}", json={"state": changed},
                   headers=token("alice"))
    assert r.status_code == 200 and r.json()["state"]["range"] == 5
    r = client.put(f"/v1/workspace-items/{item['item_id']}", json={"name": "  Revenue  "},
                   headers=token("alice"))
    assert r.json()["name"] == "Revenue"  # trimmed
    r = client.patch(f"/v1/workspaces/{wid}", json={"name": "Chips"}, headers=token("alice"))
    assert r.status_code == 200 and r.json()["name"] == "Chips"
    assert client.delete(f"/v1/workspace-items/{item['item_id']}",
                         headers=token("alice")).json() == {"item_id": item["item_id"],
                                                            "deleted": True}
    assert client.delete(f"/v1/workspaces/{wid}", headers=token("alice")).status_code == 200
    assert client.get("/v1/workspaces", headers=token("alice")).json() == {"workspaces": []}


def test_duplicate_workspace_and_item(world, token):
    client, *_ = world
    wid = new_workspace(client, token)
    item = new_item(client, token, wid)
    new_item(client, token, wid, kind="comps", state=COMPS, name="Comps")
    r = client.post(f"/v1/workspaces/{wid}/duplicate", headers=token("alice"))
    assert r.status_code == 201
    assert r.json()["name"] == "Semis (copy)"
    assert [i["name"] for i in r.json()["items"]] == ["Rev", "Comps"]
    other = new_workspace(client, token, name="Other")
    r = client.post(f"/v1/workspace-items/{item['item_id']}/duplicate",
                    json={"workspace_id": other, "name": "Rev 2"}, headers=token("alice"))
    assert r.status_code == 201 and r.json()["workspace_id"] == other
    assert r.json()["state"] == SAVED_CHART and r.json()["name"] == "Rev 2"
    r = client.post(f"/v1/workspace-items/{item['item_id']}/duplicate", headers=token("alice"))
    assert r.json()["name"] == "Rev (copy)" and r.json()["workspace_id"] == wid


CHART_V2 = {"title": "Capital return", "range": "max", "align": "fiscal", "markers": True,
            "layers": [
                {"kind": "metric", "companyId": NVDA, "metric": "dividends_paid", "transform": "ps",
                 "type": "sbar", "axis": "L", "name": "Dividends / share", "color": 3},
                {"kind": "metric", "companyId": NVDA, "metric": "revenue", "transform": "qoq",
                 "type": "sarea", "axis": "R"}]}


@pytest.mark.parametrize("kind, state", [("chart", CHART), ("chart", CHART_V2), ("comps", COMPS),
                                         ("screen", SCREEN)])
def test_each_kind_round_trips(world, token, kind, state):
    client, *_ = world
    wid = new_workspace(client, token)
    item = new_item(client, token, wid, kind=kind, state=state)
    got = client.get(f"/v1/workspace-items/{item['item_id']}", headers=token("alice")).json()
    assert got["state"] == validate_state(kind, state)


def test_another_users_workspace_is_indistinguishable_from_a_missing_one(world, token):
    client, *_ = world
    wid = new_workspace(client, token)
    iid = new_item(client, token, wid)["item_id"]
    guess = str(uuid.uuid4())
    bob = token("bob")
    for wsid in (wid, guess):
        answers = [
            client.patch(f"/v1/workspaces/{wsid}", json={"name": "mine"}, headers=bob),
            client.delete(f"/v1/workspaces/{wsid}", headers=bob),
            client.post(f"/v1/workspaces/{wsid}/duplicate", headers=bob),
            client.post(f"/v1/workspaces/{wsid}/items",
                        json={"kind": "chart", "name": "x", "state": CHART}, headers=bob),
        ]
        assert {(r.status_code, r.text) for r in answers} == {
            (404, '{"detail":"workspace not found"}')}
    for itid in (iid, guess):
        answers = [
            client.get(f"/v1/workspace-items/{itid}", headers=bob),
            client.post(f"/v1/workspace-items/{itid}/open", headers=bob),
            client.put(f"/v1/workspace-items/{itid}", json={"name": "x"}, headers=bob),
            client.post(f"/v1/workspace-items/{itid}/duplicate", headers=bob),
            client.delete(f"/v1/workspace-items/{itid}", headers=bob),
        ]
        assert {(r.status_code, r.text) for r in answers} == {
            (404, '{"detail":"item not found"}')}
    assert client.get("/v1/workspaces", headers=bob).json() == {"workspaces": []}
    # bob can't copy alice's item into his workspace, nor his item into hers
    bobs = new_workspace(client, token, user="bob", name="Bob's")
    r = client.post(f"/v1/workspace-items/{iid}/duplicate", json={"workspace_id": bobs},
                    headers=bob)
    assert r.status_code == 404
    bob_item = new_item(client, token, bobs, user="bob")["item_id"]
    r = client.post(f"/v1/workspace-items/{bob_item}/duplicate", json={"workspace_id": wid},
                    headers=bob)
    assert r.status_code == 404
    # alice's work is untouched
    mine = client.get("/v1/workspaces", headers=token("alice")).json()["workspaces"]
    assert [(w["name"], len(w["items"])) for w in mine] == [("Semis", 1)]


@pytest.mark.parametrize("body", [
    {"name": "x", "user_id": "alice"},
    {"name": "x", "owner": "alice"},
])
def test_a_user_id_in_the_body_is_refused(world, token, body):
    client, ws, _ = world
    r = client.post("/v1/workspaces", json=body, headers=token("bob"))
    assert r.status_code == 422
    assert ws.workspaces == {}


def test_user_comes_only_from_the_token(world, token):
    client, ws, _ = world
    assert client.get("/v1/workspaces").status_code == 401
    assert client.get("/v1/workspaces", headers={"Authorization": "Bearer nope"}
                      ).status_code == 401
    assert client.get("/v1/workspaces?user_id=alice", headers=token("bob")).json() == {
        "workspaces": []}
    new_workspace(client, token, user="bob", name="B")
    assert {w["user_id"] for w in ws.workspaces.values()} == {"bob"}


@pytest.mark.parametrize("path", ["/v1/workspaces/1", "/v1/workspaces/..%2F..",
                                  "/v1/workspace-items/not-a-uuid",
                                  "/v1/workspace-items/1%20OR%201=1"])
def test_malformed_ids_422(world, token, path):
    client, *_ = world
    assert client.get(path if "items" in path else path + "/x", headers=token("alice")
                      ).status_code in (404, 405, 422)
    # "..%2F.." is decoded before routing and matches no route at all
    assert client.delete(path, headers=token("alice")).status_code in (
        (404,) if "%2F" in path else (422,))


BAD_STATES = [
    ("chart", {"layers": []}),  # nothing to save
    ("chart", {**CHART, "layers": CHART["layers"] * 13}),
    ("chart", {**CHART, "range": 7}),
    ("chart", {**CHART, "range": "all"}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "color": 8}]}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "name": "a\x00b"}]}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "name": "x" * 81}]}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "type": "pie"}]}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "transform": "log"}]}),
    ("chart", {**CHART, "title": "x" * 201}),
    ("chart", {**CHART, "extra": 1}),
    ("chart", {**CHART, "layers": [{"kind": "metric", "companyId": "NVDA",
                                    "metric": "revenue"}]}),
    ("chart", {**CHART, "layers": [{"kind": "metric", "companyId": NVDA,
                                    "metric": "Revenue; DROP"}]}),
    ("chart", {**CHART, "layers": [{"kind": "metric", "companyId": NVDA}]}),
    ("chart", {**CHART, "layers": [{"kind": "formula", "formula": "x" * 501}]}),
    ("chart", {**CHART, "layers": [{"kind": "formula", "formula": "a", "metric": "revenue"}]}),
    ("chart", {**CHART, "layers": [{**CHART["layers"][0], "id": 1}]}),
    ("comps", {"peers": [AAPL]}),  # no target
    ("comps", {"target": NVDA, "peers": [AAPL] * 2}),
    ("comps", {"target": NVDA, "peers": [NVDA]}),
    ("comps", {"target": NVDA, "peers": [f"{i:010d}" for i in range(21)]}),
    ("comps", {"target": NVDA, "cols": ["price"]}),
    ("comps", {"target": "../etc"}),
    ("screen", {"sort": "revenue_ttm; DROP"}),
    ("screen", {"margin": 1000}),
    ("screen", {"q": "x" * 101}),
    ("screen", {"page": 3}),
    ("screen", {"band": "huge"}),
]


@pytest.mark.parametrize("kind, state", BAD_STATES)
def test_malformed_states_are_refused(world, token, kind, state):
    client, ws, _ = world
    wid = new_workspace(client, token)
    r = client.post(f"/v1/workspaces/{wid}/items",
                    json={"kind": kind, "name": "x", "state": state}, headers=token("alice"))
    assert r.status_code == 422, state
    assert ws.items == {}


def test_save_over_validates_against_the_items_kind(world, token):
    client, *_ = world
    wid = new_workspace(client, token)
    iid = new_item(client, token, wid)["item_id"]
    r = client.put(f"/v1/workspace-items/{iid}", json={"state": COMPS}, headers=token("alice"))
    assert r.status_code == 422  # a chart can't become a comps set
    assert client.put(f"/v1/workspace-items/{iid}", json={}, headers=token("alice")
                      ).status_code == 422


@pytest.mark.parametrize("body", [
    {"kind": "notes", "name": "x", "state": {}},
    {"kind": "chart", "name": "", "state": CHART},
    {"kind": "chart", "name": "   ", "state": CHART},
    {"kind": "chart", "name": "x" * 121, "state": CHART},
    {"kind": "chart", "name": "bell\x07", "state": CHART},
    {"kind": "chart", "name": "x", "state": [CHART]},
    {"kind": "chart", "name": "x"},
])
def test_malformed_item_bodies(world, token, body):
    client, *_ = world
    wid = new_workspace(client, token)
    r = client.post(f"/v1/workspaces/{wid}/items", json=body, headers=token("alice"))
    assert r.status_code == 422


@pytest.mark.parametrize("name", ["", " ", "x" * 101, "tab\tname", "a\nb"])
def test_malformed_workspace_names(world, token, name):
    client, *_ = world
    assert client.post("/v1/workspaces", json={"name": name}, headers=token("alice")
                       ).status_code == 422


def test_oversized_and_unsized_bodies_are_refused_before_reading(world, token):
    client, ws, _ = world
    wid = new_workspace(client, token)
    big = {"kind": "chart", "name": "x",
           "state": {**CHART, "title": "x" * (MAX_BODY_BYTES + 1)}}
    r = client.post(f"/v1/workspaces/{wid}/items", json=big, headers=token("alice"))
    assert r.status_code == 413
    r = client.post("/v1/workspaces", headers={**token("alice"), "Transfer-Encoding": "chunked"},
                    content=iter([json.dumps({"name": "x"}).encode()]))
    assert r.status_code == 411
    assert ws.items == {} and len(ws.workspaces) == 1


def test_state_size_cap():
    many = {**CHART, "layers": [{"kind": "formula", "formula": "x" * 500}] * 12,
            "title": "t" * 200, "subtitle": "s" * 200, "range": "custom",
            "start": "2016-01-01", "end": "2026-06-30", "frequency": "annual", "deck": True,
            "markers": True}
    validate_state("chart", many)  # the largest valid chart fits
    with pytest.raises(StateError):
        validate_state("notes", {})


def test_chart_range_frequency_and_deck_settings():
    saved = validate_state("chart", {**CHART, "range": "custom", "start": "2020-01-01",
                                     "frequency": "annual", "deck": True,
                                     "subtitle": " USD, fiscal years "})
    assert (saved["range"], saved["start"], saved["frequency"], saved["deck"]) == (
        "custom", "2020-01-01", "annual", True)
    assert saved["subtitle"] == "USD, fiscal years" and "end" not in saved
    for bad in ({"start": "2020-1-1"}, {"end": "yesterday"}, {"frequency": "monthly"},
                {"range": 7}, {"subtitle": "x" * 201}, {"from": "2020-01-01"}):
        with pytest.raises(StateError):
            validate_state("chart", {**CHART, **bad})


def test_limits(world, token):
    client, *_ = world
    for _ in range(MAX_WORKSPACES):
        new_workspace(client, token, name="w")
    r = client.post("/v1/workspaces", json={"name": "one more"}, headers=token("alice"))
    assert r.status_code == 409 and str(MAX_WORKSPACES) in r.json()["detail"]
    # bob's caps are his own
    new_workspace(client, token, user="bob")


def test_unknown_user_409(world, token):
    client, *_ = world
    r = client.post("/v1/workspaces", json={"name": "x"}, headers=token("mallory"))
    assert r.status_code == 409


def test_unavailable_without_an_app_database(token):
    client = TestClient(create_app(FakeAuthorizer({}), MetaStore(), SECRET))
    assert client.get("/v1/workspaces", headers=token("alice")).status_code == 503
    assert client.get("/v1/recent", headers=token("alice")).status_code == 503


def test_saved_items_never_grant_access(world, token):
    """A chart saved while alice could read a company opens after she lost it, but its data
    is still refused like any company she can't read; the saved-work routes never consult
    the authorizer or read data."""
    client, ws, store = world
    chart = {**CHART, "layers": [{**CHART["layers"][0], "companyId": AAPL}]}
    wid = new_workspace(client, token)
    iid = new_item(client, token, wid, state=chart)["item_id"]
    store.calls.clear()
    opened = client.post(f"/v1/workspace-items/{iid}/open", headers=token("alice")).json()
    assert opened["state"]["layers"][0]["companyId"] == AAPL
    assert store.calls == []  # no data read for the saved item
    r = client.get(f"/v1/companies/{AAPL}/financials", headers=token("alice"))
    assert r.status_code == 403
    r = client.post("/v1/formulas/evaluate", headers=token("alice"),
                    json={"formula": "revenue", "default_company": AAPL})
    assert r.status_code == 403


def test_recent_items_and_filings_newest_first(world, token):
    client, _, store = world
    wid = new_workspace(client, token)
    a = new_item(client, token, wid, name="A")["item_id"]
    b = new_item(client, token, wid, kind="comps", state=COMPS, name="B")["item_id"]
    new_item(client, token, wid, name="never opened")
    for iid in (a, b, a):
        client.post(f"/v1/workspace-items/{iid}/open", headers=token("alice"))
    for acc in (ACC, "0000320193-26-000001"):
        r = client.put("/v1/recent/filings", headers=token("alice"),
                       json={"company_id": NVDA, "accession_no": acc, "section_key": "II-1A"})
        assert r.status_code == 200
    recent = client.get("/v1/recent", headers=token("alice")).json()
    assert [i["name"] for i in recent["items"]] == ["A", "B"]
    assert recent["items"][0]["workspace_name"] == "Semis"
    assert [f["accession_no"] for f in recent["filings"]] == ["0000320193-26-000001", ACC]
    assert recent["filings"][0]["form"] == "10-Q" and recent["filings"][0]["name"]
    # per user
    assert client.get("/v1/recent", headers=token("bob")).json() == {"items": [],
                                                                     "filings": []}


@pytest.mark.parametrize("body", [
    {"company_id": "NVDA", "accession_no": ACC},
    {"company_id": NVDA, "accession_no": "1-2-3"},
    {"company_id": NVDA, "accession_no": ACC, "section_key": "<script>"},
    {"company_id": NVDA, "accession_no": ACC, "user_id": "bob"},
])
def test_recent_filing_bodies_are_validated(world, token, body):
    client, *_ = world
    assert client.put("/v1/recent/filings", json=body, headers=token("alice")
                      ).status_code == 422


def test_saved_work_is_not_streamed():
    """Owner-only workspaces grant nothing, so Debezium doesn't capture them (yet: sharing
    would add an OpenFGA type and authz-sync mapping)."""
    from pathlib import Path

    connector = json.loads((Path(__file__).parents[3] / "streaming/debezium/app-connector.json")
                           .read_text())
    for table in ("workspaces", "workspace_items", "recent_filings"):
        assert table not in connector["table.include.list"]
