"""Agent HTTP API: POST /v1/ask streams the agent's events as server-sent events."""

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

import anthropic
import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from tributary_agent.agent import Agent, Answer
from tributary_agent.telemetry import record_answer, record_error
from tributary_agent.tools import QueryApi
from tributary_telemetry import configure_logging
from tributary_telemetry.http import forward_request_id, instrument, request_id_headers

log = logging.getLogger("tributary_agent")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default=[], max_length=10)


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

    def acquire(self, user_id: str, client_ip: str) -> None:
        now = self._clock()
        with self._lock:
            if self._spent[self._today()] >= self._budget:
                raise LimitExceeded("The demo's daily model budget is used up. Try tomorrow.")
            self._sweep(now)
            by_ip = self._recent(self._by_ip[client_ip], now)
            by_user = self._recent(self._by_user[user_id], now)
            if len(by_ip) >= self._per_ip:
                raise LimitExceeded(f"Limit of {self._per_ip} questions per hour reached. "
                                    "Try again later.")
            if len(by_user) >= self._per_hour:
                raise LimitExceeded(f"Demo user {user_id} has reached its limit of "
                                    f"{self._per_hour} questions per hour. "
                                    "Try another demo user.")
            by_ip.append(now)
            by_user.append(now)

    def spend(self, usd: float) -> None:
        with self._lock:
            self._spent[self._today()] += usd


def answer_json(a: Answer) -> dict:
    return {"text": a.text, "stop": a.stop, "model": a.model, "rounds": a.rounds,
            "tool_calls": [asdict(c) for c in a.tool_calls], "usage": asdict(a.usage),
            "cost_usd": a.cost_usd, "citations": list(a.citations.values()),
            "verification": a.verification}


def create_app(make_agent: Callable[[str], Agent], who_is: Callable[[str], str],
               limits: Limits, client_ip_header: str = "X-Client-IP") -> FastAPI:
    """make_agent(token) builds an agent reading as the token's user; who_is(token) returns
    that user's id or raises HTTPException. The client IP is read from client_ip_header, set
    by the web proxy, else the connection's peer address."""
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
        agent = make_agent(tok)

        def stream() -> Iterator[str]:
            try:
                for event in agent.events(body.question, [t.model_dump() for t in body.history]):
                    if event["type"] == "done":
                        answer = event["answer"]
                        limits.spend(answer.cost_usd or 0)
                        record_answer(answer)
                        log.info("user=%s rounds=%d cost=%.4f stop=%s", user_id, answer.rounds,
                                 answer.cost_usd or 0, answer.stop,
                                 extra={"user_id": user_id, "rounds": answer.rounds,
                                        "stop": answer.stop, "cost_usd": answer.cost_usd,
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


def build_app() -> FastAPI:
    base_url = os.environ.get("QUERY_API_URL", "http://localhost:8000")
    messages = anthropic.Anthropic().messages
    model = os.environ.get("AGENT_MODEL", "claude-haiku-5-5")
    http = httpx.Client(base_url=base_url, timeout=30,
                        event_hooks={"request": [forward_request_id]})
    return create_app(
        make_agent=lambda tok: Agent(messages, QueryApi(base_url, tok, http=http), model,
                                     stream=True, effort=os.environ.get("AGENT_EFFORT") or None),
        who_is=query_api_who_is(base_url),
        limits=Limits(int(os.environ.get("AGENT_RATE_PER_HOUR", "30")),
                      int(os.environ.get("AGENT_IP_RATE_PER_HOUR", "10")),
                      float(os.environ.get("AGENT_DAILY_BUDGET_USD", "2"))),
    )


def main() -> None:
    configure_logging()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8001")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
