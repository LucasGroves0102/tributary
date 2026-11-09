"""query-api-alerts: makes filing alerts and sends them on (query_api/alerts.py has the rules,
query_api/webhooks.py the sending). A CronJob every ten minutes (deploy/helm/tributary,
`alerts`), one run at a time (concurrencyPolicy Forbid).

Why a database poll and not a Kafka consumer: a filing reaches silver.filings within seconds of
the feed (poller → filings.raw → bronze-loader), but what an alert says arrives later and from
elsewhere: an 8-K's items with the nightly submissions load or the parser, a 10-Q's figures with
companyfacts (hours to a day), and the red lines need those figures. A consumer of filings.raw
would still have to wait and come back to the database for each, so it buys nothing; it would
also need its own SCRAM user, an ACL entry and a secret only the owner can create on hermes. A
poll needs nothing new and is idempotent by construction: each run re-reads the last
LOOKBACK_DAYS of the filing index and inserts with ON CONFLICT DO NOTHING (alerts.py), so a
missed or repeated run loses or doubles nothing. Cost per run (measured on hermes' sizes in the
README): one short app-DB transaction per user, one filing-index query per CHUNK followed
companies, then a recap and the red lines only for the few filings that just arrived.

A run:
1. Every user's follows, settings and red lines, as that user (forced RLS).
2. The followed companies' 10-K/10-Q/8-K filed since LOOKBACK_DAYS ago, CHUNK companies a query,
   from the public filing index (Store.alert_filings).
3. Per user: new notifications, then each pending one: an 8-K's items (index, else the parsed
   item headings with the filings tier); a 10-K/10-Q's figures and red lines, after layer 1
   (`can`, OpenFGA) and through Store as that user (RLS). Never another user's access.
4. Deliveries due (a new ready alert x each enabled webhook, and earlier failures whose backoff
   ran out), through HttpsSender; each result in the delivery log. Sends come after every user's
   alerts are made, users in a random order, at most SENDS_PER_USER each, a webhook that times out
   or can't be reached skipped for the rest of the run, and none started after SEND_BUDGET seconds:
   a slow or hostile endpoint delays only its owner's deliveries (they stay due for the next run).
   Email only with a provider configured AND a verified address for the user
   (query_api/alert_email.py: neither exists yet, so none is sent): one digest of the alerts that
   became ready.
5. alert_checks (the Monitor step's last check); notifications older than KEEP_DAYS deleted.

One user's failure (OpenFGA down: fail closed) is logged and the run goes on; the next run
retries it. Sends happen outside any database transaction. A send that succeeded but wasn't
recorded (a crash in between) is sent again next run: receivers dedupe on `id`.
"""

import logging
import os
import random
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from query_api.alert_email import EmailProvider, digest, email_from_env, no_recipients
from query_api.alerts import (
    FORMS,
    KEEP_DAYS,
    LOOKBACK_DAYS,
    PENDING_DAYS,
    RECAP_METRICS,
    AppAlerts,
    evaluate,
    key_figures,
    material,
    message,
    red_line_points,
)
from query_api.shapes import filing_url
from query_api.webhooks import HttpsSender, Sender, body_of, headers, webhook_secret

log = logging.getLogger("query_api.alert_job")
ET = ZoneInfo("America/New_York")
CHUNK = 50          # followed companies per filing-index query (like /v1/filing-index's cap)
MAX_NEW = 200       # new notifications per user per run (a first run after a long outage)
DEFAULT_APP_URL = "https://tributary.lucasgroves.dev"
SENDS_PER_USER = 10     # deliveries tried per user per run
SEND_BUDGET = 240.0     # seconds of sending per run (the Job's deadline is 540)
STALLED = frozenset({"timeout", "connect", "tls"})  # skip that webhook for the rest of the run
SYSTEM = ""         # the filing index is public metadata: read without a user


@dataclass
class Report:
    users: int = 0
    followed: int = 0
    filings: int = 0
    created: int = 0
    ready: int = 0
    skipped: int = 0
    delivered: int = 0
    failed: int = 0
    emailed: int = 0
    errors: int = 0


def links(app_url: str, n: dict) -> dict[str, str]:
    cid, acc = n["company_id"], n["accession_no"]
    out = {"Open in Tributary": f"{app_url}/#/company/{cid}"}
    if n["form"] in ("10-K", "10-Q"):
        out["What changed"] = f"{app_url}/#/company/{cid}/filings?changes={acc}"
    out["Earnings"] = f"{app_url}/#/company/{cid}/earnings"
    edgar = filing_url((n.get("detail") or {}).get("cik") or cid, acc)
    if edgar:
        out["EDGAR"] = edgar
    return out


def event(n: dict, company: dict, link: dict[str, str], at: datetime) -> dict:
    detail = n.get("detail") or {}
    return {"type": "filing", "id": n["notification_id"], "sent_at": at.isoformat(),
            "company": company, "form": n["form"], "filed": str(n["filed"]),
            "period_end": str(n["period_end"]) if n.get("period_end") else None,
            "items": n.get("items"), "sources": n.get("sources"),
            "figures": detail.get("figures"), "figures_status": detail.get("figures_status"),
            "red_lines": detail.get("red_lines") or [], "links": link}


def followed_filings(store: Any, companies: list[str], since: date) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(0, len(companies), CHUNK):
        for r in store.alert_filings(SYSTEM, companies[i:i + CHUNK], list(FORMS), since):
            out.setdefault(r["company_id"], []).append(r)
    return out


def new_rows(plan: dict, filings: dict[str, list[dict]]) -> tuple[list[dict], date | None]:
    """Notifications to insert for a user: followed companies' filings of the forms they want,
    filed on or after the day they followed; and the newest filing date seen."""
    rows, newest = [], None
    for f in plan["follows"]:
        forms = plan["settings"].get(f["company_id"], list(FORMS))
        for r in filings.get(f["company_id"], []):
            newest = max(newest, r["filing_date"]) if newest else r["filing_date"]
            if r["form"] not in forms or r["filing_date"] < f["since"]:
                continue
            rows.append({"company_id": r["company_id"], "accession_no": r["accession_no"],
                         "form": r["form"], "filed": r["filing_date"],
                         "period_end": r["period_end"], "items": r["items"],
                         "sources": f["sources"], "cik": r["cik"]})
    rows.sort(key=lambda r: (r["filed"], r["accession_no"]), reverse=True)
    return rows[:MAX_NEW], newest


def complete(n: dict, *, user_id: str, store: Any, can: Callable[[str, str, str], bool],
             rules: list[dict], index_items: dict[str, list[str] | None],
             today: date) -> dict | None:
    """What a pending notification becomes now (status, items, detail); None: still waiting."""
    detail = dict(n.get("detail") or {})
    waited = (today - n["filed"]).days > PENDING_DAYS
    cid, acc = n["company_id"], n["accession_no"]
    if n["form"] == "8-K":
        items = n["items"] or index_items.get(acc)
        source = "index" if items else None
        if not items and can(user_id, "filings", cid):
            parsed = [r for r in store.company_8ks(user_id, cid, n["filed"])
                      if r["accession_no"] == acc]
            items = (parsed[0]["items"] or parsed[0]["parsed_items"] or None) if parsed else None
            source = "parsed" if items else None
        is_material = material(items)
        if is_material is None and not waited:
            return None
        detail.update(items_from=source, material=is_material)
        return {"notification_id": n["notification_id"], "items": items, "detail": detail,
                "status": "skipped" if is_material is False else "ready"}
    if not can(user_id, "financials", cid):
        detail.update(figures=None, figures_status="no_access", red_lines=[])
        return {"notification_id": n["notification_id"], "items": None, "detail": detail,
                "status": "ready"}
    figures = key_figures(n["form"], store.recap(user_id, cid, acc, n["period_end"],
                                                 RECAP_METRICS), acc)
    if figures is None:
        if not waited:
            return None
        detail.update(figures=None, figures_status="not_in_data", red_lines=[])
        return {"notification_id": n["notification_id"], "items": None, "detail": detail,
                "status": "ready"}
    period = date.fromisoformat(figures[0]["period_end"]) if figures else n["period_end"]
    lines = []
    for rule in rules:
        try:
            lines.append(evaluate(rule, red_line_points(store, user_id, cid, rule), period))
        except Exception as e:  # a rule that no longer compiles reads as no value, not a crash
            log.warning("red line not evaluated: %s", type(e).__name__,
                        extra={"user_id": user_id, "rule_id": rule["rule_id"]})
            lines.append({**evaluate(rule, [], period), "status": "invalid"})
    detail.update(figures=figures, figures_status="ready", red_lines=lines)
    return {"notification_id": n["notification_id"], "items": None, "detail": detail,
            "status": "ready"}


def deliver(alerts: AppAlerts, store: Any, sender: Sender, user_id: str, *, jwt_secret: str,
            app_url: str, at: datetime,
            until: Callable[[], bool] = lambda: True) -> tuple[int, int]:
    """Up to SENDS_PER_USER due deliveries while `until()` holds; a webhook that stalls is left
    for the next run (its rows stay due, untouched)."""
    due = alerts.due(user_id, at, SENDS_PER_USER)
    if not due:
        return 0, 0
    ids = sorted({d["company_id"] for d in due})
    names = store.profiles(user_id, ids)
    tickers = store.tickers(user_id, ids)
    results = []
    stalled: set[str] = set()
    for d in due:
        if d["webhook_id"] in stalled or not until():
            continue
        cid = d["company_id"]
        name = (names.get(cid) or {}).get("name") or cid
        ticker = (tickers.get(cid) or [None])[0]
        company = {"company_id": cid, "name": name, "ticker": ticker}
        label = f"{name} ({ticker})" if ticker else name
        link = links(app_url, d)
        secret = webhook_secret(jwt_secret, d["webhook_id"], bytes(d["secret_salt"]))
        body = body_of(d["format"], event(d, company, link, at), message(d, label, link))
        sent = sender.send(d["url"], body, headers(secret, d["delivery_id"], "filing", body))
        if sent.error in STALLED:
            stalled.add(d["webhook_id"])
        results.append((d["delivery_id"], d["attempts"], sent))
    alerts.record(user_id, results, at)
    ok = sum(1 for _, _, s in results if s.ok)
    return ok, len(results) - ok


def email_digest(alerts: AppAlerts, store: Any, email: EmailProvider, to: str, user_id: str,
                 ready: list[str], app_url: str) -> None:
    """One message with the alerts that just became ready (alert_email.digest)."""
    rows = [n for n in alerts.notifications(user_id, None, 100) if n["notification_id"] in ready]
    if not rows:
        return
    ids = sorted({n["company_id"] for n in rows})
    names, tickers = store.profiles(user_id, ids), store.tickers(user_id, ids)

    def label(cid: str) -> str:
        name = (names.get(cid) or {}).get("name") or cid
        ticker = (tickers.get(cid) or [None])[0]
        return f"{name} ({ticker})" if ticker else name
    subject, body = digest([message(n, label(n["company_id"]), links(app_url, n)) for n in rows])
    email.send(to, subject, body)


def run(*, alerts: AppAlerts, store: Any, can: Callable[[str, str, str], bool], sender: Sender,
        jwt_secret: str, now: datetime, app_url: str = DEFAULT_APP_URL,
        users: list[str] | None = None, email: EmailProvider | None = None,
        recipients: Callable[[str], str | None] = no_recipients,
        monotonic: Callable[[], float] = time.monotonic,
        shuffle: Callable[[list[str]], None] = random.shuffle) -> Report:
    report = Report()
    today = now.astimezone(ET).date()
    since = today - timedelta(days=LOOKBACK_DAYS)
    plans: dict[str, dict] = {}
    for user_id in users if users is not None else alerts.users():
        try:
            plans[user_id] = alerts.plan(user_id)
        except Exception as e:
            report.errors += 1
            log.warning("alerts: couldn't read a user's follows: %s", type(e).__name__,
                        extra={"user_id": user_id})
    report.users = len(plans)
    companies = sorted({f["company_id"] for p in plans.values() for f in p["follows"]})
    report.followed = len(companies)
    filings = followed_filings(store, companies, since)
    report.filings = sum(len(v) for v in filings.values())
    index_items = {r["accession_no"]: r["items"] for rows in filings.values() for r in rows}
    for user_id, plan in plans.items():
        if not plan["follows"] and not plan["webhooks"]:
            continue  # nothing to alert and nothing to send: no writes for this user
        try:
            rows, newest = new_rows(plan, filings)
            report.created += alerts.add_notifications(user_id, rows)
            updates = []
            for n in alerts.pending(user_id):
                u = complete(n, user_id=user_id, store=store, can=can,
                             rules=plan["red_lines"].get(n["company_id"], []),
                             index_items=index_items, today=today)
                if u is not None:
                    updates.append(u)
            ready = alerts.finish(user_id, updates, now)
            report.ready += len(ready)
            to = recipients(user_id) if email is not None and ready else None
            if email is not None and to:
                try:
                    email_digest(alerts, store, email, to, user_id, ready, app_url)
                    report.emailed += 1
                except Exception as e:  # the in-app alert and webhook stand; logged, no retry
                    log.warning("alerts: email not sent: %s", type(e).__name__,
                                extra={"user_id": user_id})
            report.skipped += sum(1 for u in updates if u["status"] == "skipped")
            alerts.checked(user_id, now, newest)
            alerts.prune(user_id, now - timedelta(days=KEEP_DAYS))
        except Exception as e:
            report.errors += 1
            log.warning("alerts: a user's run failed: %s", type(e).__name__,
                        extra={"user_id": user_id})
    # sending last, in a random order, within a budget (module docstring, step 4)
    senders = [u for u, p in plans.items() if p["webhooks"]]
    shuffle(senders)
    deadline = monotonic() + SEND_BUDGET
    for user_id in senders:
        try:
            ok, failed = deliver(alerts, store, sender, user_id, jwt_secret=jwt_secret,
                                 app_url=app_url, at=now, until=lambda: monotonic() < deadline)
            report.delivered += ok
            report.failed += failed
        except Exception as e:
            report.errors += 1
            log.warning("alerts: a user's deliveries failed: %s", type(e).__name__,
                        extra={"user_id": user_id})
    return report


def main() -> None:
    from psycopg_pool import ConnectionPool

    from query_api.auth import check_secret
    from query_api.authorizer import FgaAuthorizer
    from query_api.store import Store
    from tributary_authz import FgaClient
    from tributary_telemetry import configure_logging

    configure_logging()
    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=2, open=True)
    app_pool = ConnectionPool(os.environ["APP_DATABASE_URL"], min_size=1, max_size=2, open=True)
    authorizer = FgaAuthorizer(FgaClient.from_env())
    try:
        report = run(alerts=AppAlerts(app_pool), store=Store(pool), can=authorizer.can,
                     sender=HttpsSender(),
                     jwt_secret=check_secret(os.environ["QUERY_API_JWT_SECRET"]),
                     now=datetime.now(UTC),
                     app_url=os.environ.get("PUBLIC_APP_URL", DEFAULT_APP_URL).rstrip("/"),
                     email=email_from_env(os.environ))
    finally:
        pool.close()
        app_pool.close()
    log.info("alerts run: %d users, %d new, %d ready, %d delivered, %d failed, %d errors",
             report.users, report.created, report.ready, report.delivered, report.failed,
             report.errors, extra=asdict(report))
    # every user failing means something is down (the database, OpenFGA): fail the Job
    sys.exit(1 if report.errors and report.errors >= max(report.users, 1) else 0)


if __name__ == "__main__":
    main()
