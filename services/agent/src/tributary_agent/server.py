"""Agent HTTP API: POST /v1/ask streams the agent's events as server-sent events; POST
/v1/reports writes a research note from one of the user's workspaces (report.py); POST
/v1/thesis-checks checks one of the user's theses against the data (thesis_check.py)."""

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
from tributary_agent.thesis_check import CHECK_QUESTIONS, Checker, check_agent
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


class CheckRequest(BaseModel):
    """Check this thesis (the user's own: the Query API checks)."""
    model_config = ConfigDict(extra="forbid")
    item_id: UUID


class LimitExceeded(Exception):
    pass


# Runs given back per user per UTC day (`Limits.take_refund`). Only failures that are ours and
# can't be caused on purpose are given back at all (create_app's docstring); two a day covers a
# retry after a passing outage, and caps what anyone could get free if one of them turned out to
# be reachable after all: two runs' model calls a day (two checks: 8 questions), still inside the
# day's budget.
REFUNDS_PER_DAY = 2


class Limits:
    """Questions per hour per user and per client IP, model spend per UTC day across all
    users, and refunds per user per UTC day."""

    def __init__(self, per_hour: int, per_ip_per_hour: int, daily_budget_usd: float,
                 clock: Callable[[], float] = time.time,
                 refunds_per_day: int = REFUNDS_PER_DAY) -> None:
        self._per_hour = per_hour
        self._per_ip = per_ip_per_hour
        self._budget = daily_budget_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._by_user: dict[str, deque[float]] = defaultdict(deque)
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)
        self._swept = clock()
        self._spent: dict[str, float] = defaultdict(float)  # UTC date -> USD
        self._refunds_per_day = refunds_per_day
        self._refunds_day = ""
        self._refunds: dict[str, int] = {}  # user -> refunds given on _refunds_day

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

    def acquire(self, user_id: str, client_ip: str, questions: int = 1,
                what: str = "A report") -> float:
        """Takes `questions` from the hour's allowances (a report or a thesis check counts as
        several), or none of them. `what` names it in the refusal. Returns the stamp
        `release` gives them back by."""
        now = self._clock()
        with self._lock:
            if self._spent[self._today()] >= self._budget:
                raise LimitExceeded("The demo's daily model budget is used up. Try tomorrow.")
            self._sweep(now)
            by_ip = self._recent(self._by_ip[client_ip], now)
            by_user = self._recent(self._by_user[user_id], now)
            report = f"{what} counts as {questions} questions. " if questions > 1 else ""
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
            return now

    def release(self, user_id: str, client_ip: str, questions: int, stamp: float) -> None:
        """Gives back what `acquire` took at `stamp` (a run given back, a refused quota). The
        day's spend is never given back: the model calls were made."""
        with self._lock:
            for asked in (self._by_user.get(user_id), self._by_ip.get(client_ip)):
                for _ in range(questions):
                    if asked is None or stamp not in asked:
                        break
                    asked.remove(stamp)

    def left(self, user_id: str, client_ip: str) -> dict:
        """What `acquire` would still allow this hour, taking nothing: for the assistant panel's
        meter. `left_this_hour` is the tighter of the user's and the address's allowance."""
        now = self._clock()
        with self._lock:
            mine = len(self._recent(self._by_user[user_id], now)) if user_id in self._by_user else 0
            near = len(self._recent(self._by_ip[client_ip], now)) if client_ip in self._by_ip else 0
            return {"per_hour": self._per_hour, "network_per_hour": self._per_ip,
                    "left_this_hour": max(0, min(self._per_hour - mine, self._per_ip - near)),
                    "paused": self._spent[self._today()] >= self._budget}

    def spend(self, usd: float) -> None:
        with self._lock:
            self._spent[self._today()] += usd

    def spent(self) -> float:
        """The day's (UTC) recorded model spend so far."""
        with self._lock:
            return self._spent[self._today()]

    def take_refund(self, user_id: str) -> bool:
        """Takes one of the user's refunds for today (UTC), or False when they're used up."""
        with self._lock:
            today = self._today()
            if today != self._refunds_day:
                self._refunds_day, self._refunds = today, {}
            if self._refunds.get(user_id, 0) >= self._refunds_per_day:
                return False
            self._refunds[user_id] = self._refunds.get(user_id, 0) + 1
            return True


def answer_json(a: Answer) -> dict:
    return {"text": a.text, "stop": a.stop, "model": a.model, "rounds": a.rounds,
            "tool_calls": [asdict(c) for c in a.tool_calls], "usage": asdict(a.usage),
            "cost_usd": a.cost_usd, "citations": list(a.citations.values()),
            "verification": a.verification, "actions": a.actions}


def _no_quota(tok: str, questions: int = 1) -> None:
    return None


NO_DAILY_COUNT = {"per_day": None, "left_today": None}


def _no_daily_count(tok: str) -> dict:
    return NO_DAILY_COUNT


class Charge:
    """What one request took from the limits (the hour's, per user and per address, and the
    user's persistent daily quota), and how to give it back (create_app's docstring says when)."""

    def __init__(self, limits: Limits, refund_quota: Callable[[str, int], None], user_id: str,
                 ip: str, tok: str, questions: int, stamp: float) -> None:
        self._limits, self._refund = limits, refund_quota
        self._user, self._ip, self._tok = user_id, ip, tok
        self.questions, self._stamp = questions, stamp
        self.refunded = False

    def refund(self) -> None:
        if self.refunded:
            return
        self.refunded = True
        self._limits.release(self._user, self._ip, self.questions, self._stamp)
        self._refund(self._tok, self.questions)


def create_app(make_agent: Callable[[str], Agent], who_is: Callable[[str], str],
               limits: Limits, client_ip_header: str = "X-Client-IP",
               take_quota: Callable[[str, int], None] = _no_quota,
               daily_count: Callable[[str], dict] = _no_daily_count,
               make_reporter: Callable[[str], Reporter] | None = None,
               make_checker: Callable[[str], Checker] | None = None,
               refund_quota: Callable[[str, int], None] = _no_quota) -> FastAPI:
    """make_agent(token) builds an agent reading as the token's user; who_is(token) returns
    that user's id or raises HTTPException. The client IP is read from client_ip_header, set
    by the web proxy, else the connection's peer address. take_quota(token, n) takes n of the
    user's persistent question allowances (demo sandboxes, Query API) or raises
    HTTPException (429 with the message to show); refund_quota(token, n) gives them back
    (never raises). make_reporter(token): reports as the
    token's user (report.py); None leaves /v1/reports out. make_checker(token): thesis checks
    as the token's user (thesis_check.py); None leaves /v1/thesis-checks out. daily_count(tok):
    the user's daily cap and what's left of it, for /v1/allowance.

    Charging: a request takes its questions before the model runs (so parallel requests can't
    overspend). Every model call that returned is recorded against the day's budget as it
    happens (the agent's `usage` events), whatever happens next: a model error later in the
    run, a client that went away. Questions are given back only for a failure that is ours and
    can't be caused on purpose, at most REFUNDS_PER_DAY times per user per day:
    - a model API error (outage, overload) before any model call returned and before any text
      streamed: nothing billable was produced;
    - a thesis check that saved nothing because its replies were cut off at max_tokens.
    Nothing else is given back: an error after a paid round (a user can push the context past
    the model's limit), a model that chose not to submit, a save refused because the thesis or
    workspace was deleted mid-run, a client that disconnects (it may have read the answer). A
    result counts, partial ones included (a check the model submitted before stopping). The
    "refunded" event is sent only when the questions really were given back."""
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

    def charge(user_id: str, ip: str, tok: str, questions: int = 1,
               what: str = "A report") -> Charge:
        """Takes the questions from every limit, all or none (a refused daily quota gives the
        hour's back)."""
        try:
            stamp = limits.acquire(user_id, ip, questions, what=what)
        except LimitExceeded as e:
            raise HTTPException(429, str(e)) from None
        try:
            take_quota(tok, questions)
        except HTTPException:
            limits.release(user_id, ip, questions, stamp)
            raise
        return Charge(limits, refund_quota, user_id, ip, tok, questions, stamp)

    def sse(events: Iterator[dict], user_id: str, kind: str, paid: Charge,
            result: str | None = None) -> StreamingResponse:
        """result: the event type that means the run produced what it was asked for ("report",
        "check"); None: an answer (any streamed text, or done). `paid` is given back, with a
        "refunded" event before done (or the error), only as create_app's docstring says."""
        produces = result or "text"

        def give_back(why: str) -> Iterator[str]:
            if not limits.take_refund(user_id):
                log.info("user=%s %s not refunded (%s): today's refunds are used", user_id,
                         kind, why, extra={"user_id": user_id, "kind": kind, "why": why})
                return
            paid.refund()
            log.info("user=%s %s refunded %d questions (%s)", user_id, kind, paid.questions,
                     why, extra={"user_id": user_id, "kind": kind, "questions": paid.questions,
                                 "why": why})
            yield f"data: {json.dumps({'type': 'refunded', 'questions': paid.questions})}\n\n"

        def stream() -> Iterator[str]:
            produced = False  # the result: an answer's text, a saved report or check
            billable = False  # a model call returned, or text streamed
            cut_off = False  # a check with nothing to save: its replies hit max_tokens
            partial = 0.0  # what the calls that returned cost, until done gives the whole
            spent = False

            def spend(usd: float) -> None:
                nonlocal spent
                if not spent:
                    spent = True
                    limits.spend(usd)

            try:
                for event in events:
                    if event["type"] == "usage":  # internal: never sent to the browser
                        billable = True
                        partial += event.get("cost_usd") or 0
                        continue
                    if event["type"] == "text":
                        billable = True
                    if event["type"] == produces:
                        produced = True
                    if event["type"] == "error":
                        cut_off = cut_off or event.get("cut_off") is True
                        event = {"type": "error", "message": event.get("message")}
                    if event["type"] == "done":
                        answer = event["answer"]
                        spend(answer.cost_usd or 0)
                        record_answer(answer)
                        log.info("user=%s %s rounds=%d cost=%.4f stop=%s", user_id, kind,
                                 answer.rounds, answer.cost_usd or 0, answer.stop,
                                 extra={"user_id": user_id, "kind": kind,
                                        "rounds": answer.rounds, "stop": answer.stop,
                                        "cost_usd": answer.cost_usd,
                                        "input_tokens": answer.usage.input_tokens,
                                        "output_tokens": answer.usage.output_tokens,
                                        "tool_calls": len(answer.tool_calls)})
                        if result is not None and not produced and cut_off:
                            yield from give_back("cut off at max_tokens")
                        event = {"type": "done", "answer": answer_json(answer)}
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except anthropic.APIError as e:
                spend(partial)
                record_error()
                log.warning("model error for %s: %s (cost so far %.4f)", user_id, e, partial,
                            extra={"user_id": user_id, "cost_usd": partial})
                if not produced and not billable:
                    yield from give_back("model error before any output")
                yield f"data: {json.dumps({'type': 'error', 'message': 'model unavailable'})}\n\n"
            finally:  # a client that went away: what the model calls cost still counts
                spend(partial)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/allowance")
    def allowance(tok: Annotated[str, Depends(token)],
                  ip: Annotated[str, Depends(client_ip)]) -> dict:
        """The caller's questions left, before spending any: per hour here, per day from the
        Query API (null where it doesn't count them), and what a report and a thesis check
        take. Takes nothing and costs no model call."""
        return {**limits.left(who_is(tok), ip), **daily_count(tok),
                "report_questions": REPORT_QUESTIONS, "check_questions": CHECK_QUESTIONS}

    @app.post("/v1/ask")
    def ask(body: AskRequest, tok: Annotated[str, Depends(token)],
            ip: Annotated[str, Depends(client_ip)]) -> StreamingResponse:
        user_id = who_is(tok)
        paid = charge(user_id, ip, tok)
        agent = make_agent(tok)
        return sse(agent.events(body.question, [t.model_dump() for t in body.history],
                                body.context), user_id, "answer", paid)

    if make_reporter is not None:
        reporter_for = make_reporter

        @app.post("/v1/reports")
        def report(body: ReportRequest, tok: Annotated[str, Depends(token)],
                   ip: Annotated[str, Depends(client_ip)]) -> StreamingResponse:
            """Writes a research note from one of the user's workspaces and saves it there
            (report.py). Streams the agent's events, a "report" event once saved (or an
            "error"), then "done". Counts as REPORT_QUESTIONS questions against every limit,
            taken only once the workspace is the user's and has something to report on, and
            given back ("refunded") only after a model error before any output."""
            user_id = who_is(tok)
            reporter = reporter_for(tok)
            try:
                job = reporter.prepare(str(body.workspace_id),
                                       str(body.report_id) if body.report_id else None)
            except ReportError as e:
                raise HTTPException(e.status, e.message) from None
            paid = charge(user_id, ip, tok, REPORT_QUESTIONS)
            return sse(reporter.events(job), user_id, "report", paid, result="report")

    if make_checker is not None:
        checker_for = make_checker

        @app.post("/v1/thesis-checks")
        def check_thesis(body: CheckRequest, tok: Annotated[str, Depends(token)],
                         ip: Annotated[str, Depends(client_ip)]) -> StreamingResponse:
            """Checks one of the user's theses against the data and saves the result with it
            (thesis_check.py). Streams the agent's events, a "check" event once saved (or an
            "error"), then "done". Counts as CHECK_QUESTIONS questions against every limit,
            taken only once the thesis is the user's and has text to check, and given back
            ("refunded") only after a model error before any output or when replies cut off at
            max_tokens left nothing to save; a partial check (submitted before the model
            stopped) is saved and counts."""
            user_id = who_is(tok)
            checker = checker_for(tok)
            try:
                job = checker.prepare(str(body.item_id))
            except ReportError as e:
                raise HTTPException(e.status, e.message) from None
            paid = charge(user_id, ip, tok, CHECK_QUESTIONS, what="A thesis check")
            return sse(checker.events(job), user_id, "thesis_check", paid, result="check")

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
    """Demo sandboxes' (and GitHub sign-ins') daily question caps, and each owner-created
    password account's own cap, live in the Query API (app database), so they hold across
    restarts and replicas; other users aren't limited there.
    The Query API decides who is capped from the token. A report takes several
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


def query_api_refund(base_url: str,
                     http: httpx.Client | None = None) -> Callable[[str, int], None]:
    """Gives back questions take_quota took for a run create_app gives back
    (`POST /v1/demo/agent-quota/refund`, off the public site like the take). Never raises: a
    refund that fails only leaves the count as it was, and is logged."""
    post = http.post if http else httpx.post

    def refund(tok: str, questions: int = 1) -> None:
        try:
            r = post(f"{base_url}/v1/demo/agent-quota/refund",
                     headers={"Authorization": f"Bearer {tok}", **request_id_headers()},
                     timeout=10, json={"questions": questions})
        except httpx.HTTPError as e:
            log.warning("quota refund failed: %s", type(e).__name__)
            return
        if r.status_code not in (200, 404):  # 404: an older Query API without refunds
            log.warning("quota refund failed: %s", r.status_code)

    return refund


def query_api_daily_count(base_url: str,
                          http: httpx.Client | None = None) -> Callable[[str], dict]:
    """The user's daily question cap and what's left, from the same place `take_quota` takes
    them. Never raises: a meter that can't get the day's count shows the hour's."""
    get = http.get if http else httpx.get

    def daily_count(tok: str) -> dict:
        try:
            r = get(f"{base_url}/v1/demo/agent-quota",
                    headers={"Authorization": f"Bearer {tok}", **request_id_headers()}, timeout=10)
            body = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return NO_DAILY_COUNT
        if not body.get("capped"):
            return NO_DAILY_COUNT
        return {"per_day": body.get("per_day"), "left_today": body.get("left_today")}

    return daily_count


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

    def make_checker(tok: str) -> Checker:
        api = QueryApi(base_url, tok, http=http)
        return Checker(api, lambda scoped: check_agent(
            messages, scoped, model, tool_list=tools.get(api), effort=effort, stream=True))

    return create_app(
        make_agent=make_agent,
        who_is=query_api_who_is(base_url),
        limits=Limits(int(os.environ.get("AGENT_RATE_PER_HOUR", "30")),
                      int(os.environ.get("AGENT_IP_RATE_PER_HOUR", "10")),
                      float(os.environ.get("AGENT_DAILY_BUDGET_USD", "2"))),
        take_quota=query_api_quota(base_url),
        refund_quota=query_api_refund(base_url),
        daily_count=query_api_daily_count(base_url),
        make_reporter=make_reporter,
        make_checker=make_checker,
    )


def main() -> None:
    configure_logging()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8001")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
