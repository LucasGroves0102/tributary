"""Saved screens that alert (query_api/watches.py, watch_job.py) without a database: the rules
(readings, crossings, first-check silence, caps, what a user still reads), the Screen a saved
screen becomes (the web's screenParams, then /v1/screener's), the routes with two users, and the
job's pass as each watch's owner over fakes, through to a webhook send. The SQL is in
test_watches_db.py (app database) and test_watch_screens_db.py (the screen under RLS)."""

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from query_api import watch_job, watches
from query_api.alert_job import deliver
from query_api.app import create_app
from query_api.authorizer import Access, AuthzUnavailable, Readable
from query_api.screener import Filter, compile_screen
from query_api.watch_job import run_watches
from query_api.watches import (
    MAX_LISTED,
    MAX_MEMBERS,
    MAX_WATCHES,
    Snapshot,
    TooMany,
    changes,
    comparable,
    fraction,
    past,
    screen_of,
    visible,
)
from query_api.webhooks import Sent

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

A, B = "t-watch-a", "t-watch-b"
SCREEN_A, SCREEN_B = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"
METRIC_A = "aaaaaaaa-0000-4000-8000-0000000000aa"
NVDA, MSFT, TSLA, AAPL = "0001045810", "0000789019", "0001318605", "0000320193"
NOW = datetime(2026, 11, 18, 11, 0, tzinfo=UTC)
LANDED = datetime(2026, 11, 18, 10, 30, tzinfo=UTC)
APP = "https://app.example"


# --- rules ------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, expected", [
    (None, Decimal("0.3")), ("behind", Decimal("0.3")),
    ("not_meaningful", None), ("not_applicable", None), ("old", None),
    ("other_currency", None)])
def test_only_a_value_that_reads_as_one_is_compared(status, expected):
    """k<i>'s rule: n/m, n/a, old (stale) and another currency never compare."""
    assert comparable(Decimal("0.3"), status) == expected
    assert comparable(None, None) is None


def test_a_crossing_is_strictly_past_the_line():
    above = {"direction": "above", "threshold": "0.25"}
    below = {"direction": "below", "threshold": "0.25"}
    assert past(Decimal("0.2501"), above) and not past(Decimal("0.25"), above)
    assert past(Decimal("0.2499"), below) and not past(Decimal("0.25"), below)


@pytest.mark.parametrize("percent, text", [(20, "0.2"), (12.5, "0.125"), (-3, "-0.03"),
                                           (33.3333333333333, "0.333333333333")])
def test_percent_becomes_the_fraction_the_web_app_sends(percent, text):
    """companies/screen.ts fraction: Number((percent / 100).toPrecision(12))."""
    assert fraction(percent) == Decimal(text)


def test_a_saved_screen_runs_as_the_companies_page_sends_it():
    """screenParams (web) then /v1/screener's Screen, plus: only companies the owner reads,
    every page in company-id order, the lines' columns read without listing more companies."""
    state = {"sector": "Technology", "sector_industry": "Semiconductors", "band": "1b-10b",
             "growth": 20, "margin": 12.5, "fye": 12, "exchange": "Nasdaq",
             "filer": "large_accelerated", "sic": "3674", "industry": "semi",
             "sort": "-revenue_ttm", "mine": True}
    s = screen_of(state, ["revenue_ttm", "gross_margin_ttm"], {}, ["net_margin_ttm"])
    assert s.filters == [Filter("revenue_growth_ttm", "gte", Decimal("0.2")),
                         Filter("operating_margin_ttm", "gte", Decimal("0.125")),
                         Filter("revenue_ttm", "gte", Decimal("1e9")),
                         Filter("revenue_ttm", "lt", Decimal("1e10"))]
    assert (s.sectors, s.sector_industries, s.sic, s.industry) == (
        ["Technology"], ["Semiconductors"], ["3674"], "semi")
    assert (s.exchanges, s.fye_months, s.filer_categories) == (["Nasdaq"], [12],
                                                               ["large_accelerated"])
    assert s.columns == ["revenue_ttm", "gross_margin_ttm", "net_margin_ttm"]
    assert s.blank_columns == ["revenue_ttm", "gross_margin_ttm"]
    assert s.granted_only and s.coverage and s.hide_blank
    assert (s.sort, s.limit, s.offset) == ("company_id", MAX_MEMBERS + 1, 0)


def test_a_name_or_a_data_check_lists_companies_whatever_they_have():
    assert not screen_of({"q": "NVDA"}, [], {}, []).hide_blank
    assert not screen_of({"issue": "outlier"}, [], {}, []).hide_blank
    plain = screen_of({}, [], {}, [])
    assert plain.columns == list(watches.DEFAULT_SCREENER) and plain.blank_columns is None


def test_reading_more_columns_leaves_a_plain_screens_sql_as_it_was():
    """Screen.blank_columns = None: the screener's SQL byte for byte."""
    s = screen_of({"growth": 10}, ["revenue_ttm"], {}, [])
    with_blank = screen_of({"growth": 10}, ["revenue_ttm"], {}, ["revenue_ttm"])
    assert compile_screen(s, None).sql == compile_screen(with_blank, None).sql
    wider = compile_screen(screen_of({"growth": 10}, ["revenue_ttm"], {}, ["net_margin_ttm"]),
                           None)
    assert wider.params["has_ttm"] == sorted({"revenue"})  # hide_blank: the shown columns only


LINE = {"column": "operating_margin_ttm", "label": "Op margin", "direction": "above",
        "threshold": "0.25", "company_id": None}


def snap(members, values=None, line_values=None):
    vals = [{c: (Decimal(v) if v is not None else None, v) for c, v in lv.items()}
            for lv in (line_values or [])]
    return Snapshot(members=sorted(members), total=len(members), values=vals,
                    units=[("ratio", "percent")] * len(vals))


def test_the_first_check_only_records():
    watch = {"entering": True, "leaving": True, "lines": [LINE]}
    detail, state = changes(watch, None, snap([NVDA, MSFT], line_values=[{NVDA: "0.2",
                                                                          MSFT: "0.3"}]))
    assert detail is None
    assert state == {"members": [MSFT, NVDA], "near": {"0": [NVDA]}}


def test_entering_leaving_and_a_crossing():
    watch = {"entering": True, "leaving": True, "lines": [LINE]}
    before = {"members": [NVDA, MSFT], "near": {"0": [NVDA]}}
    detail, state = changes(watch, before, snap([NVDA, TSLA], line_values=[
        {NVDA: "0.31", TSLA: "0.1"}]))
    assert detail == {
        "crossed": [{"line": 0, "column": "operating_margin_ttm", "label": "Op margin",
                     "direction": "above", "threshold": "0.25", "company_id": None,
                     "unit": "ratio", "display": "percent",
                     "companies": [{"company_id": NVDA, "value": "0.31"}], "more": 0}],
        "entered": {"companies": [TSLA], "more": 0},
        "left": {"companies": [MSFT], "more": 0}}
    assert state["near"] == {"0": [TSLA]}


def test_entering_or_leaving_off_says_nothing_of_it():
    before = {"members": [NVDA], "near": {}}
    detail, _ = changes({"entering": False, "leaving": True, "lines": []}, before,
                        snap([NVDA, TSLA]))
    assert detail is None


def test_a_value_that_stops_reading_never_crosses():
    """near before, n/m now (None): no crossing; unknown before, past now: no crossing."""
    watch = {"entering": False, "leaving": False, "lines": [LINE]}
    before = {"members": [NVDA, MSFT], "near": {"0": [NVDA]}}
    s = snap([NVDA, MSFT], line_values=[{NVDA: None, MSFT: "0.9"}])
    assert changes(watch, before, s)[0] is None


def test_an_any_company_line_counts_only_companies_still_in_the_screen():
    watch = {"entering": False, "leaving": True, "lines": [LINE]}
    before = {"members": [NVDA, MSFT], "near": {"0": [NVDA, MSFT]}}
    detail, _ = changes(watch, before, snap([NVDA], line_values=[{NVDA: "0.1"}]))
    assert detail == {"left": {"companies": [MSFT], "more": 0}}


def test_one_alert_lists_at_most_max_listed_companies_then_how_many_more():
    many = [f"{i:010d}" for i in range(1, 31)]
    watch = {"entering": True, "leaving": True, "lines": [LINE]}
    before = {"members": many[:10] + [AAPL], "near": {"0": many[:5]}}
    now = many[:10] + many[10:30]  # 20 entered, AAPL left, 5 crossed
    detail, _ = changes(watch, before, snap(now, line_values=[{c: "0.5" for c in now}]))
    assert detail is not None
    assert [c["company_id"] for c in detail["crossed"][0]["companies"]] == many[:5]
    assert len(detail["entered"]["companies"]) == MAX_LISTED - 5
    assert detail["entered"]["more"] == 5
    assert detail["left"] == {"companies": [], "more": 1}
    assert len(watches.listed(detail)) == MAX_LISTED


def test_shown_and_sent_without_companies_the_user_no_longer_reads():
    detail = {"crossed": [{**LINE, "line": 0, "companies": [{"company_id": NVDA, "value": "1"},
                                                            {"company_id": MSFT, "value": "2"}],
                           "more": 0}],
              "entered": {"companies": [TSLA], "more": 0}, "left": {"companies": [AAPL],
                                                                    "more": 3}}
    out = visible(detail, lambda c: c == NVDA)
    assert out["crossed"][0]["companies"] == [{"company_id": NVDA, "value": "1"}]
    assert "entered" not in out
    assert out["left"] == {"companies": [], "more": 3}
    assert out["hidden"] == 3


def test_the_message_is_plain_words_with_a_link_back():
    alert = {"name": "Cheap growers", "detail": {
        "crossed": [{**LINE, "line": 0, "unit": "ratio", "display": "percent",
                     "companies": [{"company_id": NVDA, "value": "0.312"}], "more": 2}],
        "entered": {"companies": [TSLA], "more": 0}}}
    text = watches.message(alert, lambda c: {NVDA: "NVIDIA (NVDA)", TSLA: "Tesla (TSLA)"}[c],
                           watches.screen_links(APP, SCREEN_A))
    assert text.splitlines() == [
        "Your screen “Cheap growers” changed with the latest figures",
        "Op margin rose above 25.0%: NVIDIA (NVDA) 31.2% and 2 more",
        "Entered: Tesla (TSLA)",
        f"Open the screen: {APP}/#/screen/{SCREEN_A}"]


@pytest.mark.parametrize("value, unit, display, text", [
    ("1.5", "ratio", "multiple", "1.50x"), ("0.2", "ratio", "percent", "20.0%"),
    ("2500000000", "USD", None, "$2.5B"), ("3.2", "ratio", "number", "3.20")])
def test_figures_read_as_the_screener_shows_them(value, unit, display, text):
    assert watches.figure(value, unit, display) == text


def test_the_basis_moves_with_the_screen_lines_metrics_and_access():
    acc = Access(frozenset({"financials"}), {})
    b = watches.basis({"growth": 10}, [], [LINE], {}, acc)
    assert b == watches.basis({"growth": 10}, [], [LINE], {}, acc)
    assert b != watches.basis({"growth": 11}, [], [LINE], {}, acc)
    assert b != watches.basis({"growth": 10}, ["revenue_ttm"], [LINE], {}, acc)
    assert b != watches.basis({"growth": 10}, [], [], {}, acc)
    assert b != watches.basis({"growth": 10}, [], [LINE], {}, Access(frozenset(), {NVDA: {
        "financials"}}))


def test_no_network_but_through_alert_job_and_webhooks():
    """Webhook sends happen only in alert_job.deliver through webhooks.Sender (HttpsSender in
    the Job): the watch modules import nothing that could post anywhere."""
    banned = {"http", "httpx", "urllib", "requests", "socket", "smtplib", "ssl"}
    for name in ("watches.py", "watch_job.py"):
        tree = ast.parse((Path(watches.__file__).parent / name).read_text())
        mods = {(n.module or "").split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)}
        mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        assert not mods & banned, (name, mods & banned)


# --- fakes ------------------------------------------------------------------------------------

class FakeWatches:
    """AppWatches in memory: each user's own rows only (what the forced RLS does)."""

    def __init__(self):
        self.items: dict[str, dict[str, dict]] = {}   # user → item_id → item
        self.rows: dict[tuple[str, str], dict] = {}   # (user, item) → watch
        self.made: list[dict] = []
        self.deliveries: list[dict] = []
        self.webhooks: dict[str, list[str]] = {}
        self.n = 0

    def add_item(self, user, item_id, kind="screen", name="My screen", state=None):
        self.items.setdefault(user, {})[item_id] = {
            "item_id": item_id, "kind": kind, "name": name, "state": state or {},
            "updated_at": NOW - timedelta(days=3)}

    def screen(self, user_id, item_id):
        it = self.items.get(user_id, {}).get(item_id)
        return it if it and it["kind"] == "screen" else None

    def watches(self, user_id):
        return [self._out(w) for (u, _), w in self.rows.items() if u == user_id]

    def _out(self, w):
        return {**w, "name": self.items[w["user_id"]][w["item_id"]]["name"]}

    def put(self, user_id, item_id, entering, leaving, columns, lines):
        if self.screen(user_id, item_id) is None:
            return None
        if sum(1 for (u, i) in self.rows if u == user_id and i != item_id) >= MAX_WATCHES:
            raise TooMany()
        self.n += 1
        old = self.rows.get((user_id, item_id), {})
        self.rows[(user_id, item_id)] = {
            "watch_id": old.get("watch_id", f"00000000-0000-4000-8000-{self.n:012d}"),
            "user_id": user_id, "source": "screen", "item_id": item_id, "entering": entering,
            "leaving": leaving, "columns": columns, "lines": lines,
            "basis": old.get("basis"), "members": old.get("members"), "near": old.get("near"),
            "checked_at": old.get("checked_at"), "outcome": old.get("outcome"),
            "companies": old.get("companies"), "created_at": NOW, "updated_at": NOW}
        return self._out(self.rows[(user_id, item_id)])

    def delete(self, user_id, item_id):
        return self.rows.pop((user_id, item_id), None) is not None

    def alerts(self, user_id, item_id, limit):
        return [a for a in reversed(self.made) if a["user_id"] == user_id
                and (item_id is None or a["item_id"] == item_id)][:limit]

    def due(self, user_id, landed, fallback):
        out = []
        for (u, i), w in self.rows.items():
            c = w["checked_at"]
            if u == user_id and (c is None or (landed and c < landed) or c < fallback
                                 or w["updated_at"] > c):
                it = self.items[u][i]
                out.append({**w, "kind": it["kind"], "name": it["name"], "state": it["state"]})
        return out

    def record(self, user_id, watch, result, at):
        w = self.rows[(user_id, watch["item_id"])]
        w["checked_at"], w["outcome"] = at, result["outcome"]
        if result["outcome"] == "failed":
            return None
        w.update(basis=result.get("basis"), members=result.get("members"),
                 near=result.get("near"), companies=result.get("companies"))
        if not result.get("detail"):
            return None
        self.n += 1
        alert = {"alert_id": f"a0000000-0000-4000-8000-{self.n:012d}", "user_id": user_id,
                 "watch_id": watch["watch_id"], "item_id": watch["item_id"],
                 "name": watch["name"], "detail": result["detail"], "created_at": at,
                 "read_at": None}
        self.made.append(alert)
        for hook in self.webhooks.get(user_id, []):
            self.deliveries.append({"user_id": user_id, "webhook_id": hook,
                                    "alert": alert["alert_id"]})
        return alert["alert_id"]

    def prune(self, user_id, before):
        return 0


class ScreenStore:
    """The screen as SQL would answer it: `members[user]` are the companies passing the
    filters that RLS lets the user read; a closed catalog's id list narrows them too. `leak`:
    rows SQL returns although layer 1 says no (layer 1 must drop them). Values by column."""

    def __init__(self):
        self.members: dict[str, list[str]] = {}
        self.values: dict[str, dict[str, tuple[Any, str | None]]] = {}  # company → column →
        self.leak: dict[str, list[str]] = {}  # user → companies
        self.calls: list[tuple[str, dict]] = []
        self.landed: datetime | None = LANDED

    def figures_landed_at(self):
        return self.landed

    def catalog_companies(self, user_id):
        self.calls.append((user_id, {"catalog": True}))
        return frozenset({NVDA, MSFT, TSLA, AAPL})

    def screen(self, user_id, compiled, known_on=None):
        self.calls.append((user_id, compiled.params))
        granted = compiled.params.get("granted")
        ids = compiled.params.get("ids")
        leak = self.leak.get(user_id, [])
        pool = list(ids) if ids is not None else self.members.get(user_id, []) + leak
        rows = []
        for cid in pool:
            if granted is not None and cid not in granted and cid not in leak:
                continue
            row: dict[str, Any] = {"company_id": cid, "name": cid}
            for key, i in compiled.slots.items():
                v, st = self.values.get(cid, {}).get(key, (None, None))
                row[f"v{i}"], row[f"st{i}"] = v, st
            rows.append(row)
        for r in rows:
            r["total"] = len(rows)
        return rows

    def profiles(self, user_id, ids):
        return {i: {"name": f"Co {i[-4:]}"} for i in ids}

    def tickers(self, user_id, ids):
        return {i: [f"T{i[-2:]}"] for i in ids}


class Auth:
    """OpenFGA per user: closed catalog (an id list) or open (`public`)."""

    def __init__(self, listed=None, public=frozenset(), down=False):
        self.listed, self.public, self.down = listed or {}, public, down
        self.asked: list[str] = []

    def access(self, user_id):
        self.asked.append(user_id)
        if self.down:
            raise AuthzUnavailable("down")
        return Access(self.public, {c: {"financials"} for c in self.listed.get(user_id, [])})


def job(store, ws, auth, now=NOW, **kw):
    kw.setdefault("item", lambda u, i: ws.items.get(u, {}).get(i))
    return run_watches(watches=ws, store=store, access=auth.access, datasets=None,
                       users=[A, B], now=now, app_url=APP, make_until=float("inf"), **kw)


def watched(ws, user=A, item=SCREEN_A, state=None, lines=(), entering=True, leaving=True):
    ws.add_item(user, item, state=state if state is not None else {"growth": 10})
    ws.put(user, item, entering, leaving, [], list(lines))


# --- the job, as each watch's owner -----------------------------------------------------------

def test_the_job_checks_each_screen_as_its_owner_never_with_anothers_access():
    """A reads NVDA and MSFT; B also TSLA. The same screen run for A never lists TSLA, even
    when SQL hands it back (layer 1 drops it), and A's id list is A's."""
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    watched(ws, B, SCREEN_B)
    store.members = {A: [NVDA, MSFT], B: [NVDA, MSFT, TSLA]}
    auth = Auth({A: [NVDA, MSFT], B: [NVDA, MSFT, TSLA]})
    job(store, ws, auth)  # baselines
    assert ws.rows[(B, SCREEN_B)]["members"] == [MSFT, NVDA, TSLA]
    store.leak = {A: [TSLA]}  # SQL lets TSLA through for A: layer 1 must not
    store.members = {A: [NVDA, MSFT], B: [NVDA, MSFT, TSLA, AAPL]}
    auth.listed[B].append(AAPL)  # B's access changed: B's next check only records
    store.calls.clear()
    store.landed = NOW + timedelta(hours=20)
    report = job(store, ws, auth, now=NOW + timedelta(days=1))
    assert (report.checked, report.alerts) == (2, 0)
    assert ws.rows[(A, SCREEN_A)]["members"] == [MSFT, NVDA]
    assert ws.rows[(B, SCREEN_B)]["outcome"] == "baseline"
    asked = [(u, p["granted"]) for u, p in store.calls if "granted" in p]
    assert asked == [(A, [MSFT, NVDA]), (B, sorted([NVDA, MSFT, TSLA, AAPL]))]


def test_first_check_silent_then_entering_and_leaving_alert():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    store.members = {A: [NVDA, MSFT]}
    auth = Auth({A: [NVDA, MSFT, TSLA]})
    first = job(store, ws, auth)
    assert (first.checked, first.baselines, first.alerts) == (1, 1, 0)
    assert ws.rows[(A, SCREEN_A)]["outcome"] == "baseline"
    # nothing landed since: not due again
    assert job(store, ws, auth, now=NOW + timedelta(minutes=10)).checked == 0
    store.members = {A: [NVDA, TSLA]}
    store.landed = NOW + timedelta(hours=20)
    second = job(store, ws, auth, now=NOW + timedelta(hours=21))
    assert (second.checked, second.alerts) == (1, 1)
    detail = ws.made[-1]["detail"]
    assert detail["entered"] == {"companies": [TSLA], "more": 0}
    assert detail["left"] == {"companies": [MSFT], "more": 0}
    assert detail["figures_at"] == store.landed.isoformat()


def test_without_a_landing_time_a_watch_is_checked_once_a_day():
    ws, store = FakeWatches(), ScreenStore()
    store.landed = None
    watched(ws, A, SCREEN_A)
    store.members = {A: [NVDA]}
    auth = Auth({A: [NVDA]})
    assert job(store, ws, auth).checked == 1
    assert job(store, ws, auth, now=NOW + timedelta(hours=20)).checked == 0
    assert job(store, ws, auth, now=NOW + watches.FALLBACK + timedelta(minutes=1)).checked == 1


def test_a_changed_screen_or_watch_records_again_silently():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    store.members = {A: [NVDA]}
    auth = Auth({A: [NVDA, MSFT]})
    job(store, ws, auth)
    ws.items[A][SCREEN_A]["state"] = {"growth": 30}   # saved over: another screen
    store.members = {A: [MSFT]}
    store.landed = NOW + timedelta(hours=20)
    later = job(store, ws, auth, now=NOW + timedelta(hours=21))
    assert (later.baselines, later.alerts) == (1, 0)


def test_a_screen_too_broad_alerts_nothing_and_stores_nothing():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    many = [f"{i:010d}" for i in range(1, MAX_MEMBERS + 2)]
    store.members = {A: many}
    auth = Auth(public=frozenset({"financials"}))
    store.catalog_companies = lambda _u: frozenset(many)  # type: ignore[method-assign]
    assert job(store, ws, auth).too_broad == 1
    row = ws.rows[(A, SCREEN_A)]
    assert (row["outcome"], row["members"]) == ("too_broad", None)
    assert row["companies"] == MAX_MEMBERS + 1


def test_catalog_open_no_id_list_reaches_sql():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A, lines=[{**LINE, "company_id": NVDA}])
    store.members = {A: [NVDA, MSFT]}
    job(store, ws, Auth(public=frozenset({"financials"})))
    screens = [p for _, p in store.calls if "catalog" not in p]
    assert screens and all(p.get("granted") is None for p in screens)
    assert all(len(p.get("ids") or []) < 100 for p in screens)


def test_lines_cross_with_readings_respected_for_a_named_or_any_company():
    ws, store = FakeWatches(), ScreenStore()
    any_line = {**LINE, "label": "Op margin"}
    named = {"column": "gross_margin_ttm", "label": "Gross margin", "direction": "below",
             "threshold": "0.6", "company_id": TSLA}  # TSLA isn't in the screen
    watched(ws, A, SCREEN_A, lines=[any_line, named], entering=False, leaving=False)
    store.members = {A: [NVDA, MSFT, AAPL]}
    store.values = {NVDA: {"operating_margin_ttm": (Decimal("0.2"), None)},
                    MSFT: {"operating_margin_ttm": (Decimal("0.24"), None)},
                    AAPL: {"operating_margin_ttm": (Decimal("0.1"), None)},
                    TSLA: {"gross_margin_ttm": (Decimal("0.7"), None)}}
    auth = Auth({A: [NVDA, MSFT, AAPL, TSLA]})
    job(store, ws, auth)
    store.values = {NVDA: {"operating_margin_ttm": (Decimal("0.3"), "behind")},   # crossed
                    MSFT: {"operating_margin_ttm": (Decimal("0.9"), "not_meaningful")},  # n/m
                    AAPL: {"operating_margin_ttm": (Decimal("0.4"), "old")},  # stale
                    TSLA: {"gross_margin_ttm": (Decimal("0.5"), None)}}       # crossed
    store.landed = NOW + timedelta(hours=20)
    assert job(store, ws, auth, now=NOW + timedelta(hours=21)).alerts == 1
    crossed = ws.made[-1]["detail"]["crossed"]
    assert [(x["label"], [c["company_id"] for c in x["companies"]]) for x in crossed] == [
        ("Op margin", [NVDA]), ("Gross margin", [TSLA])]
    assert crossed[0]["companies"][0]["value"] == "0.3"


def test_a_metric_the_screen_reads_gone_fails_the_check_and_keeps_what_it_saw():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A, state={"metric": [METRIC_A]})
    report = job(store, ws, Auth({A: [NVDA]}))
    assert report.failed == 1 and ws.rows[(A, SCREEN_A)]["outcome"] == "failed"
    assert ws.rows[(A, SCREEN_A)]["members"] is None


def test_openfga_down_skips_the_user_fail_closed():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    report = job(store, ws, Auth(down=True))
    assert report.errors == 1 and report.checked == 0
    assert not [c for c in store.calls if "catalog" not in c[1]]


def test_out_of_time_a_watch_waits_for_the_next_run():
    ws, store = FakeWatches(), ScreenStore()
    watched(ws, A, SCREEN_A)
    report = run_watches(watches=ws, store=store, access=Auth({A: [NVDA]}).access,
                         item=lambda u, i: None, datasets=None, users=[A], now=NOW,
                         app_url=APP, make_until=-1000.0, monotonic=lambda: 0.0)
    assert (report.checked, report.deferred) == (0, 1)


# --- delivery: through alert_job.deliver and the injected Sender (HttpsSender in the Job) -------

class DueAlerts:
    """AppAlerts' due/record/webhook_result for one screen alert delivery."""

    def __init__(self, detail):
        self.detail, self.recorded = detail, []

    def due(self, user_id, at, limit):
        return [{"delivery_id": "d1", "attempts": 0,
                 "webhook_id": "11111111-0000-4000-8000-000000000001",
                 "url": "https://hooks.example.com/x", "format": "json",
                 "secret_salt": b"s" * 16, "stalls": 0, "notification_id": None,
                 "company_id": None, "screen_alert_id": "s1", "screen_item_id": SCREEN_A,
                 "name": "Cheap growers", "screen_detail": self.detail}]

    def record(self, user_id, results, at):
        self.recorded += results

    def webhook_result(self, *a):
        pass


class Recording:
    def __init__(self):
        self.sent = []

    def send(self, url, body, headers):
        self.sent.append((url, json.loads(body), headers))
        return Sent(True, 204)


def test_a_screen_alert_goes_out_as_event_screen_without_companies_now_unreadable():
    detail = {"entered": {"companies": [NVDA, TSLA], "more": 0}}
    alerts, sender = DueAlerts(detail), Recording()
    ok, failed, stalled = deliver(alerts, ScreenStore(), sender, A, jwt_secret=SECRET,  # type: ignore[arg-type]
                                  app_url=APP, at=NOW, can=lambda u, t, c: c == NVDA)
    assert (ok, failed, stalled) == (1, 0, 0)
    _, body, headers = sender.sent[0]
    assert headers["X-Tributary-Event"] == "screen"
    assert body["type"] == "screen" and body["screen"]["item_id"] == SCREEN_A
    assert [c["company_id"] for c in body["entered"]["companies"]] == [NVDA]
    assert body["hidden"] == 1
    assert body["entered"]["companies"][0]["url"] == f"{APP}/#/company/{NVDA}"
    assert body["links"] == {"Open the screen": f"{APP}/#/screen/{SCREEN_A}"}


def test_a_new_alert_makes_a_delivery_for_each_enabled_webhook():
    ws, store = FakeWatches(), ScreenStore()
    ws.webhooks[A] = ["w1", "w2"]
    watched(ws, A, SCREEN_A)
    store.members = {A: [NVDA]}
    auth = Auth({A: [NVDA, MSFT]})
    job(store, ws, auth)
    store.members = {A: [NVDA, MSFT]}
    store.landed = NOW + timedelta(hours=20)
    job(store, ws, auth, now=NOW + timedelta(hours=21))
    assert [d["webhook_id"] for d in ws.deliveries] == ["w1", "w2"]


# --- the routes, two users --------------------------------------------------------------------

def api(ws, grants=None):
    grants = grants if grants is not None else {A: {NVDA: {"financials"}},
                                                B: {NVDA: {"financials"}, TSLA: {"financials"}}}
    return TestClient(create_app(FakeAuthorizer(grants), FakeStore(), SECRET,  # type: ignore[arg-type]
                                 watches=ws))  # type: ignore[arg-type]


def body(**kw):
    return {"entering": True, "leaving": True, "lines": [], **kw}


def test_one_user_cant_watch_read_or_stop_anothers_screen(token):
    ws = FakeWatches()
    ws.add_item(A, SCREEN_A, state={"growth": 10})
    ws.add_item(B, SCREEN_B)
    c = api(ws)
    assert c.put(f"/v1/watches/screen/{SCREEN_A}", json=body(), headers=token(A)).status_code == 200
    # B: A's screen and one that doesn't exist answer the same
    for item in (SCREEN_A, "cccccccc-0000-4000-8000-000000000003"):
        r = c.put(f"/v1/watches/screen/{item}", json=body(), headers=token(B))
        assert (r.status_code, r.json()) == (404, {"detail": "no such saved screen"})
        r = c.delete(f"/v1/watches/screen/{item}", headers=token(B))
        assert (r.status_code, r.json()) == (404, {"detail": "no such watch"})
    assert c.get("/v1/watches", headers=token(B)).json()["watches"] == []
    mine = c.get("/v1/watches", headers=token(A)).json()["watches"]
    assert [w["item_id"] for w in mine] == [SCREEN_A]
    assert (A, SCREEN_A) in ws.rows


def test_a_user_sees_only_their_own_screen_alerts_and_only_what_they_read(token):
    ws = FakeWatches()
    ws.made = [{"alert_id": "a1", "user_id": A, "watch_id": "w", "item_id": SCREEN_A,
                "name": "Mine", "detail": {"entered": {"companies": [NVDA, TSLA], "more": 0}},
                "created_at": NOW, "read_at": None}]
    c = api(ws)
    assert c.get("/v1/alerts/screens", headers=token(B)).json()["alerts"] == []
    got = c.get("/v1/alerts/screens", headers=token(A)).json()["alerts"]
    assert got[0]["entered"] == {"companies": [NVDA], "more": 0} and got[0]["hidden"] == 1


def test_a_watch_is_checked_before_saving(token):
    ws = FakeWatches()
    ws.add_item(A, SCREEN_A, state={"metric": ["dddddddd-0000-4000-8000-000000000004"]})
    ws.add_item(A, "eeeeeeee-0000-4000-8000-000000000005", kind="chart")
    c = api(ws)
    url = f"/v1/watches/screen/{SCREEN_A}"
    assert c.put(url, json=body(entering=False, leaving=False),
                 headers=token(A)).status_code == 422
    assert c.put(url, json=body(columns=["not_a_column"]), headers=token(A)).status_code == 422
    line = {"column": "m_" + "f" * 32, "direction": "above", "threshold": "1"}
    assert c.put(url, json=body(lines=[line]), headers=token(A)).status_code == 422
    own = {**line, "column": "m_" + "dddddddd-0000-4000-8000-000000000004".replace("-", "")}
    r = c.put(url, json=body(lines=[own, {"column": "net_margin_ttm", "direction": "below",
                                          "threshold": "0.05", "company_id": NVDA,
                                          "label": " Net margin "}]),
              headers=token(A))
    assert r.status_code == 200, r.text
    lines = r.json()["watch"]["lines"]
    assert [x["label"] for x in lines] == ["Your metric", "Net margin"]
    assert c.put(url, json=body(lines=[{**own, "threshold": "nan"}]),
                 headers=token(A)).status_code == 422
    assert c.put(url, json=body(lines=[{**own, "label": "bad\x07"}]),
                 headers=token(A)).status_code == 422
    # a chart isn't a screen
    assert c.put("/v1/watches/screen/eeeeeeee-0000-4000-8000-000000000005", json=body(),
                 headers=token(A)).status_code == 404


def test_at_most_max_watches(token):
    ws = FakeWatches()
    c = api(ws)
    for n in range(MAX_WATCHES + 1):
        item = f"aaaaaaaa-0000-4000-8000-{n:012d}"
        ws.add_item(A, item)
        r = c.put(f"/v1/watches/screen/{item}", json=body(), headers=token(A))
        assert r.status_code == (200 if n < MAX_WATCHES else 409), r.text


def test_without_the_app_database_the_routes_answer_503(token):
    c = TestClient(create_app(FakeAuthorizer({}), FakeStore(), SECRET))  # type: ignore[arg-type]
    assert c.get("/v1/watches", headers=token(A)).status_code == 503
    assert c.get("/v1/alerts/screens", headers=token(A)).status_code == 503


def test_an_alert_list_with_the_catalog_open_reads_the_catalog_once(token):
    """readable() with the catalog public: the cached catalog ids, no id list into SQL."""
    ws = FakeWatches()
    ws.made = [{"alert_id": "a1", "user_id": A, "watch_id": "w", "item_id": SCREEN_A,
                "name": "Mine", "detail": {"left": {"companies": [NVDA], "more": 0}},
                "created_at": NOW, "read_at": None}]

    class Open(FakeAuthorizer):
        def access(self, user_id):
            return Access(frozenset({"financials"}), {})

    class Store(FakeStore):
        def catalog_companies(self, user_id):
            return frozenset({NVDA})

    c = TestClient(create_app(Open({}), Store(), SECRET, watches=ws))  # type: ignore[arg-type]
    got = c.get("/v1/alerts/screens", headers=token(A)).json()["alerts"]
    assert got[0]["left"]["companies"] == [NVDA]


def test_readable_from_is_what_routes_use():
    r = watch_job.readable_from(Access(frozenset({"financials"}), {TSLA: {"filings"}}),
                                lambda: frozenset({NVDA}))
    assert isinstance(r, Readable) and r.ids("financials") is None
    assert r[NVDA] == {"financials"} and r[TSLA] == {"filings"}  # not in the catalog
