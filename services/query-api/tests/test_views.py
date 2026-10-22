"""Customizable views (app migration 006): the routes with an in-memory store keyed by owner
(test_views_db.py runs the real one). Adversarial cases: unknown cards and columns, a user id
in the body, another user's preferences."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.screener import COLUMNS
from query_api.views import (
    DEFAULT_SCREENER,
    DEFAULT_WATCHLIST,
    DEFAULTS,
    HOME_CARDS,
    MAX_SCREENER_COLUMNS,
    UnknownUser,
    validate_view,
)

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore


class FakeViews:
    def __init__(self, users=("alice", "bob")):
        self.users = set(users)
        self.rows: dict[tuple[str, str], dict] = {}

    def all(self, user_id):
        return {v: s for (u, v), s in self.rows.items() if u == user_id}

    def put(self, user_id, view, state):
        if user_id not in self.users:
            raise UnknownUser(user_id)
        self.rows[(user_id, view)] = state

    def delete(self, user_id, view):
        return self.rows.pop((user_id, view), None) is not None


@pytest.fixture
def world():
    views = FakeViews()
    app = create_app(FakeAuthorizer({}), FakeStore(), SECRET, views=views)
    return TestClient(app), views


HOME = {"cards": [{"id": "watchlist"}, {"id": "status", "hidden": True}, {"id": "notes"}]}


def test_defaults_until_saved_then_saved_then_reset(world, token):
    client, views = world
    body = client.get("/v1/views", headers=token("alice")).json()
    assert body["views"] == {"home": None, "screener": None, "watchlist": None}
    assert body["defaults"] == DEFAULTS
    assert body["options"]["home_cards"] == list(HOME_CARDS)
    assert {c["key"] for c in body["options"]["columns"]} == set(COLUMNS)
    r = client.put("/v1/views/home", json={"state": HOME}, headers=token("alice"))
    assert r.status_code == 200
    assert r.json()["state"]["cards"][1] == {"id": "status", "hidden": True}
    r = client.put("/v1/views/screener", json={"state": {"columns": ["net_income_ttm",
                                                                     "revenue_ttm"]}},
                   headers=token("alice"))
    assert r.status_code == 200
    got = client.get("/v1/views", headers=token("alice")).json()["views"]
    assert got["home"]["cards"][0] == {"id": "watchlist", "hidden": False}
    assert got["screener"] == {"columns": ["net_income_ttm", "revenue_ttm"]}
    assert got["watchlist"] is None
    # bob sees none of it
    assert client.get("/v1/views", headers=token("bob")).json()["views"]["home"] is None
    r = client.delete("/v1/views/home", headers=token("alice"))
    assert r.json() == {"view": "home", "state": DEFAULTS["home"], "reset": True}
    assert client.get("/v1/views", headers=token("alice")).json()["views"]["home"] is None
    assert client.delete("/v1/views/home", headers=token("alice")).status_code == 200  # again
    assert ("alice", "screener") in views.rows


@pytest.mark.parametrize("view, state, message", [
    ("home", {"cards": [{"id": "ads"}]}, "cards.0.id"),
    ("home", {"cards": [{"id": "status"}, {"id": "status"}]}, "repeat"),
    ("home", {"cards": []}, "at least 1"),
    ("home", {"cards": [{"id": "status", "hidden": "yes please"}]}, "hidden"),
    ("home", {"cards": [{"id": "status"}], "user_id": "bob"}, "Extra inputs"),
    ("screener", {"columns": []}, "1 to"),
    ("screener", {"columns": ["revenue_ttm; DROP TABLE x"]}, "unknown columns"),
    ("screener", {"columns": ["revenue_ttm", "revenue_ttm"]}, "repeat"),
    ("screener", {"columns": sorted(COLUMNS)[:MAX_SCREENER_COLUMNS + 1]}, "1 to"),
    ("watchlist", {"columns": sorted(COLUMNS)[:9]}, "1 to 8"),
    ("watchlist", {"columns": "revenue_ttm"}, "list"),
])
def test_preferences_are_validated(world, token, view, state, message):
    client, views = world
    r = client.put(f"/v1/views/{view}", json={"state": state}, headers=token("alice"))
    assert r.status_code == 422 and message in r.text, r.text
    assert views.rows == {}


def test_unknown_view_and_user_in_body(world, token):
    client, _ = world
    assert client.put("/v1/views/admin", json={"state": {}},
                      headers=token("alice")).status_code == 422
    r = client.put("/v1/views/home", json={"state": HOME, "user_id": "bob"},
                   headers=token("alice"))
    assert r.status_code == 422


def test_needs_a_token_and_an_app_database(world, token):
    client, _ = world
    assert client.get("/v1/views").status_code == 401
    assert client.put("/v1/views/home", json={"state": HOME}).status_code == 401
    no_db = TestClient(create_app(FakeAuthorizer({}), FakeStore(), SECRET))
    assert no_db.get("/v1/views", headers=token("alice")).status_code == 503


def test_unknown_user_is_409(world, token):
    client, _ = world
    r = client.put("/v1/views/home", json={"state": HOME}, headers=token("mallory"))
    assert r.status_code == 409


def test_a_stale_saved_row_reads_as_the_default(world, token):
    client, views = world
    views.rows[("alice", "screener")] = {"columns": ["retired_metric_ttm"]}
    assert client.get("/v1/views", headers=token("alice")).json()["views"]["screener"] is None


def test_defaults_are_valid_and_match_the_web_app():
    for view, state in DEFAULTS.items():
        assert validate_view(view, state) == state
    web = Path(__file__).parents[2] / "web/src"
    screen = (web / "companies/screen.ts").read_text()
    api = (web / "api.ts").read_text()
    assert all(f'key: "{c}"' in screen for c in DEFAULT_SCREENER)
    assert all(f'"{c}"' in api for c in DEFAULT_WATCHLIST)
    model = (web / "views/model.ts").read_text()
    assert ", ".join(f'"{c}"' for c in HOME_CARDS) in model  # same cards, same order


def test_preferences_are_not_streamed():
    connector = json.loads((Path(__file__).parents[3] / "streaming/debezium/app-connector.json")
                           .read_text())
    assert "user_views" not in connector["table.include.list"]
