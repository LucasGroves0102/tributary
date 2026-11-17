"""POST /v1/sql and GET /v1/sql/schema without a database: what reaches the Store, the answers
for each refusal and limit, the budget, API keys, and the notebook item state. The database
cases (RLS, the role, the limits for real) are in test_sql_workspace_db.py."""

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access, AuthzUnavailable
from query_api.sql_workspace import (
    Busy,
    Failed,
    Limits,
    Result,
    Tampered,
    TooCostly,
    cell,
)
from query_api.workspaces import MAX_NOTEBOOK_CELLS, StateError, validate_state

from .conftest import SECRET
from .test_api_keys import FakeKeys, bearer, make_key

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
USER = "alice"


class FakeUsage:
    """AppSqlUsage in memory: seconds per (user, day)."""

    def __init__(self, seconds=None, fail=False):
        self.seconds = dict(seconds or {})
        self.fail = fail

    def used(self, user_id, day):
        if self.fail:
            raise psycopg.OperationalError("down")
        return self.seconds.get((user_id, day), 0.0)

    def add(self, user_id, day, seconds):
        self.seconds[(user_id, day)] = self.seconds.get((user_id, day), 0.0) + seconds


class SqlStore:
    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome

    def run_sql(self, user_id, checked, limits):
        self.calls.append((user_id, checked.sql, limits))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome or Result([{"name": "n", "type": "number"}], [[1]], None, 12.0, 0.25,
                                      ["financials"])

    def sql_schema(self):
        self.calls.append(("schema",))
        return [{"name": "financials", "kind": "view", "description": "d", "arguments": None,
                 "columns": [{"name": "value", "type": "numeric", "description": "v"}]}]


class Fga:
    def __init__(self, down=False):
        self.down = down

    def can(self, *_):
        return True

    def access(self, user_id):
        if self.down:
            raise AuthzUnavailable("down")
        return Access(frozenset(), {})


def make(store=None, usage=None, fga=None, keys=None, limits=None):
    store = store or SqlStore()
    usage = FakeUsage() if usage is None else usage
    app = create_app(fga or Fga(), store, SECRET, sql_usage=usage, api_keys=keys,
                     sql_limits=limits or Limits(), clock=lambda: NOW)
    c = TestClient(app)
    c.store, c.usage = store, usage  # type: ignore[attr-defined]
    return c


def post(c, token, query, user=USER):
    return c.post("/v1/sql", json={"sql": query}, headers=token(user))


def test_a_query_runs_as_the_signed_in_user_and_is_counted(token):
    c = make()
    r = post(c, token, "select 1 as n -- note")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == [[1]] and body["row_count"] == 1 and body["truncated"] is None
    assert body["sql"] == "SELECT 1 AS n"  # what ran: the checked statement, deparsed
    assert c.store.calls[0][:2] == (USER, "SELECT 1 AS n")
    assert c.usage.seconds == {(USER, NOW.date()): 0.25}
    assert body["budget"] == {"used_seconds": 0.2, "limit_seconds": 600}


@pytest.mark.parametrize("query", [
    "SET app.user_id = 'bob'", "select set_config('app.user_id', 'bob', true)",
    "select current_setting('app.user_id')", "select pg_sleep(20)", "select * from pg_class",
    "select * from gold.quarterly_financials", "select 1; select 2", "select 1 \x00",
])
def test_a_refused_query_never_reaches_the_store_or_the_budget(token, query):
    c = make()
    r = post(c, token, query)
    assert r.status_code == 422 and r.json()["code"] == "refused"
    assert c.store.calls == [] and c.usage.seconds == {}


def test_a_syntax_error_says_where(token):
    r = post(make(), token, "select from where")
    assert r.status_code == 422
    assert r.json()["position"] == 12 and "syntax error" in r.json()["detail"]


@pytest.mark.parametrize(("outcome", "status", "code"), [
    (Busy("user"), 429, "busy_user"), (Busy("slots"), 429, "busy_slots"),
    (TooCostly(9e10, 1e3, Limits()), 422, "too_costly"),
    (TooCostly(1e5, 2e9, Limits()), 422, "too_costly"),
    (Failed("timeout", "the query ran longer than 15 seconds", "57014", 15.0), 422, "timeout"),
    (Failed("sql_error", "division by zero", "22012", 0.01), 422, "sql_error"),
])
def test_limits_and_failures_answer_with_a_code(token, outcome, status, code):
    c = make(SqlStore(outcome))
    r = post(c, token, "select 1")
    assert r.status_code == status and r.json()["code"] == code
    spent = c.usage.seconds.get((USER, NOW.date()), 0.0)
    assert spent == (outcome.seconds if isinstance(outcome, Failed) else 0.0)


def test_a_tampered_session_returns_nothing(token):
    r = post(make(SqlStore(Tampered())), token, "select 1")
    assert r.status_code == 500 and "rows" not in r.json()


def test_the_database_down_is_a_503(token):
    r = post(make(SqlStore(psycopg.OperationalError("gone"))), token, "select 1")
    assert r.status_code == 503
    r = post(make(usage=FakeUsage(fail=True)), token, "select 1")
    assert r.status_code == 503


def test_openfga_down_is_a_503(token):
    c = make(fga=Fga(down=True))
    assert post(c, token, "select 1").status_code == 503
    assert c.store.calls == []


def test_without_the_app_database_sql_is_off(token):
    c = TestClient(create_app(Fga(), SqlStore(), SECRET))
    assert post(c, token, "select 1").status_code == 503


def test_the_budget_is_checked_before_running(token):
    c = make(usage=FakeUsage({(USER, NOW.date()): 600.0}))
    r = post(c, token, "select 1")
    assert r.status_code == 429 and r.json()["code"] == "budget"
    assert c.store.calls == []
    # another user, or tomorrow, runs
    assert post(c, token, "select 1", user="bob").status_code == 200


def test_api_keys_cannot_run_sql(token):
    keys = FakeKeys(users=(USER,))
    c = make(keys=keys)
    key = make_key(c, token, user=USER, scopes=("read", "write"))["key"]
    r = c.post("/v1/sql", json={"sql": "select 1"}, headers=bearer(key))
    assert r.status_code == 403
    assert c.get("/v1/sql/schema", headers=bearer(key)).status_code == 403
    assert c.store.calls == []


def test_schema_is_cached_and_lists_examples(token):
    c = make()
    for _ in range(2):
        r = c.get("/v1/sql/schema", headers=token(USER))
        assert r.status_code == 200
    assert c.store.calls == [("schema",)]
    body = r.json()
    assert body["objects"][0]["name"] == "financials"
    assert len(body["examples"]) >= 3 and body["limits"]["rows"] == 10_000


def test_signed_out_is_refused():
    c = make()
    assert c.post("/v1/sql", json={"sql": "select 1"}).status_code == 401
    assert c.get("/v1/sql/schema").status_code == 401


def test_cells_keep_exact_numbers():
    assert cell(Decimal("394328000000")) == "394328000000"
    assert cell(Decimal("1E+3")) == "1000"
    assert cell(Decimal("0.4231")) == "0.4231"
    assert cell(2 ** 60) == str(2 ** 60) and cell(5) == 5
    assert cell(float("nan")) == "nan"
    assert cell([Decimal("1.5"), None]) == ["1.5", None]


# --- the notebook item ---------------------------------------------------------------------------

def notebook(*cells):
    return {"cells": list(cells)}


def test_a_notebook_holds_sql_text_and_charts_never_results():
    state = validate_state("notebook", notebook(
        {"id": "a1", "kind": "text", "text": "# Margins\nWhy they moved"},
        {"id": "b2", "kind": "sql", "sql": "select 1"},
        {"id": "c3", "kind": "chart", "chart": {"source": "b2", "x": "period_end",
                                                "y": ["value"]}}))
    assert [c["kind"] for c in state["cells"]] == ["text", "sql", "chart"]
    assert state["cells"][2]["chart"]["type"] == "line"


@pytest.mark.parametrize("bad", [
    notebook({"id": "a", "kind": "sql", "sql": "select 1", "rows": [[1]]}),  # no results
    notebook({"id": "a", "kind": "sql", "text": "x"}),
    notebook({"id": "a", "kind": "sql", "sql": "select 1", "text": "x"}),
    notebook({"id": "a", "kind": "sql", "sql": "x"}, {"id": "a", "kind": "text", "text": "y"}),
    notebook({"id": "c", "kind": "chart", "chart": {"source": "zz", "x": "a", "y": ["b"]}}),
    notebook({"id": "A!", "kind": "text", "text": "x"}),
    notebook({"id": "a", "kind": "text", "text": "bell\x07"}),
    notebook(*({"id": f"c{i}", "kind": "text", "text": "x"}
               for i in range(MAX_NOTEBOOK_CELLS + 1))),
    notebook(*({"id": f"c{i}", "kind": "sql", "sql": "x" * 19_000} for i in range(4))),
])
def test_a_notebook_out_of_shape_is_refused(bad):
    with pytest.raises(StateError):
        validate_state("notebook", bad)
