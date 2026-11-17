"""A report the user stops (the browser drops the stream) before it saved anything gives its
questions back, within REFUNDS_PER_DAY (Riley #18); one stopped after it saved counts. Run through
the ASGI app with a real disconnect: the stream's generator is closed mid-stream, as in
production, by whoever collects it."""

import asyncio
import gc
import json
import threading
import time

from fastapi import HTTPException

from tributary_agent.report import REPORT_QUESTIONS
from tributary_agent.server import REFUNDS_PER_DAY, Limits, create_app

WS = "00000000-0000-0000-0000-000000000001"


def who_is(tok):
    if tok != "alice-token":
        raise HTTPException(401, "invalid token")
    return "alice"


class StoppedReporter:
    """Streams a paid round and some text, then waits for the user, who presses Stop; `saves`:
    the report was saved before the stop."""

    def __init__(self, saves: bool = False):
        self.saves = saves
        self.gate = threading.Event()

    def prepare(self, workspace_id, report_id):
        return "job"

    def events(self, job):
        yield {"type": "usage", "cost_usd": 0.25}
        if self.saves:
            yield {"type": "report", "item": {"item_id": "r1"}}
        yield {"type": "text", "text": "Nvidia's revenue"}
        self.gate.wait(5)  # the user stops here
        yield {"type": "text", "text": " grew"}
        yield {"type": "report", "item": {"item_id": "r1"}}


def stop_after_first_event(app, reporter: StoppedReporter) -> list[bytes]:
    """POST /v1/reports and disconnect once the first event arrives, as a browser's Stop does."""
    chunks: list[bytes] = []
    started = False

    async def run():
        first = asyncio.Event()

        async def receive():
            nonlocal started
            if not started:
                started = True
                return {"type": "http.request", "body": json.dumps({"workspace_id": WS}).encode(),
                        "more_body": False}
            await first.wait()
            reporter.gate.set()  # the model call in flight returns; nobody reads it
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                first.set()

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/v1/reports", "raw_path": b"/v1/reports", "query_string": b"",
                 "root_path": "", "client": ("203.0.113.9", 1), "server": ("agent", 80),
                 "headers": [(b"content-type", b"application/json"),
                             (b"authorization", b"Bearer alice-token")]}
        await app(scope, receive, send)

    asyncio.run(run())
    gc.collect()  # the stream is closed when collected (production: when the response is freed)
    return chunks


def app_with(reporter: StoppedReporter):
    ledger: list[int] = []
    limits = Limits(per_hour=100, per_ip_per_hour=100, daily_budget_usd=5)
    app = create_app(lambda tok: None, who_is, limits,  # type: ignore[arg-type,return-value]
                     take_quota=lambda tok, n=1: ledger.append(n),
                     refund_quota=lambda tok, n=1: ledger.append(-n),
                     make_reporter=lambda tok: reporter)  # type: ignore[arg-type,return-value]
    return app, ledger, limits


def settled(ledger: list[int], length: int) -> list[int]:
    """The refund runs on a thread of its own: wait for it (briefly)."""
    deadline = time.monotonic() + 5
    while len(ledger) < length and time.monotonic() < deadline:
        time.sleep(0.01)
    return ledger


def test_a_report_stopped_before_it_saved_gives_its_questions_back():
    reporter = StoppedReporter()
    app, ledger, limits = app_with(reporter)
    chunks = stop_after_first_event(app, reporter)
    assert [json.loads(c.decode()[6:])["type"] for c in chunks] == ["text"]
    assert settled(ledger, 2) == [REPORT_QUESTIONS, -REPORT_QUESTIONS]
    assert limits.left("alice", "203.0.113.9")["left_this_hour"] == 100
    assert limits.spent() == 0.25  # the model call was made: the day's budget keeps it


def test_a_report_stopped_after_it_saved_counts():
    reporter = StoppedReporter(saves=True)
    app, ledger, limits = app_with(reporter)
    stop_after_first_event(app, reporter)
    time.sleep(0.1)
    assert ledger == [REPORT_QUESTIONS]
    assert limits.left("alice", "203.0.113.9")["left_this_hour"] == 100 - REPORT_QUESTIONS


def test_stopped_reports_are_given_back_at_most_twice_a_day():
    """One app (one day's refunds), three stopped reports: the third counts."""
    reporters = [StoppedReporter() for _ in range(REFUNDS_PER_DAY + 1)]
    queue = list(reporters)
    ledger: list[int] = []
    limits = Limits(per_hour=100, per_ip_per_hour=100, daily_budget_usd=5)
    app = create_app(lambda tok: None, who_is, limits,  # type: ignore[arg-type,return-value]
                     take_quota=lambda tok, n=1: ledger.append(n),
                     refund_quota=lambda tok, n=1: ledger.append(-n),
                     make_reporter=lambda tok: queue.pop(0))  # type: ignore[arg-type,return-value]
    for r in reporters:
        stop_after_first_event(app, r)
    settled(ledger, 2 * REFUNDS_PER_DAY + 1)
    time.sleep(0.1)
    assert ledger == [REPORT_QUESTIONS, -REPORT_QUESTIONS] * REFUNDS_PER_DAY + [REPORT_QUESTIONS]
