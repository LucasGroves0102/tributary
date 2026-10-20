"""FastAPI instrumentation: /metrics, per-route request metrics and access logs, request IDs."""

import logging
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tributary_telemetry.logs import current_request_id, request_id

if TYPE_CHECKING:
    import httpx

REQUEST_ID_HEADER = "X-Request-ID"
UNMATCHED = "unmatched"  # route label for paths no route matched
QUIET = {"/health", "/metrics"}  # probes and scrapes: counted, not logged

REQUESTS = Counter("tributary_http_requests_total", "HTTP requests",
                   ["route", "method", "status"])
LATENCY = Histogram("tributary_http_request_duration_seconds",
                    "HTTP request duration, until the last body byte", ["route", "method"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120))

log = logging.getLogger("tributary.http")


class RequestMiddleware:
    """Takes X-Request-ID from the request (or makes one), returns it, and records the request
    under its route template."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with request_id(Headers(scope=scope).get(REQUEST_ID_HEADER)) as rid:
            status = 500

            async def send_with_id(message: Message) -> None:
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
                await send(message)

            start = time.perf_counter()
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                elapsed = time.perf_counter() - start
                route = getattr(scope.get("route"), "path", UNMATCHED)
                REQUESTS.labels(route, scope["method"], str(status)).inc()
                LATENCY.labels(route, scope["method"]).observe(elapsed)
                if route not in QUIET:
                    log.info("%s %s %d", scope["method"], scope["path"], status,
                             extra={"method": scope["method"], "route": route,
                                    "path": scope["path"], "status": status,
                                    "duration_ms": round(elapsed * 1000, 1)})


def instrument(app: FastAPI) -> None:
    """Add RequestMiddleware and GET /metrics."""
    app.add_middleware(RequestMiddleware)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def request_id_headers() -> dict[str, str]:
    """{X-Request-ID: current ID}, or {} outside a request."""
    rid = current_request_id()
    return {REQUEST_ID_HEADER: rid} if rid is not None else {}


def forward_request_id(request: "httpx.Request") -> None:
    """httpx request hook: pass the current request ID to the service being called."""
    request.headers.update(request_id_headers())
