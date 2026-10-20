import json
import logging

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from tributary_telemetry import request_id
from tributary_telemetry.http import forward_request_id, instrument
from tributary_telemetry.logs import JsonFormatter, RequestIdFilter


def app():
    a = FastAPI()
    instrument(a)

    @a.get("/items/{item_id}")
    def item(item_id: str) -> dict:
        if item_id == "missing":
            raise HTTPException(404)
        logging.getLogger("test").info("item")
        return {"item_id": item_id}

    return TestClient(a)


def requests(route, status):
    return REGISTRY.get_sample_value("tributary_http_requests_total",
                                     {"route": route, "method": "GET", "status": status}) or 0


def test_counts_requests_by_route_template_and_status():
    c = app()
    ok, missing = requests("/items/{item_id}", "200"), requests("/items/{item_id}", "404")
    unmatched = requests("unmatched", "404")
    c.get("/items/1")
    c.get("/items/2")
    c.get("/items/missing")
    c.get("/nowhere")
    assert requests("/items/{item_id}", "200") == ok + 2
    assert requests("/items/{item_id}", "404") == missing + 1
    assert requests("unmatched", "404") == unmatched + 1
    assert (REGISTRY.get_sample_value("tributary_http_request_duration_seconds_count",
                                      {"route": "/items/{item_id}", "method": "GET"}) or 0) >= 3


def test_metrics_endpoint_serves_prometheus_text():
    r = app().get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "tributary_http_requests_total" in r.text


def test_request_id_is_echoed_or_generated():
    c = app()
    assert c.get("/items/1", headers={"X-Request-ID": "web-42"}).headers["X-Request-ID"] == "web-42"
    generated = c.get("/items/1", headers={"X-Request-ID": "bad id"}).headers["X-Request-ID"]
    assert generated != "bad id" and len(generated) == 32
    assert len(c.get("/items/1").headers["X-Request-ID"]) == 32


def test_logs_carry_the_request_id(caplog):
    caplog.handler.setFormatter(JsonFormatter())
    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.INFO):
        app().get("/items/7", headers={"X-Request-ID": "trace-7"})
    lines = [json.loads(caplog.handler.format(r)) for r in caplog.records]
    handler_line = next(x for x in lines if x["logger"] == "test")
    access = next(x for x in lines if x["logger"] == "tributary.http")
    assert handler_line["request_id"] == access["request_id"] == "trace-7"
    assert access["route"] == "/items/{item_id}" and access["status"] == 200
    assert access["path"] == "/items/7"


def test_forward_request_id_sets_the_header_inside_a_request():
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: seen.append(req.headers.get("X-Request-ID")) or httpx.Response(200)),
        event_hooks={"request": [forward_request_id]})
    client.get("http://query-api/v1/me")
    with request_id("agent-1"):
        client.get("http://query-api/v1/me")
    assert seen == [None, "agent-1"]
