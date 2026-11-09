"""Filing alerts against the real app database (migration 016): forced row-level security on
every table, the routes end to end with two users (one can't read, mark, change, test or see the
deliveries of the other's alerts and webhooks), and the job (query_api/alert_job.py) over a fake
filing index: follows, forms, pending then ready, red lines, routine 8-Ks, idempotence, webhook
deliveries with retries. Skipped unless APP_TEST_DATABASE_URL is set; every test is rolled back.
Never calls the network: the resolver and the sender are fakes."""

import json
import logging
import os
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.alert_job import deliver, run
from query_api.alerts import FORMS, MAX_RED_LINES_TOTAL, STALL_WAITS, AppAlerts
from query_api.app import create_app
from query_api.webhooks import Sent, signature, webhook_secret

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

A, B = "t-alert-a", "t-alert-b"
NVDA, MSFT = "0001045810", "0000789019"
Q3 = "0001045810-26-000101"     # NVDA 10-Q
REL = "0001045810-26-000099"    # NVDA 8-K 2.02 + 9.01
VOTE = "0001045810-26-000098"   # NVDA 8-K 5.07 (routine)
OLD8K = "0001045810-26-000050"  # NVDA 8-K filed before A followed NVDA
MSQ = "0000950170-26-000200"    # MSFT 10-Q (B doesn't follow MSFT)
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
TABLES = ["alert_settings", "notifications", "red_lines", "webhooks", "webhook_deliveries",
          "alert_checks"]
HOOK_URL = "https://hooks.example.com/tributary/" + "t" * 32


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@example.com'),"
                  " (%s, 'b@example.com')", (A, B))
        c.execute("INSERT INTO watchlists (user_id, company_id, added_at) VALUES"
                  " (%s, %s, '2026-11-10T12:00:00Z')", (A, NVDA))
        yield c


@pytest.fixture
def alerts(conn):
    return AppAlerts(OneConnection(conn))


def public_resolver(*_a, **_k):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


class RecordingSender:
    def __init__(self, results=None):
        self.sent, self.results = [], list(results or [])

    def send(self, url, body, headers):
        self.sent.append((url, body, headers))
        return self.results.pop(0) if self.results else Sent(True, 204)


def api(alerts, sender, grants=None):
    grants = grants if grants is not None else {
        A: {NVDA: {"financials", "filings"}}, B: {NVDA: {"financials", "filings"}}}
    return TestClient(create_app(FakeAuthorizer(grants), FakeStore(), SECRET, alerts=alerts,
                                 webhook_sender=sender, webhook_resolver=public_resolver))


# --- the tables ---------------------------------------------------------------------------------

@pytest.mark.parametrize("table", TABLES)
def test_every_alert_table_forces_rls_and_isnt_streamed(conn, table):
    rls, forced, identity = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity, relreplident FROM pg_class"
        " WHERE oid = %s::regclass", (table,)).fetchone()
    assert rls and forced
    assert identity == "d"  # not REPLICA IDENTITY FULL: not a CDC table (grants nothing)


def test_rls_binds_the_owner(conn, alerts):
    alerts.add_notifications(A, [{"company_id": NVDA, "accession_no": Q3, "form": "10-Q",
                                  "filed": date(2026, 11, 19), "period_end": date(2026, 10, 26),
                                  "items": None, "sources": ["watchlist"], "cik": NVDA}])
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    alerts.add_red_line(A, NVDA, "GM", "gross_margin", "ttm", "<", Decimal("0.7"))
    alerts.put_setting(A, NVDA, ["10-K"])
    alerts.checked(A, NOW, None)
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'notifications'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (B,))
    for table in TABLES:
        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier(table))).fetchone()[0] == 0, table
    assert conn.execute("UPDATE webhooks SET enabled = false").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO webhook_deliveries (user_id, webhook_id) VALUES (%s, %s)",
                     (A, hook["webhook_id"]))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    assert conn.execute("SELECT count(*) FROM notifications").fetchone()[0] == 1


def test_a_delivery_cant_point_at_another_users_webhook(conn, alerts):
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO webhook_deliveries (user_id, webhook_id) VALUES (%s, %s)",
                     (B, hook["webhook_id"]))


def test_checks_hold_the_shapes(conn):
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO webhooks (user_id, url, format, secret_salt)"
                     " VALUES (%s, 'http://169.254.169.254/', 'json', %s)", (A, b"s" * 16))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO alert_settings (user_id, company_id, forms)"
                     " VALUES (%s, %s, '{S-1}')", (A, NVDA))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO red_lines (user_id, company_id, label, formula, view, op,"
                     " threshold) VALUES (%s, %s, 'x', 'revenue', 'ttm', '!=', 1)", (A, NVDA))


# --- the routes, two users ------------------------------------------------------------------

def test_one_user_cant_read_change_or_trigger_anothers_webhooks(conn, alerts, token, caplog):
    caplog.set_level(logging.DEBUG)
    sender = RecordingSender()
    c = api(alerts, sender)
    made = c.post("/v1/alerts/webhooks", headers=token(A), json={"url": HOOK_URL})
    assert made.status_code == 200, made.text
    hook, secret = made.json()["webhook"], made.json()["secret"]
    assert hook["url"] == "https://hooks.example.com/…tttt" and hook["format"] == "json"
    hid = hook["id"]

    # B: nothing listed, and A's id is a 404 everywhere, with no send made
    assert c.get("/v1/alerts/webhooks", headers=token(B)).json() == {"webhooks": []}
    assert c.patch(f"/v1/alerts/webhooks/{hid}", headers=token(B),
                   json={"enabled": False}).status_code == 404
    assert c.post(f"/v1/alerts/webhooks/{hid}/rotate", headers=token(B)).status_code == 404
    assert c.post(f"/v1/alerts/webhooks/{hid}/test", headers=token(B)).status_code == 404
    assert c.delete(f"/v1/alerts/webhooks/{hid}", headers=token(B)).status_code == 404
    assert sender.sent == []

    # A's own test send: signed with A's secret, logged for A only
    r = c.post(f"/v1/alerts/webhooks/{hid}/test", headers=token(A))
    assert r.status_code == 200 and r.json()["ok"] is True
    ((url, body, headers),) = sender.sent
    assert url == HOOK_URL
    assert headers["X-Tributary-Signature"] == signature(
        secret, int(headers["X-Tributary-Timestamp"]), body)
    assert c.get("/v1/alerts/deliveries", headers=token(B),
                 params={"webhook_id": hid}).json()["deliveries"] == []
    (log,) = c.get("/v1/alerts/deliveries", headers=token(A)).json()["deliveries"]
    assert log["test"] and log["status"] == "delivered" and log["last_status"] == 204

    # the secret and the URL were shown once and never again, nor logged
    listed = c.get("/v1/alerts/webhooks", headers=token(A)).text
    settings = c.get("/v1/alerts/settings", headers=token(A)).text
    for text in (listed, settings, caplog.text):
        assert secret not in text and HOOK_URL not in text
    # rotating changes it (shown once more)
    rotated = c.post(f"/v1/alerts/webhooks/{hid}/rotate", headers=token(A)).json()["secret"]
    assert rotated != secret
    assert c.delete(f"/v1/alerts/webhooks/{hid}", headers=token(A)).json() == {"deleted": True}


def test_one_user_cant_read_or_mark_anothers_notifications(conn, alerts, token):
    alerts.add_notifications(A, [{"company_id": NVDA, "accession_no": REL, "form": "8-K",
                                  "filed": date(2026, 11, 19), "period_end": None,
                                  "items": ["2.02"], "sources": ["watchlist"], "cik": NVDA}])
    (n,) = alerts.pending(A)
    alerts.finish(A, [{"notification_id": n["notification_id"], "status": "ready",
                       "items": ["2.02"], "detail": {}}], NOW)
    c = api(alerts, RecordingSender())
    assert c.get("/v1/alerts/notifications", headers=token(B)).json()["notifications"] == []
    assert c.get("/v1/alerts/unread", headers=token(B)).json()["unread"] == 0
    r = c.post("/v1/alerts/notifications/read", headers=token(B),
               json={"ids": [n["notification_id"]]})
    assert r.json() == {"marked": 0}
    assert c.post("/v1/alerts/notifications/read", headers=token(B),
                  json={"all": True}).json() == {"marked": 0}
    assert c.get("/v1/alerts/unread", headers=token(A)).json()["unread"] == 1
    (shown,) = c.get("/v1/alerts/notifications", headers=token(A)).json()["notifications"]
    assert shown["items"][0]["item"] == "2.02" and shown["read_at"] is None
    assert c.post("/v1/alerts/notifications/read", headers=token(A),
                  json={"ids": [n["notification_id"]]}).json() == {"marked": 1}
    assert c.get("/v1/alerts/unread", headers=token(A)).json()["unread"] == 0


def test_red_lines_are_the_owners_and_evaluate_only_with_access(conn, alerts, token):
    store = FakeStore()
    store.formula = lambda user_id, compiled: [
        {"period_end": date(2026, 10, 26), "value": Decimal("0.735"), "period_key": 1}]
    grants = {A: {NVDA: {"financials"}}, B: {}}
    c = TestClient(create_app(FakeAuthorizer(grants), store, SECRET, alerts=alerts))
    body = {"company_id": NVDA, "label": "Gross margin below 75%", "formula": "gross_margin",
            "view": "ttm", "op": "<", "threshold": "0.75"}
    made = c.post("/v1/alerts/red-lines", headers=token(A), json=body).json()["red_line"]
    assert made["display"] == "percent" and made["threshold"] == "0.75"
    c.post("/v1/alerts/red-lines", headers=token(A),
           json={**body, "label": "Gross margin below 70%", "threshold": "0.70"})
    got = c.get("/v1/alerts/red-lines", headers=token(A),
                params={"company_id": NVDA, "evaluate": "true"}).json()
    assert [(e["label"], e["crossed"]) for e in got["evaluation"]] == [
        ("Gross margin below 75%", True), ("Gross margin below 70%", False)]
    # B sees none of A's rules, can't delete them, and can't evaluate without access
    assert c.get("/v1/alerts/red-lines", headers=token(B)).json()["red_lines"] == []
    assert c.delete(f"/v1/alerts/red-lines/{made['rule_id']}",
                    headers=token(B)).status_code == 404
    assert c.get("/v1/alerts/red-lines", headers=token(B),
                 params={"company_id": NVDA, "evaluate": "true"}).status_code == 403


def test_settings_list_follows_and_forms(conn, alerts, token):
    conn.execute("INSERT INTO workspaces (user_id, name, project) VALUES (%s, 'MSFT', %s)",
                 (A, psycopg.types.json.Jsonb({"companyId": MSFT, "peers": [], "notes": "",
                                               "done": []})))
    c = api(alerts, RecordingSender())
    assert c.put(f"/v1/alerts/settings/{MSFT}", headers=token(A),
                 json={"forms": ["10-K", "10-Q"]}).status_code == 200
    s = c.get("/v1/alerts/settings", headers=token(A)).json()
    by = {x["company_id"]: x for x in s["companies"]}
    assert by[NVDA]["sources"] == ["watchlist"] and by[NVDA]["forms"] == ["10-K", "10-Q", "8-K"]
    assert by[MSFT]["sources"] == ["analysis"] and by[MSFT]["forms"] == ["10-K", "10-Q"]
    assert c.get("/v1/alerts/settings", headers=token(B)).json()["companies"] == []


# --- the job ----------------------------------------------------------------------------------

def filing(acc, form, filed, company=NVDA, period=None, items=None):
    return {"company_id": company, "accession_no": acc, "cik": company, "form": form,
            "filing_date": filed, "accepted_at": None, "period_end": period, "items": items}


class JobStore:
    """The filing index, recaps and formula points the job reads, recording who asked."""

    def __init__(self):
        self.filings = [
            filing(Q3, "10-Q", date(2026, 11, 19), period=date(2026, 10, 26)),
            filing(REL, "8-K", date(2026, 11, 19), items=["2.02", "9.01"]),
            filing(VOTE, "8-K", date(2026, 11, 18), items=["5.07"]),
            filing(OLD8K, "8-K", date(2026, 11, 17), items=["5.02"]),
            filing(MSQ, "10-Q", date(2026, 11, 19), company=MSFT, period=date(2026, 9, 30)),
        ]
        self.recaps: dict[str, list[dict]] = {}
        self.asked: list[tuple] = []

    def alert_filings(self, user_id, ids, forms, since):
        self.asked.append(("index", user_id, tuple(ids)))
        return [f for f in self.filings
                if f["company_id"] in ids and f["form"] in forms and f["filing_date"] >= since]

    def recap(self, user_id, company_id, acc, period_end, metrics):
        self.asked.append(("recap", user_id, company_id, acc))
        return self.recaps.get(acc, [])

    def formula(self, user_id, compiled):
        self.asked.append(("formula", user_id))
        return [{"period_end": date(2026, 10, 26), "value": Decimal("0.735"), "period_key": 1}]

    def company_8ks(self, user_id, company_id, since):
        return []

    def profiles(self, user_id, ids):
        return {i: {"name": "NVIDIA"} for i in ids}

    def tickers(self, user_id, ids):
        return {i: ["NVDA"] for i in ids}


def recap_rows(acc):
    def row(metric, comparison, value, unit="USD", a=acc):
        return {"comparison": comparison, "metric": metric, "span": "quarter",
                "value": Decimal(value), "unit": unit, "period_end": date(2026, 10, 26),
                "accession_no": a, "source_accessions": [a], "not_meaningful": None}
    return [row("revenue", "current", "57006000000"),
            row("revenue", "year_ago", "35082000000", a="0001045810-25-000200"),
            row("gross_margin", "current", "0.735", "ratio")]


def run_job(alerts, store, sender, grants=None, now=NOW, **kw):
    auth = FakeAuthorizer(grants if grants is not None else {A: {NVDA: {"financials",
                                                                        "filings"}}})
    return run(alerts=alerts, store=store, can=auth.can, sender=sender, jwt_secret=SECRET,
               now=now, app_url="https://app.example", users=[A, B], **kw)


def test_the_job_alerts_followed_filings_once_and_completes_them(conn, alerts):
    store, sender = JobStore(), RecordingSender()
    hook = alerts.add_webhook(A, HOOK_URL, "discord", b"s" * 16)
    alerts.add_red_line(A, NVDA, "GM below 75%", "gross_margin", "ttm", "<", Decimal("0.75"))
    alerts.add_red_line(A, NVDA, "GM below 70%", "gross_margin", "ttm", "<", Decimal("0.70"))

    first = run_job(alerts, store, sender)
    # A follows NVDA since 2026-11-10: the 10-Q and both 8-Ks of the window (filed after);
    # B follows nothing; MSFT's 10-Q alerts no one
    assert first.created == 4 and first.errors == 0
    rows = {n["accession_no"]: n for n in alerts.notifications(A, None, 50)}
    assert set(rows) == {Q3, REL, OLD8K}  # the 5.07 vote is skipped, not shown
    assert rows[REL]["status"] == "ready" and rows[OLD8K]["status"] == "ready"
    assert rows[Q3]["status"] == "pending"  # gold doesn't have the 10-Q's figures yet
    assert alerts.notifications(B, None, 50) == []
    assert all(a[1] != B for a in store.asked if a[0] != "index")  # nothing read as B
    assert len(sender.sent) == 2  # the two ready 8-Ks, to A's webhook

    # the figures arrive: the 10-Q becomes ready with its red lines, and is sent once
    store.recaps[Q3] = recap_rows(Q3)
    second = run_job(alerts, store, sender, now=NOW + timedelta(minutes=10))
    assert second.created == 0 and second.ready == 1
    q = {n["accession_no"]: n for n in alerts.notifications(A, None, 50)}[Q3]
    assert q["status"] == "ready"
    assert [f["metric"] for f in q["detail"]["figures"]] == ["revenue", "gross_margin"]
    assert [(r["label"], r["crossed"]) for r in q["detail"]["red_lines"]] == [
        ("GM below 75%", True), ("GM below 70%", False)]
    assert len(sender.sent) == 3
    url, body, headers = sender.sent[-1]
    assert url == HOOK_URL and b"CROSSED" in body and b"allowed_mentions" in body
    assert headers["X-Tributary-Signature"] == signature(
        webhook_secret(SECRET, hook["webhook_id"], b"s" * 16),
        int(headers["X-Tributary-Timestamp"]), body)

    # idempotent: another run changes nothing and sends nothing
    third = run_job(alerts, store, sender, now=NOW + timedelta(minutes=20))
    assert (third.created, third.ready, third.delivered) == (0, 0, 0)
    assert len(sender.sent) == 3 and len(alerts.notifications(A, None, 50)) == 3
    assert alerts.unread(A)["last_check"]["checked_at"] == NOW + timedelta(minutes=20)


def test_the_job_respects_forms_and_access(conn, alerts):
    alerts.put_setting(A, NVDA, ["10-K", "10-Q"])
    store = JobStore()
    store.recaps[Q3] = recap_rows(Q3)
    run_job(alerts, store, RecordingSender(), grants={A: {}})
    (n,) = alerts.notifications(A, None, 50)
    assert n["accession_no"] == Q3 and n["status"] == "ready"
    assert n["detail"]["figures_status"] == "no_access" and n["detail"]["figures"] is None
    assert not [a for a in store.asked if a[0] in ("recap", "formula")]  # layer 1 said no


def test_a_failed_delivery_is_retried_after_its_backoff(conn, alerts):
    alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    store = JobStore()
    store.filings = [f for f in store.filings if f["accession_no"] == REL]
    sender = RecordingSender([Sent(False, 500, "http_500"), Sent(True, 200)])
    run_job(alerts, store, sender)
    (d,) = alerts.deliveries(A, None, 10)
    assert (d["status"], d["attempts"], d["last_error"]) == ("pending", 1, "http_500")
    run_job(alerts, store, sender, now=NOW + timedelta(minutes=1))  # before the backoff
    assert len(sender.sent) == 1
    run_job(alerts, store, sender, now=NOW + timedelta(minutes=6))
    (d,) = alerts.deliveries(A, None, 10)
    assert (d["status"], d["attempts"], len(sender.sent)) == ("delivered", 2, 2)


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text):
        self.sent.append((to, subject, text))


def test_email_needs_a_provider_and_a_verified_address(conn, alerts):
    store, outbox = JobStore(), Outbox()
    # a provider but no verified address (today's state): nothing is emailed
    run_job(alerts, store, RecordingSender(), email=outbox)
    assert outbox.sent == []
    conn.execute("DELETE FROM notifications WHERE user_id = %s", (A,))
    # with an address for A only: one digest of A's ready alerts, nothing for B
    report = run_job(alerts, store, RecordingSender(), email=outbox,
                     recipients=lambda u: "a@example.org" if u == A else None)
    assert report.emailed == 1
    ((to, subject, text),) = outbox.sent
    assert to == "a@example.org" and subject == "Tributary: 2 new filing alerts"
    assert "NVIDIA (NVDA) filed an 8-K" in text and "Items: 2.02" in text


def test_a_stalled_webhook_holds_up_only_its_owner(conn, alerts):
    """A tarpit endpoint: after its first timeout the rest of its deliveries wait for the next run,
    and another user's webhook is still sent within the run's budget."""
    conn.execute("INSERT INTO watchlists (user_id, company_id, added_at) VALUES"
                 " (%s, %s, '2026-11-10T12:00:00Z')", (B, NVDA))
    slow = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    alerts.add_webhook(A, HOOK_URL + "-second", "json", b"t" * 16)
    alerts.add_webhook(B, "https://hooks.example.org/b", "json", b"u" * 16)

    class Tarpit:
        def __init__(self):
            self.sent = []

        def send(self, url, body, headers):
            self.sent.append(url)
            return Sent(False, None, "timeout") if url.startswith(HOOK_URL) else Sent(True, 204)

    sender = Tarpit()
    order = []
    report = run(alerts=alerts, store=JobStore(), can=FakeAuthorizer({}).can, sender=sender,
                 jwt_secret=SECRET, now=NOW, users=[A, B],
                 shuffle=lambda users: order.extend(users))
    assert order == [A, B, A, B]  # making, then sending
    # each of A's two webhooks was tried once (then skipped for the run); B's got all three
    assert sender.sent.count(HOOK_URL) == 1 and sender.sent.count(HOOK_URL + "-second") == 1
    assert sender.sent.count("https://hooks.example.org/b") == 3
    assert report.delivered == 3 and report.failed == 2
    left = [d for d in alerts.deliveries(A, slow["webhook_id"], 10) if d["attempts"] == 0]
    assert left and all(d["status"] == "pending" for d in left)  # still due next run


def test_sends_stop_at_the_runs_budget(conn, alerts):
    alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    # making starts at 0 and reaches A at 1; sending starts at 1e6 and is past its budget by the
    # first send
    clock = iter([0.0, 1.0, 1e6, 3e6, 3e6, 3e6])
    sender = RecordingSender()
    run(alerts=alerts, store=JobStore(), can=FakeAuthorizer({}).can, sender=sender,
        jwt_secret=SECRET, now=NOW, users=[A], monotonic=lambda: next(clock))
    assert sender.sent == []
    assert {d["status"] for d in alerts.deliveries(A, None, 10)} == {"pending"}


def test_an_upper_case_scheme_is_stored_in_lower_case(conn, alerts, token):
    # it used to pass the check and fail the table's https:// CHECK, with the URL in the error
    c = api(alerts, RecordingSender())
    r = c.post("/v1/alerts/webhooks", headers=token(A),
               json={"url": "HTTPS://Hooks.Example.com/tributary/" + "t" * 32})
    assert r.status_code == 200, r.text
    (row,) = conn.execute("SELECT url FROM webhooks WHERE user_id = %s", (A,)).fetchall()
    assert row[0] == "https://hooks.example.com/tributary/" + "t" * 32


def test_red_lines_are_capped_per_user_and_only_followed_ones_are_planned(conn, alerts):
    for i in range(MAX_RED_LINES_TOTAL):
        conn.execute("INSERT INTO red_lines (user_id, company_id, label, formula, view, op,"
                     " threshold) VALUES (%s, %s, 'x', 'revenue', 'ttm', '<', 1)",
                     (A, f"{9000000000 + i // 10:010d}"))
    assert alerts.add_red_line(A, NVDA, "GM", "gross_margin", "ttm", "<", Decimal(1)) is None
    plan = alerts.plan(A)
    assert set(plan["red_lines"]) == set()  # A follows NVDA only, and has no rule for it
    assert len(alerts.red_lines(A, None)) == MAX_RED_LINES_TOTAL


def test_old_test_sends_are_pruned(conn, alerts):
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    alerts.record_test(A, hook["webhook_id"], Sent(True, 204), NOW - timedelta(days=200))
    conn.execute("UPDATE webhook_deliveries SET created_at = %s", (NOW - timedelta(days=200),))
    alerts.prune(A, NOW - timedelta(days=180))
    assert alerts.deliveries(A, None, 10) == []


def test_figures_go_out_only_while_the_company_is_readable(conn, alerts):
    """A delivery retried after the user lost access sends no figures (layer 1 at send time)."""
    alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    store = JobStore()
    store.filings = [f for f in store.filings if f["accession_no"] == Q3]
    store.recaps[Q3] = recap_rows(Q3)
    failing = RecordingSender([Sent(False, 500, "http_500"), Sent(True, 200)])
    run_job(alerts, store, failing)  # ready with figures; the send fails
    run_job(alerts, store, failing, grants={A: {}}, now=NOW + timedelta(minutes=6))
    first, second = (json.loads(b) for _, b, _ in failing.sent)
    assert first["figures"] and second["figures"] is None and second["red_lines"] == []
    assert second["figures_status"] == "no_access"


# --- pre-ship (2026-11-09): the 8-K default, stalled webhooks, a fair share of the budget -------

FD = "0001045810-26-000102"     # NVDA 8-K 7.01 + 9.01 (an investor presentation)
OTHER = "0001045810-26-000103"  # NVDA 8-K 8.01 alone
DEAL = "0001045810-26-000104"   # NVDA 8-K 1.01 + 8.01
LATER_FD = "0001045810-26-000105"


def eight_ks():
    store = JobStore()
    store.filings = [filing(FD, "8-K", date(2026, 11, 19), items=["7.01", "9.01"]),
                     filing(OTHER, "8-K", date(2026, 11, 19), items=["8.01"]),
                     filing(DEAL, "8-K", date(2026, 11, 18), items=["1.01", "8.01"])]
    return store


def test_7_01_and_8_01_8ks_alert_only_for_companies_that_asked(conn, alerts):
    alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    sender = RecordingSender()
    run_job(alerts, eight_ks(), sender)
    shown = {n["accession_no"]: n for n in alerts.notifications(A, None, 50)}
    assert set(shown) == {DEAL}  # the deal alerts; the presentation and 8.01 alone don't
    assert len(sender.sent) == 1 and alerts.unread(A)["unread"] == 1
    quiet = conn.execute("SELECT accession_no, detail->>'quiet' FROM notifications"
                         " WHERE user_id = %s AND status = 'skipped' ORDER BY 1", (A,)).fetchall()
    assert quiet == [(FD, "true"), (OTHER, "true")]  # kept, never shown or sent

    # turned on for NVDA: the next 7.01 / 8.01 8-Ks alert (the skipped ones stay skipped)
    assert alerts.put_setting(A, NVDA, list(FORMS), True) is True
    later = eight_ks()
    later.filings.append(filing(LATER_FD, "8-K", date(2026, 11, 20), items=["7.01"]))
    run_job(alerts, later, sender, now=NOW + timedelta(minutes=10))
    shown = {n["accession_no"] for n in alerts.notifications(A, None, 50)}
    assert shown == {DEAL, LATER_FD} and len(sender.sent) == 2


def test_other_8ks_is_saved_kept_by_an_older_web_app_and_the_default_is_no_row(conn, alerts,
                                                                                token):
    # a row saved before 017 reads false: the new default
    conn.execute("INSERT INTO alert_settings (user_id, company_id, forms) VALUES"
                 " (%s, %s, '{10-K,8-K}')", (A, MSFT))
    c = api(alerts, RecordingSender())
    s = c.get("/v1/alerts/settings", headers=token(A)).json()
    assert s["other_8k_items"] == ["7.01", "8.01"]
    by = {x["company_id"]: x for x in s["companies"]}
    assert by[NVDA]["other_8ks"] is False and by[NVDA]["forms"] == ["10-K", "10-Q", "8-K"]
    assert alerts.settings(A)[MSFT] == {"forms": ["10-K", "8-K"], "other_8ks": False}

    r = c.put(f"/v1/alerts/settings/{NVDA}", headers=token(A),
              json={"forms": ["10-K", "10-Q", "8-K"], "other_8ks": True})
    assert r.json() == {"company_id": NVDA, "forms": ["10-K", "10-Q", "8-K"], "other_8ks": True}
    # an older web app saves forms only: the choice stays
    r = c.put(f"/v1/alerts/settings/{NVDA}", headers=token(A), json={"forms": ["10-Q", "8-K"]})
    assert r.json()["other_8ks"] is True
    assert alerts.settings(A)[NVDA] == {"forms": ["10-Q", "8-K"], "other_8ks": True}
    # back to the default: no row
    c.put(f"/v1/alerts/settings/{NVDA}", headers=token(A),
          json={"forms": ["10-K", "10-Q", "8-K"], "other_8ks": False})
    assert NVDA not in alerts.settings(A)
    assert c.get("/v1/alerts/settings", headers=token(B)).json()["companies"] == []


class Stalling:
    """A sender whose URLs starting with `bad` stall with `code`; every other one arrives."""

    def __init__(self, bad, code="timeout"):
        self.bad, self.code, self.sent = bad, code, []

    def send(self, url, body, headers):
        self.sent.append(url)
        return Sent(False, None, self.code) if url.startswith(self.bad) else Sent(True, 204)


def ready_8ks(alerts, user, first, n, at):
    """n ready 8-Ks for `user`: a delivery each for every enabled webhook, due at `at`."""
    rows = [{"company_id": NVDA, "accession_no": f"0001045810-26-{first + i:06d}", "form": "8-K",
             "filed": date(2026, 11, 19), "period_end": None, "items": ["2.02"],
             "sources": ["watchlist"], "cik": NVDA} for i in range(n)]
    alerts.add_notifications(user, rows)
    mine = {r["accession_no"] for r in rows}
    pending = [p for p in alerts.pending(user) if p["accession_no"] in mine]
    alerts.finish(user, [{"notification_id": p["notification_id"], "status": "ready",
                          "items": ["2.02"], "detail": {}} for p in pending], at)


def test_one_stalled_webhook_doesnt_starve_the_users_others(conn, alerts):
    """The review's minor: 12 older deliveries to a dead webhook filled the 10-row batch and the
    user's other webhook got nothing. Deliveries are now taken in turns across webhooks."""
    alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    ready_8ks(alerts, A, 900, 12, NOW - timedelta(hours=1))  # only the first webhook exists yet
    alerts.add_webhook(A, "https://hooks.example.org/ok", "json", b"t" * 16)
    ready_8ks(alerts, A, 950, 1, NOW)
    sender = Stalling(HOOK_URL)
    ok, failed, stalled = deliver(alerts, JobStore(), sender, A, jwt_secret=SECRET,
                                  app_url="https://app.example", at=NOW,
                                  can=FakeAuthorizer({}).can)
    assert sender.sent.count(HOOK_URL) == 1  # stalled once, then skipped for the run
    assert sender.sent.count("https://hooks.example.org/ok") == 1  # its delivery went out
    assert (ok, failed, stalled) == (1, 1, 1)


@pytest.mark.parametrize("code", ["timeout", "dns_timeout", "unresolvable", "connect", "tls",
                                  "private_address"])
def test_repeated_stalls_leave_a_webhook_out_of_later_runs(conn, alerts, token, code):
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    store = JobStore()
    store.filings = [f for f in store.filings if f["accession_no"] == REL]
    sender = Stalling(HOOK_URL, code)
    run_job(alerts, store, sender)                                   # stall 1: this run only
    run_job(alerts, store, sender, now=NOW + timedelta(minutes=10))  # stall 2: 30 minutes off
    assert len(sender.sent) == 2
    (w,) = api(alerts, sender).get("/v1/alerts/webhooks", headers=token(A)).json()["webhooks"]
    assert w["stalls"] == 2
    assert datetime.fromisoformat(w["stalled_until"]) == NOW + timedelta(minutes=40)
    run_job(alerts, store, sender, now=NOW + timedelta(minutes=20))  # left out
    assert len(sender.sent) == 2
    (d,) = alerts.deliveries(A, None, 10)
    assert d["status"] == "pending"  # still due once the webhook is tried again
    # a test send that arrives clears it; the next run once the delivery's own backoff is over
    # (30 minutes after its 2nd try) sends
    assert alerts.record_test(A, hook["webhook_id"], Sent(True, 204), NOW)
    w = alerts.webhook(A, hook["webhook_id"])
    assert (w["stalls"], w["stalled_until"]) == (0, None)
    sender.bad = "https://nothing-stalls"
    run_job(alerts, store, sender, now=NOW + timedelta(minutes=40))
    assert len(sender.sent) == 3
    assert {d["status"] for d in alerts.deliveries(A, None, 10)} == {"delivered"}


def test_an_endpoint_flipping_between_slow_and_private_reaches_the_long_waits(conn, alerts):
    """Pre-ship review: a send refused at send time (the name now resolves to a private address)
    cleared the stall count, so an endpoint that answered slowly one run and resolved privately
    the next was never left out for more than a run. A refusal counts as a stall now."""
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    store = JobStore()
    store.filings = [f for f in store.filings if f["accession_no"] == REL]
    sender = Stalling(HOOK_URL)
    at = NOW
    for n, code in enumerate(["timeout", "private_address", "timeout", "private_address"], 1):
        w = alerts.webhook(A, hook["webhook_id"])
        at = max(at + timedelta(minutes=10), w["stalled_until"] or at)  # once it is due again
        sender.code = code
        run_job(alerts, store, sender, now=at)
        assert len(sender.sent) == n  # tried each time it was due
        w = alerts.webhook(A, hook["webhook_id"])
        assert w["stalls"] == n
        assert w["stalled_until"] == at + STALL_WAITS[min(n, len(STALL_WAITS)) - 1]
    assert w["stalled_until"] == at + timedelta(hours=6)  # the longest wait
    (d,) = alerts.deliveries(A, None, 10)
    assert d["status"] == "pending" and d["last_error"] == "private_address"


def test_turning_a_webhook_back_on_clears_its_stall(conn, alerts):
    hook = alerts.add_webhook(A, HOOK_URL, "json", b"s" * 16)
    for _ in range(3):
        alerts.webhook_result(A, hook["webhook_id"], True, NOW)
    assert alerts.webhook(A, hook["webhook_id"])["stalls"] == 3
    assert alerts.webhook(A, hook["webhook_id"])["stalled_until"] == NOW + timedelta(hours=2)
    alerts.set_webhook(A, hook["webhook_id"], False)
    assert alerts.webhook(A, hook["webhook_id"])["stalls"] == 3
    row = alerts.set_webhook(A, hook["webhook_id"], True)
    assert (row["stalls"], row["stalled_until"]) == (0, None)
    # B can't touch it
    alerts.webhook_result(B, hook["webhook_id"], True, NOW)
    assert alerts.webhook(A, hook["webhook_id"])["stalls"] == 0


def test_each_user_gets_a_fair_share_of_the_send_budget(conn, alerts):
    """A slow endpoint that answers (no stall) used to keep the first user sending until the
    run's 240 s were gone. Each user now gets what's left over the users left."""
    conn.execute("INSERT INTO watchlists (user_id, company_id, added_at) VALUES"
                 " (%s, %s, '2026-11-10T12:00:00Z')", (B, NVDA))
    for i in range(3):
        alerts.add_webhook(A, f"https://slow.example.com/{i}", "json", bytes([i + 1]) * 16)
    alerts.add_webhook(B, "https://hooks.example.org/b", "json", b"u" * 16)
    clock = [0.0]

    class Slow:
        def __init__(self):
            self.sent = []

        def send(self, url, body, headers):
            self.sent.append(url)
            if url.startswith("https://slow."):
                clock[0] += 30.0  # 30 s and a 500: answered, so never stalled
                return Sent(False, 500, "http_500")
            return Sent(True, 204)
    sender = Slow()
    run(alerts=alerts, store=JobStore(), can=FakeAuthorizer({}).can, sender=sender,
        jwt_secret=SECRET, now=NOW, users=[A, B], monotonic=lambda: clock[0],
        shuffle=lambda users: users.sort())  # A first, in making and in sending
    a_sends = [u for u in sender.sent if u.startswith("https://slow.")]
    assert len(a_sends) == 4  # A's share: 120 s of 240, sends started at 0, 30, 60 and 90
    assert sender.sent.count("https://hooks.example.org/b") == 3  # B still gets all of its own
