"""The alerts job's watch pass: saved screens that alert (watches.py has the rules). The alerts
CronJob (query-api-alerts, every ten minutes, alert_job.py) runs it between making filing alerts
and sending, so a screen alert's webhooks go out in the same run; no CronJob of its own.

A run:
1. When the nightly figures last landed (Store.figures_landed_at, tributary 110: the
   financials-bulk job's last step), read once without a user; None (a new database, migration
   not there): watches fall back to a check a day (watches.FALLBACK).
2. Per user, in the run's order: the watches due (AppWatches.due, as that user). For each, AS
   ITS OWNER: layer 1 (OpenFGA: the owner's Access; down: the user is skipped, fail closed),
   their own metrics through their own items and datasets, then the screen through Store as the
   owner (RLS) with `granted_only` and the owner's id list (None with the catalog open), and
   layer 1 again on every row. A line on a named company reads that company the same way
   (a comps table's query). Never another user's access.
3. The check (watches.changes): first or with a new basis it only records; else the alert, at
   most MAX_LISTED companies listed. Stored with its deliveries in one transaction, unless the
   watch changed meanwhile (then it is checked again next run). A failed check (a metric gone,
   a query over its time) keeps what it saw last and waits for the next landing.
4. Email: one digest per user of the alerts made, when a provider and a recipient exist
   (alert_email.py; neither does yet). Screen alerts older than KEEP_DAYS are deleted.

None started after BUDGET seconds (and never later than LATEST seconds after the filing pass's
budget ran out, so the run's sends keep their time inside the Job's deadline): a watch not
reached is due again next run.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg

from query_api.alert_email import EmailProvider, digest, no_recipients
from query_api.alerts import KEEP_DAYS
from query_api.authorizer import Access, AuthzUnavailable, Readable, readable_from
from query_api.screener import (
    COLUMNS,
    MetricColumn,
    Screen,
    ScreenError,
    compile_screen,
    metric_column,
    metric_datasets,
)
from query_api.watches import (
    FALLBACK,
    AppWatches,
    Snapshot,
    basis,
    changes,
    comparable,
    listed,
    message,
    metric_ids,
    screen_links,
    screen_of,
    unit_of,
)

log = logging.getLogger("query_api.watch_job")
BUDGET = 60.0   # seconds of watch checks per run
LATEST = 20.0   # ... and none started later than this after the filing pass's budget ran out


class MetricGone(Exception):
    """A metric the screen shows is no longer the owner's (deleted, or not a metric)."""


@dataclass
class WatchReport:
    checked: int = 0
    baselines: int = 0
    alerts: int = 0
    too_broad: int = 0
    failed: int = 0
    deferred: int = 0
    errors: int = 0


def own_metrics(user_id: str, state: dict, item: Callable[[str, str], dict | None],
                datasets: Callable[[str, list[str]], dict] | None) -> dict[str, MetricColumn]:
    """The screen's own metrics as columns, from the owner's own items and datasets (app.py's
    own_metrics, without HTTP). MetricGone or ScreenError when one can't be read."""
    items = []
    for item_id in metric_ids(state):
        it = item(user_id, item_id)
        if it is None or it.get("kind") != "metric":
            raise MetricGone(item_id)
        items.append(it)
    names = sorted({n for i in items for n in metric_datasets(i["state"]["formula"])})
    if names and datasets is None:
        raise MetricGone("datasets unavailable")
    found = datasets(user_id, names) if names and datasets else {}
    out = {}
    for i in items:
        m = metric_column(i["item_id"], i["name"], i["state"]["formula"],
                          i["state"].get("view", "ttm"), found)
        out[m.key] = m
    return out


def evaluate(store: Any, user_id: str, readable: Readable, watch: dict,
             metrics: dict[str, MetricColumn]) -> Snapshot:
    """The watched screen as its owner sees it now (module docstring, step 2)."""
    lines = watch.get("lines") or []
    usable = [line["column"] in COLUMNS or line["column"] in metrics for line in lines]
    wide = [line["column"] for line, ok in zip(lines, usable, strict=True)
            if ok and not line.get("company_id")]
    granted = readable.ids("financials")

    def ok(company_id: str) -> bool:  # layer 1 again, on every row
        return "financials" in readable.get(company_id, set())

    compiled = compile_screen(screen_of(watch["state"] or {}, watch["columns"] or [], metrics,
                                        wide), granted)
    got = store.screen(user_id, compiled)
    rows = [r for r in got if ok(r["company_id"])]
    # the screen's count before its LIMIT (readable companies only: granted_only)
    total = max(int(got[0]["total"]) if got else 0, len(rows))
    snap = Snapshot(members=sorted(r["company_id"] for r in rows), total=total)
    if snap.too_broad:
        return snap
    named = sorted({line["company_id"] for line, u in zip(lines, usable, strict=True)
                    if u and line.get("company_id")})
    one: dict[str, dict] = {}
    one_slots: dict[str, int] = {}
    if named:
        cols = list(dict.fromkeys(line["column"] for line, u in zip(lines, usable, strict=True)
                                  if u and line.get("company_id")))
        small = compile_screen(Screen(columns=cols,
                                      metrics={k: m for k, m in metrics.items() if k in cols},
                                      company_ids=named, coverage=False, hide_blank=False,
                                      granted_only=True, sort="company_id",
                                      limit=len(named) + 1), granted)
        one = {r["company_id"]: r for r in store.screen(user_id, small) if ok(r["company_id"])}
        one_slots = small.slots
    for line, u in zip(lines, usable, strict=True):
        snap.units.append(unit_of(line["column"], metrics))
        if not u:  # its own metric left the screen: it reads nothing, so never crosses
            snap.values.append({})
        elif line.get("company_id"):
            r = one.get(line["company_id"])
            i = one_slots.get(line["column"])
            snap.values.append({} if r is None or i is None else {
                line["company_id"]: (comparable(r[f"v{i}"], r[f"st{i}"]), r[f"v{i}"])})
        else:
            i = compiled.slots[line["column"]]
            snap.values.append({r["company_id"]: (comparable(r[f"v{i}"], r[f"st{i}"]),
                                                  r[f"v{i}"]) for r in rows})
    return snap


def check(store: Any, user_id: str, access: Access, readable: Readable, watch: dict, *,
          item: Callable[[str, str], dict | None],
          datasets: Callable[[str, list[str]], dict] | None,
          landed: datetime | None) -> dict:
    """One watch checked as its owner: the result AppWatches.record stores."""
    state = watch["state"] or {}
    lines = watch.get("lines") or []
    try:
        metrics = own_metrics(user_id, state, item, datasets)
        snap = evaluate(store, user_id, readable, watch, metrics)
    except (MetricGone, ScreenError, psycopg.Error) as e:  # message of a psycopg error: values
        log.warning("watch not checked: %s", type(e).__name__,
                    extra={"user_id": user_id, "watch_id": watch["watch_id"]})
        return {"outcome": "failed"}
    if snap.too_broad:
        return {"outcome": "too_broad", "companies": snap.total}
    b = basis(state, watch["columns"] or [], lines, metrics, access)
    before = ({"members": watch["members"], "near": watch.get("near") or {}}
              if watch.get("basis") == b and watch.get("members") is not None else None)
    detail, seen = changes(watch, before, snap)
    if detail is not None:
        detail["figures_at"] = landed.isoformat() if landed else None
    return {"outcome": "baseline" if before is None else "ok", "basis": b,
            "members": seen["members"], "near": seen["near"], "companies": len(snap.members),
            "detail": detail}


def landed_at(store: Any) -> datetime | None:
    try:
        return store.figures_landed_at()
    except psycopg.Error as e:  # before migration 110, or the database is down: fall back
        log.warning("figures' landing time unavailable: %s", type(e).__name__)
        return None


def email_digest(store: Any, email: EmailProvider, to: str, user_id: str, made: list[dict],
                 app_url: str) -> None:
    """One message with the screen alerts this run made for the user (alert_email.digest)."""
    ids = sorted({c for a in made for c in listed(a["detail"])})
    names, tickers = store.profiles(user_id, ids), store.tickers(user_id, ids)

    def label(cid: str) -> str:
        name = (names.get(cid) or {}).get("name") or cid
        ticker = (tickers.get(cid) or [None])[0]
        return f"{name} ({ticker})" if ticker else name
    subject, body = digest([message(a, label, screen_links(app_url, a["item_id"])) for a in made],
                           "screen alert")
    email.send(to, subject, body)


def run_watches(*, watches: AppWatches, store: Any, access: Callable[[str], Access],
                item: Callable[[str, str], dict | None],
                datasets: Callable[[str, list[str]], dict] | None, users: list[str],
                now: datetime, app_url: str, make_until: float,
                email: EmailProvider | None = None,
                recipients: Callable[[str], str | None] = no_recipients,
                monotonic: Callable[[], float] = time.monotonic) -> WatchReport:
    """The pass (module docstring). `make_until`: when the filing pass's budget ran out
    (monotonic clock)."""
    report = WatchReport()
    landed = landed_at(store)
    fallback = now - FALLBACK
    catalog: dict[str, frozenset[str]] = {}

    def catalog_ids(user_id: str) -> frozenset[str]:
        """The public catalog's ids, once a run (the same for every user: app.py caches it)."""
        if "ids" not in catalog:
            catalog["ids"] = store.catalog_companies(user_id)
        return catalog["ids"]

    deadline = min(monotonic() + BUDGET, make_until + LATEST)
    for user_id in users:
        try:
            due = watches.due(user_id, landed, fallback)
        except Exception as e:
            report.errors += 1
            log.warning("watches: couldn't read a user's watches: %s", type(e).__name__,
                        extra={"user_id": user_id})
            continue
        if not due:
            continue
        if monotonic() > deadline:
            report.deferred += len(due)
            continue
        try:
            acc = access(user_id)  # layer 1 as the owner; OpenFGA down: skipped, fail closed
            readable = readable_from(acc, lambda u=user_id: catalog_ids(u))
        except (AuthzUnavailable, psycopg.Error) as e:
            report.errors += 1
            log.warning("watches: no access for a user: %s", type(e).__name__,
                        extra={"user_id": user_id})
            continue
        made: list[dict] = []
        for w in due:
            if monotonic() > deadline:
                report.deferred += 1
                continue
            if w.get("source") != "screen" or w.get("kind") != "screen":
                continue
            try:
                result = check(store, user_id, acc, readable, w, item=item, datasets=datasets,
                               landed=landed)
                alert_id = watches.record(user_id, w, result, now)
            except Exception as e:
                report.errors += 1
                log.warning("watches: a check failed: %s", type(e).__name__,
                            extra={"user_id": user_id, "watch_id": w["watch_id"]})
                continue
            report.checked += 1
            outcome = result["outcome"]
            report.baselines += outcome == "baseline"
            report.too_broad += outcome == "too_broad"
            report.failed += outcome == "failed"
            if alert_id:
                report.alerts += 1
                made.append({"name": w["name"], "item_id": w["item_id"],
                             "detail": result["detail"]})
        to = recipients(user_id) if email is not None and made else None
        if email is not None and to:
            try:
                email_digest(store, email, to, user_id, made, app_url)
            except Exception as e:  # the in-app alert and webhooks stand; logged, no retry
                log.warning("watches: email not sent: %s", type(e).__name__,
                            extra={"user_id": user_id})
        try:
            watches.prune(user_id, now - timedelta(days=KEEP_DAYS))
        except Exception as e:
            log.warning("watches: old alerts not pruned: %s", type(e).__name__,
                        extra={"user_id": user_id})
    return report
