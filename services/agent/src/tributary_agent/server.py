"""Agent HTTP API: POST /v1/ask streams the agent's events as server-sent events; POST
/v1/reports writes a research note from one of the user's workspaces (report.py)."""

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import anthropic
import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from tributary_agent.agent import Agent, Answer
from tributary_agent.report import (
    REPORT_QUESTIONS,
    REPORT_ROUNDS,
    REPORT_RULES,
    Reporter,
    ReportError,
)
from tributary_agent.telemetry import record_answer, record_error
from tributary_agent.tools import MetricTools, QueryApi
from tributary_agent.ui import UiActions, UiContext
from tributary_telemetry import configure_logging
from tributary_telemetry.http import forward_request_id, instrument, request_id_headers

log = logging.getLogger("tributary_agent")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default=[], max_length=10)
    # the web app's screen: untrusted, size-capped, never identity (ui.UiContext)
    context: UiContext | None = None


class ReportRequest(BaseModel):
    """Write a report from this workspace (the user's own: the Query API checks), or write
    the report `report_id` in it again."""
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID
    report_id: UUID | None = None


class LimitExceeded(Exception):
    pass


class Limits:
    """Questions per hour per user and per client IP, and model spend per UTC day across all
    users."""

    def __init__(self, per_hour: int, per_ip_per_hour: int, daily_budget_usd: float,
                 clock: Callable[[], float] = time.time) -> None:
        self._per_hour = per_hour
        self._per_ip = per_ip_per_hour
        self._budget = daily_budget_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._by_user: dict[str, deque[float]] = defaultdict(deque)
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)
        self._swept = clock()
        self._spent: dict[str, float] = defaultdict(float)  # UTC date -> USD

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).date().isoformat()

    @staticmethod
    def _recent(asked: deque[float], now: float) -> deque[float]:
        while asked and asked[0] <= now - 3600:
            asked.popleft()
        return asked

    def _sweep(self, now: float) -> None:
        """Drops users and IPs with no questions in the last hour, at most once an hour."""
        if now - self._swept < 3600:
            return
        self._swept = now
        for windows in (self._by_user, self._by_ip):
            for key in [k for k, v in windows.items() if not self._recent(v, now)]:
                del windows[key]

    def acquire(self, user_id: str, client_ip: str, questions: int = 1) -> None:
        """Takes `questions` from the hour's allowances (a report counts as several), or none
        of them."""
        now = self._clock()
        with self._lock:
            if self._spent[self._today()] >= self._budget:
                raise LimitExceeded("The demo's daily model budget is used up. Try tomorrow.")
            self._sweep(now)
            by_ip = self._recent(self._by_ip[client_ip], now)
            by_user = self._recent(self._by_user[user_id], now)
            report = f"A report counts as {questions} questions. " if questions > 1 else ""
            if len(by_ip) + questions > self._per_ip:
                raise LimitExceeded(f"{report}Limit of {self._per_ip} questions per hour "
                                    "reached. Try again later.")
            if len(by_user) + questions > self._per_hour:
                left = self._per_hour - len(by_user)
                if report and left > 0:
                    raise LimitExceeded(f"{report}You have {left} of {self._per_hour} left this "
                                        "hour. Ask a question instead, or try again later.")
                raise LimitExceeded(f"You've reached the limit of {self._per_hour} questions "
                                    "per hour. Try again later.")
            by_ip.extend([now] * questions)
            by_user.extend([now] * questions)

    def spend(self, usd: float) -> None:
        with self._lock:
            self._spent[self._today()] += usd


def answer_json(a: Answer) -> dict:
    return {"text": a.text, "stop": a.stop, "model": a.model, "rounds": a.rounds,
            "tool_calls": [asdict(c) for c in a.tool_calls], "usage": asdict(a.usage),
            "cost_usd": a.cost_usd, "citations": list(a.citations.values()),
            "verification": a.verification, "actions": a.actions}


def _no_quota(tok: str, questions: int = 1) -> None:
    return None


def create_app(make_agent: Callable[[str], Agent], who_is: Callable[[str], str],
               limits: Limits, client_ip_header: str = "X-Client-IP",
               take_quota: Callable[[str, int], None] = _no_quota,
               make_reporter: Callable[[str], Reporter] | None = None) -> FastAPI:
    """make_agent(token) builds an agent reading as the token's user; who_is(token) returns
    that user's id or raises HTTPException. The client IP is read from client_ip_header, set
    by the web proxy, else the connection's peer address. take_quota(token, n) takes n of the
    user's persistent question allowances (demo sandboxes, Query API) or raises
    HTTPException (429 with the message to show). make_reporter(token): reports as the
    token's user (report.py); None leaves /v1/reports out."""
    app = FastAPI(title="Tributary Agent", version="1")
    instrument(app)
    bearer = HTTPBearer(auto_error=False)

    def token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
        if creds is None:
            raise HTTPException(401, "missing bearer token")
        return creds.credentials

    def client_ip(request: Request) -> str:
        ip = request.headers.get(client_ip_header, "").strip()
        if ip:
            return ip
        return request.client.host if request.client else "unknown"

    def sse(events: Iterator[dict], user_id: str, kind: str) -> StreamingResponse:
        def stream() -> Iterator[str]:
            try:
                for event in events:
                    if event["type"] == "done":
                        answer = event["answer"]
                        limits.spend(answer.cost_usd or 0)
                        record_answer(answer)
                        log.info("user=%s %s rounds=%d cost=%.4f stop=%s", user_id, kind,
                                 answer.rounds, answer.cost_usd or 0, answer.stop,
                                 extra={"user_id": user_id, "kind": kind,
                                        "rounds": answer.rounds, "stop": answer.stop,
                                        "cost_usd": answer.cost_usd,
                                        "input_tokens": answer.usage.input_tokens,
                                        "output_tokens": answer.usage.output_tokens,
                                        "tool_calls": len(answer.tool_calls)})
                        event = {"type": "done", "answer": answer_json(answer)}
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except anthropic.APIError as e:
                record_error()
                log.warning("model error for %s: %s", user_id, e, extra={"user_id": user_id})
                yield f"data: {json.dumps({'type': 'error', 'message': 'model unavailable'})}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/ask")
    def ask(body: AskRequest, tok: Annotated[str, Depends(token)],
            ip: Annotated[str, Depends(client_ip)]) -> StreamingResponse:
        user_id = who_is(tok)
        try:
            limits.acquire(user_id, ip)
        except LimitExceeded as e:
            raise HTTPException(429, str(e)) from None
        take_quota(tok, 1)
        agent = make_agent(tok)
        return sse(agent.events(body.question, [t.model_dump() for t in body.history],
                                body.context), user_id, "answer")

    if make_reporter is not None:
        reporter_for = make_reporter

        @app.post("/v1/reports")
        def report(body: ReportRequest, tok: Annotated[str, Depends(token)],
                   ip: Annotated[str, Depends(client_ip)]) -> StreamingResponse:
            """Writes a research note from one of the user's workspaces and saves it there
            (report.py). Streams the agent's events, a "report" event once saved (or an
            "error"), then "done". Counts as REPORT_QUESTIONS questions against every limit,
            taken only once the workspace is the user's and has something to report on."""
            user_id = who_is(tok)
            reporter = reporter_for(tok)
            try:
                job = reporter.prepare(str(body.workspace_id),
                                       str(body.report_id) if body.report_id else None)
            except ReportError as e:
                raise HTTPException(e.status, e.message) from None
            try:
                limits.acquire(user_id, ip, REPORT_QUESTIONS)
            except LimitExceeded as e:
                raise HTTPException(429, str(e)) from None
            take_quota(tok, REPORT_QUESTIONS)
            return sse(reporter.events(job), user_id, "report")

    return app


def query_api_who_is(base_url: str) -> Callable[[str], str]:
    def who_is(tok: str) -> str:
        try:
            r = httpx.get(f"{base_url}/v1/me",
                          headers={"Authorization": f"Bearer {tok}", **request_id_headers()},
                          timeout=10)
        except httpx.HTTPError:
            raise HTTPException(503, "query api unavailable") from None
        if r.status_code == 401:
            raise HTTPException(401, "invalid token")
        if r.status_code != 200:
            raise HTTPException(503, "query api unavailable")
        return r.json()["user_id"]

    return who_is


def query_api_quota(base_url: str,
                    http: httpx.Client | None = None) -> Callable[[str, int], None]:
    """Demo sandboxes' daily question caps live in the Query API (app database), so they hold
    across restarts and replicas; other users aren't limited there. A report takes several
    questions at once (all or none)."""
    post = http.post if http else httpx.post

    def take_quota(tok: str, questions: int = 1) -> None:
        body = {"questions": questions} if questions > 1 else None
        try:
            r = post(f"{base_url}/v1/demo/agent-quota",
                     headers={"Authorization": f"Bearer {tok}", **request_id_headers()},
                     timeout=10, json=body)
        except httpx.HTTPError:
            raise HTTPException(503, "query api unavailable") from None
        if r.status_code == 200:
            return
        if r.status_code in (401, 429):
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else None
            raise HTTPException(r.status_code, detail if isinstance(detail, str) else
                                "Question limit reached.")
        if r.status_code == 404:  # an older Query API without demo sandboxes
            return
        raise HTTPException(503, "query api unavailable")

    return take_quota


def build_app() -> FastAPI:
    base_url = os.environ.get("QUERY_API_URL", "http://localhost:8000")
    messages = anthropic.Anthropic().messages
    model = os.environ.get("AGENT_MODEL", "claude-haiku-5-5")
    effort = os.environ.get("AGENT_EFFORT") or None
    http = httpx.Client(base_url=base_url, timeout=30,
                        event_hooks={"request": [forward_request_id]})
    tools = MetricTools()

    def make_agent(tok: str) -> Agent:
        api = QueryApi(base_url, tok, http=http)
        return Agent(messages, api, model, stream=True, effort=effort, tools=tools.get(api),
                     ui=UiActions(api))

    def make_reporter(tok: str) -> Reporter:
        api = QueryApi(base_url, tok, http=http)
        return Reporter(api, lambda scoped: Agent(
            messages, scoped, model, stream=True, effort=effort, tools=tools.get(api),
            rules=REPORT_RULES, max_rounds=REPORT_ROUNDS))

    return create_app(
        make_agent=make_agent,
        who_is=query_api_who_is(base_url),
        limits=Limits(int(os.environ.get("AGENT_RATE_PER_HOUR", "30")),
                      int(os.environ.get("AGENT_IP_RATE_PER_HOUR", "10")),
                      float(os.environ.get("AGENT_DAILY_BUDGET_USD", "2"))),
        take_quota=query_api_quota(base_url),
        make_reporter=make_reporter,
    )


def main() -> None:
    configure_logging()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8001")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
