"""Demo sandbox routes (query_api.sandbox): create one, and the agent's per-question quota.

POST /v1/demo/sandbox takes no input at all: plan, companies, watchlist, starter workspace and
lifetime come from sandbox.py and SandboxConfig, so the route can't be used to pick any of them.
Identity is still only ever the JWT `sub` (the agent-quota route uses current_user like every
other route). The agent quota also caps GitHub sign-ins (query_api.github), with the same
limits, and owner-created password accounts (query_api.password_accounts), each with its own
daily cap."""

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_client import Counter
from pydantic import BaseModel, ConfigDict, Field

from query_api.auth import mint
from query_api.github import is_github_user
from query_api.passwords import is_password_user
from query_api.sandbox import (
    DEMO_PLAN,
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    USER_PREFIX,
    NewSandbox,
    QuotaExceeded,
    Refused,
    SandboxConfig,
    Sandboxes,
    SandboxGone,
    ip_key,
    is_sandbox_user,
    sandbox_workspaces,
)
from query_api.store import Store

SANDBOXES = Counter("tributary_demo_sandboxes_total",
                    "Demo sandbox requests by result (created, ip_limit, full, error)", ["result"])
DEMO_QUESTIONS = Counter("tributary_demo_agent_questions_total",
                         "Assistant questions from demo sandboxes by result (allowed, disabled, "
                         "user_limit, day_limit, expired, refunded)", ["result"])

ACCOUNT_QUESTIONS = Counter("tributary_account_agent_questions_total",
                            "Assistant questions from owner-created password accounts by "
                            "result (allowed, disabled, user_limit, gone, refunded)", ["result"])

log = logging.getLogger("query_api.demo")

# the most one request may take: a report (services/agent report.py REPORT_QUESTIONS, 3) and a
# thesis check (thesis_check.py CHECK_QUESTIONS, 4) count as several questions
MAX_QUESTIONS_AT_ONCE = 5


class QuotaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: int = Field(default=1, ge=1, le=MAX_QUESTIONS_AT_ONCE)


REFUSED = {
    "ip": "Too many demo sandboxes were started from your network in the last hour. "
          "Try again later.",
    "full": "The demo is full right now. Sandboxes expire after a day; try again later.",
}


def demo_company_resolver(store: Store) -> Callable[[], dict[str, str]]:
    """ticker → company_id for DEMO_TICKERS; cached once every ticker resolves. Shared by
    sandboxes and GitHub sign-in."""
    resolved: dict[str, str] = {}

    def demo_companies() -> dict[str, str]:
        if len(resolved) < len(DEMO_TICKERS):
            found = store.resolve_tickers("", list(DEMO_TICKERS))
            missing = [t for t in DEMO_TICKERS if t not in found]
            if missing:
                log.warning("demo tickers that resolve to no company: %s", missing)
            resolved.clear()
            resolved.update(found)
        return resolved

    return demo_companies


def add_sandbox_routes(app: FastAPI, *, store: Store, sandboxes: Sandboxes | None,
                       config: SandboxConfig, jwt_secret: str,
                       current_user: Callable[..., str],
                       clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                       client_ip_header: str = "X-Client-IP",
                       demo_companies: Callable[[], dict[str, str]] | None = None,
                       ) -> Callable[[str], dict | None]:
    """Adds the routes; returns sandbox_info(user_id) for /v1/me (None: not a sandbox)."""
    User = Annotated[str, Depends(current_user)]
    enabled = config.enabled and sandboxes is not None
    demo_companies = demo_companies or demo_company_resolver(store)

    def client_ip(request: Request) -> str:
        ip = request.headers.get(client_ip_header, "").strip()
        if ip:
            return ip
        return request.client.host if request.client else "unknown"

    @app.get("/v1/demo/sandbox")
    def sandbox_offer() -> dict:
        """What "Try the demo" creates (404 when sandboxes are off)."""
        if not enabled:
            raise HTTPException(404)
        return {"ttl_hours": config.ttl_hours, "plan": DEMO_PLAN, "tickers": list(DEMO_TICKERS),
                "catalog_public": sandboxes is not None and sandboxes.catalog_public(),
                "agent": config.agent_enabled,
                "agent_questions_per_day": config.agent_questions_per_user_per_day
                if config.agent_enabled else 0}

    @app.post("/v1/demo/sandbox", status_code=201)
    def create_sandbox(request: Request) -> dict:
        """A new temporary user with DEMO_PLAN, grants on DEMO_TICKERS and a seeded watchlist,
        and a token for it that expires with the sandbox. Ignores any request body."""
        if not enabled or sandboxes is None:
            raise HTTPException(404)
        ids = demo_companies()
        companies = list(dict.fromkeys(ids[t] for t in DEMO_TICKERS if t in ids))
        if not companies:
            SANDBOXES.labels("error").inc()
            raise HTTPException(503, "demo companies unavailable")
        watch = list(dict.fromkeys(ids[t] for t in DEMO_WATCHLIST if t in ids))
        now = clock().replace(microsecond=0)
        sandbox = NewSandbox(user_id=USER_PREFIX + secrets.token_hex(8), company_ids=companies,
                             watchlist=watch, created_at=now,
                             expires_at=now + timedelta(hours=config.ttl_hours),
                             workspaces=tuple(sandbox_workspaces(ids)))
        try:
            sandboxes.create(sandbox, ip_key(jwt_secret, client_ip(request)),
                             config.creations_per_ip_per_hour, config.max_live)
        except Refused as e:
            SANDBOXES.labels("ip_limit" if e.reason == "ip" else "full").inc()
            log.info("demo sandbox refused: %s", e.reason, extra={"reason": e.reason})
            raise HTTPException(429, REFUSED[e.reason]) from None
        SANDBOXES.labels("created").inc()
        log.info("demo sandbox created", extra={"user_id": sandbox.user_id})
        ttl = int((sandbox.expires_at - now).total_seconds())
        tickers = {c: t for t, c in ids.items()}
        return {
            "user_id": sandbox.user_id,
            "token": mint(sandbox.user_id, jwt_secret, ttl, now=int(now.timestamp())),
            "expires_in": ttl,
            "expires_at": sandbox.expires_at.isoformat(),
            "plan": DEMO_PLAN,
            "companies": [{"company_id": c, "ticker": tickers.get(c)} for c in companies],
            "watchlist": watch,
            "workspaces": [w["name"] for w in sandbox.workspaces],
        }

    @app.post("/v1/demo/agent-quota")
    def agent_quota(user_id: User, body: QuotaRequest | None = None) -> dict:
        """Called by agent-api before each question (nginx keeps it off the public site).
        Takes one of the caller's assistant questions (or `questions` of them, all or none:
        a report or a thesis check counts as several) if they are a demo sandbox or a GitHub
        sign-in (same caps); other users aren't limited here. 429 with a message the assistant
        shows when a cap is hit. An owner-created password account is capped by its own
        questions_per_day only (account_quota)."""
        n = body.questions if body else 1
        if is_password_user(user_id):
            return account_quota(user_id, n)
        github = is_github_user(user_id)
        if not is_sandbox_user(user_id) and not github:
            return {"demo": False}
        who = "Your account" if github else "This demo sandbox"
        # GitHub accounts are capped even when "Try the demo" is switched off
        if sandboxes is None or not (enabled or github):
            DEMO_QUESTIONS.labels("disabled").inc()
            raise HTTPException(429, "The assistant isn't available for demo sandboxes.")
        if not config.agent_enabled:
            DEMO_QUESTIONS.labels("disabled").inc()
            raise HTTPException(429, "The assistant is turned off for demo accounts. The rest "
                                     "of the app works as usual." if github else
                                     "The assistant is turned off for demo sandboxes. The rest "
                                     "of the demo works as usual.")
        try:
            left = sandboxes.take_question(user_id, clock(),
                                           config.agent_questions_per_user_per_day,
                                           config.agent_questions_per_day, n)
        except SandboxGone:
            DEMO_QUESTIONS.labels("expired").inc()
            raise HTTPException(401, "demo account not found" if github
                                else "demo sandbox expired") from None
        except QuotaExceeded as e:
            if e.scope == "user":
                DEMO_QUESTIONS.labels("user_limit").inc()
                if n > 1 and e.left > 0:
                    raise HTTPException(429, (
                        f"This counts as {n} assistant questions (a report or a thesis "
                        f"check) and {who[0].lower()}"
                        f"{who[1:]} has {e.left} left today. Ask a question instead, or come "
                        "back after midnight UTC.")) from None
                raise HTTPException(429, (
                    f"{who} has used its {config.agent_questions_per_user_per_day} "
                    "assistant questions for today. The rest of the app keeps working; the "
                    "count resets at midnight UTC.")) from None
            DEMO_QUESTIONS.labels("day_limit").inc()
            raise HTTPException(429, (
                "The assistant has answered all the demo questions it can today. The rest "
                "of the app keeps working; try the assistant again after midnight UTC.")) from None
        DEMO_QUESTIONS.labels("allowed").inc()
        return {"demo": True, "remaining_today": left}

    @app.post("/v1/demo/agent-quota/refund")
    def agent_quota_refund(user_id: User, body: QuotaRequest) -> dict:
        """Gives back `questions` agent-api took for a run that produced nothing (a model
        error, a thesis check or report that saved nothing: agent server.Charge). At most what
        the caller used today, never below zero, so it can't add questions. Off the public site
        like the take (nginx's agent-quota rule covers the path): only agent-api calls it."""
        password = is_password_user(user_id)
        if not password and not is_github_user(user_id) and not is_sandbox_user(user_id):
            return {"demo": False}
        if sandboxes is None:
            return {"demo": True, "refunded": 0}
        given = sandboxes.give_back(user_id, clock(), body.questions)
        (ACCOUNT_QUESTIONS if password else DEMO_QUESTIONS).labels("refunded").inc(given)
        return {"demo": True, "refunded": given}

    @app.get("/v1/demo/agent-quota")
    def agent_quota_left(user_id: User) -> dict:
        """What POST would still allow today, taking nothing: agent-api's /v1/allowance, for the
        assistant panel's meter (nginx keeps this off the public site too). `capped` false: this
        user isn't limited here."""
        password = is_password_user(user_id)
        github = is_github_user(user_id)
        if not password and not github and not is_sandbox_user(user_id):
            return {"capped": False}
        off = sandboxes is None or (not password and (
            not (enabled or github) or not config.agent_enabled))
        if off or sandboxes is None:
            return {"capped": True, "per_day": 0, "left_today": 0}
        try:
            cap, left = sandboxes.questions_left(user_id, clock(),
                                                 config.agent_questions_per_user_per_day,
                                                 config.agent_questions_per_day)
        except SandboxGone:
            raise HTTPException(401, "account not found") from None
        return {"capped": True, "per_day": cap, "left_today": left}

    def account_quota(user_id: str, n: int) -> dict:
        """A password account's own daily cap (app migration 013, set by the owner with
        query-api-account). Not subject to DEMO_SANDBOX_ENABLED, DEMO_AGENT_ENABLED or the
        demo's shared per-day cap: the owner sets this account's allowance (0 turns its
        assistant off); agent-api's daily model budget and hourly limits still apply."""
        if sandboxes is None:
            ACCOUNT_QUESTIONS.labels("disabled").inc()
            raise HTTPException(429, "The assistant isn't available right now.")
        try:
            # per_user and per_day are the demo's; take_question uses the account's own cap
            left = sandboxes.take_question(user_id, clock(),
                                           config.agent_questions_per_user_per_day,
                                           config.agent_questions_per_day, n)
        except SandboxGone:
            ACCOUNT_QUESTIONS.labels("gone").inc()
            raise HTTPException(401, "account not found") from None
        except QuotaExceeded as e:
            ACCOUNT_QUESTIONS.labels("user_limit").inc()
            if e.limit == 0:
                raise HTTPException(429, "Your account has no assistant questions. The rest "
                                         "of the app works as usual.") from None
            if n > 1 and e.left > 0:
                raise HTTPException(429, (
                    f"This counts as {n} assistant questions (a report or a thesis check) and "
                    f"your account has {e.left} left today. Ask a question instead, or come "
                    "back after midnight UTC.")) from None
            raise HTTPException(429, (
                f"Your account has used its {e.limit} assistant questions for today. The "
                "rest of the app keeps working; the count resets at midnight UTC.")) from None
        ACCOUNT_QUESTIONS.labels("allowed").inc()
        return {"demo": True, "remaining_today": left}

    def sandbox_info(user_id: str) -> dict | None:
        if not is_sandbox_user(user_id) or sandboxes is None:
            return None
        expires = sandboxes.expires_at(user_id)
        if expires is None:
            return None
        return {"expires_at": expires.isoformat(), "plan": DEMO_PLAN,
                "companies": len(demo_companies()),
                "agent": config.agent_enabled,
                "agent_questions_per_day": config.agent_questions_per_user_per_day
                if config.agent_enabled else 0}

    return sandbox_info
