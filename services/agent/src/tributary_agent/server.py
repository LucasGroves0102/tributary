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
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from tributary_agent.agent import Agent, Answer
from tributary_agent.tools import QueryApi

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
    """Questions per user per hour, and model spend per UTC day across all users."""

    def __init__(self, per_hour: int, daily_budget_usd: float,
                 clock: Callable[[], float] = time.time) -> None:
        self._per_hour = per_hour
        self._budget = daily_budget_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._asked: dict[str, deque[float]] = defaultdict(deque)
        self._spent: dict[str, float] = defaultdict(float)  # UTC date -> USD

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).date().isoformat()

    def acquire(self, user_id: str) -> None:
        now = self._clock()
        with self._lock:
            if self._spent[self._today()] >= self._budget:
                raise LimitExceeded("The demo's daily model budget is used up. Try tomorrow.")
            asked = self._asked[user_id]
            while asked and asked[0] <= now - 3600:
                asked.popleft()
            if len(asked) >= self._per_hour:
                raise LimitExceeded(f"Limit of {self._per_hour} questions per hour reached.")
            asked.append(now)

    def spend(self, usd: float) -> None:
        with self._lock:
            self._spent[self._today()] += usd


def answer_json(a: Answer) -> dict:
    return {"text": a.text, "stop": a.stop, "model": a.model, "rounds": a.rounds,
            "tool_calls": [asdict(c) for c in a.tool_calls], "usage": asdict(a.usage),
            "cost_usd": a.cost_usd, "citations": list(a.citations.values())}


def create_app(make_agent: Callable[[str], Agent], who_is: Callable[[str], str],
               limits: Limits) -> FastAPI:
    """make_agent(token) builds an agent reading as the token's user; who_is(token) returns
    that user's id or raises HTTPException."""
    app = FastAPI(title="Tributary Agent", version="1")
    bearer = HTTPBearer(auto_error=False)

    def token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
        if creds is None:
            raise HTTPException(401, "missing bearer token")
        return creds.credentials

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/ask")
    def ask(body: AskRequest, tok: Annotated[str, Depends(token)]) -> StreamingResponse:
        user_id = who_is(tok)
        try:
            limits.acquire(user_id)
        except LimitExceeded as e:
            raise HTTPException(429, str(e)) from None
        agent = make_agent(tok)

        def stream() -> Iterator[str]:
            try:
                for event in agent.events(body.question, [t.model_dump() for t in body.history]):
                    if event["type"] == "done":
                        answer = event["answer"]
                        limits.spend(answer.cost_usd or 0)
                        log.info("user=%s rounds=%d cost=%.4f stop=%s", user_id, answer.rounds,
                                 answer.cost_usd or 0, answer.stop)
                        event = {"type": "done", "answer": answer_json(answer)}
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except anthropic.APIError as e:
                log.warning("model error for %s: %s", user_id, e)
                yield f"data: {json.dumps({'type': 'error', 'message': 'model unavailable'})}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return app


def query_api_who_is(base_url: str) -> Callable[[str], str]:
    def who_is(tok: str) -> str:
        try:
            r = httpx.get(f"{base_url}/v1/me", headers={"Authorization": f"Bearer {tok}"},
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
    http = httpx.Client(base_url=base_url, timeout=30)
    return create_app(
        make_agent=lambda tok: Agent(messages, QueryApi(base_url, tok, http=http), model,
                                     stream=True, effort=os.environ.get("AGENT_EFFORT") or None),
        who_is=query_api_who_is(base_url),
        limits=Limits(int(os.environ.get("AGENT_RATE_PER_HOUR", "30")),
                      float(os.environ.get("AGENT_DAILY_BUDGET_USD", "2"))),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))


if __name__ == "__main__":
    main()
