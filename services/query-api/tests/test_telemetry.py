"""Metrics, request IDs and log fields on the Query API."""

import json
import logging

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from query_api.app import create_app
from tributary_telemetry.logs import JsonFormatter, RequestIdFilter

from .conftest import SECRET
from .test_api import GRANTS, NVDA, TSLA, FakeAuthorizer, FakeStore

FINANCIALS = "/v1/companies/{company_id}/financials"


def checks(tier, result):
    return REGISTRY.get_sample_value("tributary_permission_checks_total",
                                     {"tier": tier, "result": result}) or 0


def requests(route, status):
    return REGISTRY.get_sample_value("tributary_http_requests_total",
                                     {"route": route, "method": "GET", "status": status}) or 0


def test_permission_checks_count_allowed_denied_and_errors(token):
    before = {r: checks("financials", r) for r in ("allowed", "denied", "error")}
    c = TestClient(create_app(FakeAuthorizer(GRANTS), FakeStore(), SECRET))
    c.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    c.get(f"/v1/companies/{TSLA}/financials", headers=token("alice"))
    down = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), FakeStore(), SECRET))
    down.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    assert {r: checks("financials", r) - n for r, n in before.items()} == \
        {"allowed": 1, "denied": 1, "error": 1}


def test_requests_are_counted_by_route_template(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS), FakeStore(), SECRET))
    ok, denied = requests(FINANCIALS, "200"), requests(FINANCIALS, "403")
    c.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    c.get(f"/v1/companies/{TSLA}/financials", headers=token("alice"))
    assert requests(FINANCIALS, "200") == ok + 1
    assert requests(FINANCIALS, "403") == denied + 1


def test_metrics_endpoint_is_served_but_not_documented():
    c = TestClient(create_app(FakeAuthorizer(GRANTS), FakeStore(), SECRET))
    assert "tributary_permission_checks_total" in c.get("/metrics").text
    assert "/metrics" not in c.get("/openapi.json").json()["paths"]


def test_denial_is_logged_with_the_callers_request_id(token, caplog):
    caplog.handler.setFormatter(JsonFormatter())
    caplog.handler.addFilter(RequestIdFilter())
    c = TestClient(create_app(FakeAuthorizer(GRANTS), FakeStore(), SECRET))
    with caplog.at_level(logging.INFO):
        r = c.get(f"/v1/companies/{TSLA}/financials",
                  headers={**token("alice"), "X-Request-ID": "agent-abc"})
    assert r.headers["X-Request-ID"] == "agent-abc"
    lines = [json.loads(caplog.handler.format(rec)) for rec in caplog.records]
    denied = next(x for x in lines if x["message"] == "permission denied")
    assert denied["request_id"] == "agent-abc"
    assert (denied["user_id"], denied["tier"], denied["company_id"]) == \
        ("alice", "financials", TSLA)
    access = next(x for x in lines if x["logger"] == "tributary.http")
    assert access["request_id"] == "agent-abc" and access["status"] == 403
    assert "Bearer" not in caplog.text
