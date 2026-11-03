"""Thesis checks (thesis_checks.py, app migration 010): the routes with an in-memory store
that keeps AppThesisChecks' rules (test_thesis_checks_db.py runs the real one). Adversarial
cases: another user's thesis, an item that isn't a thesis, a result for another thesis, verdicts
without citations, a claimed `ok`, oversized bodies, a user id in the body."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.thesis_checks import MAX_CHECK_BODY, MAX_CHECKS, counts, validate_check
from query_api.workspaces import StateError

from .conftest import SECRET
from .test_api import NVDA, FakeAuthorizer
from .test_workspaces import THESIS, FakeWorkspaces, MetaStore, new_item, new_workspace

ACC = "0001045810-26-000021"
HASH = "a" * 64


def result(item_id: str, **over) -> dict:
    """A check as agent-api's thesis_check.assemble writes it."""
    cite = {"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27", "company_id": NVDA,
            "url": "https://www.sec.gov/Archives/edgar/data/1045810/x.htm", "section_key": "I-2",
            "figure": "$46.7B", "figure_status": "verified",
            "as_of": "2026-11-02T08:00:00+00:00", "verified": True}
    base = {
        "version": 1,
        "thesis": {"item_id": item_id, "title": "NVDA thesis", "company_id": NVDA,
                   "text_hash": HASH, "characters": 120, "characters_checked": 120,
                   "truncated": False},
        "summary": "Mostly supported.",
        "claims": [
            {"quote": "Revenue was $46.7B.", "quote_found": True, "status": "supported",
             "explanation": "The 10-Q says $46.7B.", "citations": [cite],
             "figures": [{"text": "$46.7B", "status": "verified"}]},
            {"quote": "It's cheap.", "quote_found": True, "status": "unverifiable",
             "explanation": "An opinion.", "citations": [], "figures": []}],
        "assumptions": [{
            "ref": "model-1", "item_id": str(uuid.uuid4()), "name": "Base case",
            "company_id": NVDA, "base_fy": 2025,
            "stats": {"cagr": 0.1, "cagr_years": 5, "avg_margin": 0.16, "margin_years": 5,
                      "last_growth": 0.1, "last_margin": 0.1},
            "margin_min": 0.1, "margin_max": 0.2,
            "years": [{"fy": 2026, "growth": 25.0, "margin": 30.0, "growth_vs_cagr": 15.0,
                       "growth_flag": "above", "margin_flag": "above",
                       "commentary": "Well above history.", "citations": [], "figures": []}]}],
        "not_mentioned": [{"point": "Supply constraints.", "citations": [cite], "figures": []}],
        "notes": [],
        "citations": [{"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27",
                       "company_id": NVDA, "sections": ["I-2"]}],
        "verification": {"figures": [{"text": "$46.7B", "status": "verified"}],
                         "unverified_accessions": [], "used_data": True, "freshness": True},
        "as_of": "2026-11-02T08:00:00+00:00",
        "checked_at": "2026-11-02T09:00:00+00:00",
        "model": "claude-haiku-5-5", "questions": 4, "stop": "end_turn", "rounds": 5,
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0},
        "cost_usd": 0.0012,
    }
    return {**base, **over}


class FakeChecks:
    """AppThesisChecks' rules in memory: only the owner's theses take checks, newest
    MAX_CHECKS kept, someone else's thesis is None (a 404)."""

    def __init__(self, workspaces: FakeWorkspaces):
        self.ws = workspaces
        self.rows: list[dict] = []
        self.tick = 0

    def _thesis(self, user_id, item_id):
        i = self.ws.items.get(item_id)
        return i is not None and i["user_id"] == user_id and i["kind"] == "thesis"

    def add(self, user_id, item_id, res):
        if not self._thesis(user_id, item_id):
            return None
        self.tick += 1
        row = {"check_id": str(uuid.uuid4()), "item_id": item_id, "user_id": user_id,
               "text_hash": res["thesis"]["text_hash"], "model": res["model"],
               "cost_usd": res["cost_usd"], "counts": counts(res), "result": res,
               "created_at": datetime(2026, 11, 2, tzinfo=UTC) + timedelta(seconds=self.tick),
               # SUMMARY's Revisit columns
               "company_id": res["thesis"]["company_id"], "checked_at": res["checked_at"],
               "filings": res.get("filings") or []}
        self.rows.append(row)
        mine = sorted((r for r in self.rows if r["item_id"] == item_id),
                      key=lambda r: r["created_at"], reverse=True)
        for old in mine[MAX_CHECKS:]:
            self.rows.remove(old)
        return {k: v for k, v in row.items() if k not in ("result", "user_id")}

    def list(self, user_id, item_id):
        if not self._thesis(user_id, item_id):
            return None
        return [{k: v for k, v in r.items() if k not in ("result", "user_id")}
                for r in sorted(self.rows, key=lambda r: r["created_at"], reverse=True)
                if r["item_id"] == item_id and r["user_id"] == user_id]

    def get(self, user_id, item_id, check_id):
        return next(({k: v for k, v in r.items() if k != "user_id"} for r in self.rows
                     if (r["check_id"], r["item_id"], r["user_id"])
                     == (check_id, item_id, user_id)), None)


@pytest.fixture
def world(token):
    ws = FakeWorkspaces()
    checks = FakeChecks(ws)
    client = TestClient(create_app(FakeAuthorizer({}), MetaStore(), SECRET, workspaces=ws,
                                   thesis_checks=checks))
    wid = new_workspace(client, token)
    thesis = new_item(client, token, wid, kind="thesis", state=THESIS, name="NVDA thesis")
    return client, checks, thesis["item_id"], wid


def post(client, token, item_id, body, user="alice"):
    return client.post(f"/v1/thesis-checks/{item_id}", json=body, headers=token(user))


def test_store_list_and_read_checks(world, token):
    client, checks, tid, _ = world
    r = post(client, token, tid, {"result": result(tid)})
    assert r.status_code == 201, r.text
    check = r.json()
    assert check["text_hash"] == HASH and check["counts"] == {
        "supported": 1, "contradicted": 0, "unverifiable": 1, "flagged_assumptions": 1,
        "not_mentioned": 1, "ok": True}
    listed = client.get(f"/v1/workspace-items/{tid}/checks", headers=token("alice")).json()
    assert [c["check_id"] for c in listed["checks"]] == [check["check_id"]]
    assert "result" not in listed["checks"][0]
    one = client.get(f"/v1/workspace-items/{tid}/checks/{check['check_id']}",
                     headers=token("alice"))
    assert one.status_code == 200 and one.json()["result"]["claims"][0]["status"] == "supported"


def test_another_users_thesis_is_not_found(world, token):
    client, checks, tid, _ = world
    check = post(client, token, tid, {"result": result(tid)}).json()
    assert post(client, token, tid, {"result": result(tid)}, user="bob").status_code == 404
    missing = str(uuid.uuid4())
    a = client.get(f"/v1/workspace-items/{tid}/checks", headers=token("bob"))
    b = client.get(f"/v1/workspace-items/{missing}/checks", headers=token("bob"))
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    r = client.get(f"/v1/workspace-items/{tid}/checks/{check['check_id']}",
                   headers=token("bob"))
    assert r.status_code == 404
    assert len(checks.rows) == 1


def test_only_theses_take_checks(world, token):
    client, _, _, wid = world
    chart = new_item(client, token, wid)  # a chart
    r = post(client, token, chart["item_id"], {"result": result(chart["item_id"])})
    assert r.status_code == 404
    assert client.get(f"/v1/workspace-items/{chart['item_id']}/checks",
                      headers=token("alice")).status_code == 404


def test_a_result_for_another_thesis_is_refused(world, token):
    client, _, tid, _ = world
    r = post(client, token, tid, {"result": result(str(uuid.uuid4()))})
    assert r.status_code == 422 and "another thesis" in r.json()["detail"]


@pytest.mark.parametrize("change", [
    # a verdict without a citation of a filing the tools returned
    lambda r: r["claims"][0].update(citations=[]),
    lambda r: r["claims"][0]["citations"][0].update(verified=False),
    lambda r: r["not_mentioned"][0].update(citations=[]),
    lambda r: r["claims"][0].update(status="proven"),
    lambda r: r["claims"][0].update(quote=""),
    lambda r: r["claims"][0]["citations"][0].update(url="https://evil.example/x"),
    lambda r: r["claims"][0]["citations"][0].update(accession_no="../../x"),
    lambda r: r["thesis"].update(text_hash="not-a-hash"),
    lambda r: r.update(user_id="bob"),
    lambda r: r.update(claims=[r["claims"][1]] * 31),
    lambda r: r.update(summary="x" * 3001),
])
def test_invalid_results_are_refused(world, token, change):
    client, checks, tid, _ = world
    bad = copy.deepcopy(result(tid))
    change(bad)
    r = post(client, token, tid, {"result": bad})
    assert r.status_code == 422, r.text
    assert checks.rows == []


LATEST = {"accession_no": "0001045810-26-000030", "form": "10-Q", "filed": "2026-08-27"}
EVIDENCE = {"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27"}


def test_revisit_filings_are_stored_and_listed(world, token):
    """Revisit: the newest 10-K/10-Q at check time and the newest cited filing, per company;
    the list carries them (and the thesis's company, checked_at) so the web app needs no
    result to offer a re-check."""
    client, _, tid, _ = world
    basis = [{"company_id": NVDA, "latest": LATEST, "evidence": EVIDENCE},
             {"company_id": "0000002488", "latest": None, "evidence": None}]
    r = post(client, token, tid, {"result": result(tid, filings=basis)})
    assert r.status_code == 201, r.text
    listed = client.get(f"/v1/workspace-items/{tid}/checks", headers=token("alice")).json()
    one = listed["checks"][0]
    assert one["filings"] == basis and one["company_id"] == NVDA
    assert one["checked_at"] == "2026-11-02T09:00:00+00:00"


def test_results_without_filings_still_validate():
    """Checks stored before Revisit have no `filings`: still valid, read as []."""
    old = result(str(uuid.uuid4()))
    assert "filings" not in old
    assert validate_check(old)["filings"] == []


@pytest.mark.parametrize("filings", [
    # evidence must be a verified citation of this check, for that company
    [{"company_id": NVDA, "evidence": {**EVIDENCE, "accession_no": "0001045810-26-000099"}}],
    [{"company_id": "0000002488", "evidence": EVIDENCE}],
    # latest is a 10-K or 10-Q
    [{"company_id": NVDA, "latest": {**LATEST, "form": "8-K"}}],
    [{"company_id": NVDA, "latest": {**LATEST, "filed": "yesterday"}}],
    [{"company_id": NVDA}, {"company_id": NVDA}],
    [{"company_id": "nvda"}],
    [{"company_id": NVDA, "extra": 1}],
    [{"company_id": f"{i:010d}"} for i in range(13)],
])
def test_invalid_filings_are_refused(filings):
    with pytest.raises(StateError):
        validate_check(result(str(uuid.uuid4()), filings=filings))


def test_numbers_are_finite():
    bad = result(str(uuid.uuid4()))
    bad["assumptions"][0]["years"][0]["growth"] = float("nan")
    with pytest.raises(StateError):
        validate_check(bad)


def test_ok_is_recomputed_never_taken(world, token):
    client, checks, tid, _ = world
    bad = result(tid, verification={"ok": True, "figures": [{"text": "$9B",
                                                               "status": "unverified"}],
                                    "unverified_accessions": [], "used_data": True,
                                    "freshness": True})
    assert post(client, token, tid, {"result": bad}).status_code == 201
    assert checks.rows[0]["result"]["verification"]["ok"] is False
    assert checks.rows[0]["counts"]["ok"] is False


def test_body_is_strict_and_capped(world, token):
    client, checks, tid, _ = world
    assert post(client, token, tid, {"result": result(tid), "user_id": "bob"}).status_code == 422
    big = json.dumps({"result": result(tid, summary="x" * MAX_CHECK_BODY)})
    r = client.post(f"/v1/thesis-checks/{tid}", content=big,
                    headers={**token("alice"), "Content-Type": "application/json"})
    assert r.status_code == 413
    assert client.post(f"/v1/thesis-checks/{tid}", json={"result": result(tid)}).status_code \
        == 401
    assert client.post("/v1/thesis-checks/not-a-uuid", json={"result": result(tid)},
                       headers=token("alice")).status_code == 422
    assert checks.rows == []


def test_the_newest_checks_are_kept(world, token):
    client, checks, tid, _ = world
    for _ in range(MAX_CHECKS + 2):
        assert post(client, token, tid, {"result": result(tid)}).status_code == 201
    listed = client.get(f"/v1/workspace-items/{tid}/checks", headers=token("alice")).json()
    assert len(listed["checks"]) == MAX_CHECKS


def test_without_the_app_database_the_routes_answer_503(token):
    client = TestClient(create_app(FakeAuthorizer({}), MetaStore(), SECRET))
    tid = str(uuid.uuid4())
    assert client.get(f"/v1/workspace-items/{tid}/checks", headers=token("alice")).status_code \
        == 503


def test_nginx_keeps_the_write_route_off_the_public_site():
    """Only agent-api stores checks; browsers can read them, never write one."""
    import re
    from pathlib import Path

    conf = (Path(__file__).parents[2] / "web/deploy/nginx.conf").read_text()
    blocked = [re.compile(m) for m in re.findall(r"location ~ (\S+) \{\s*return 404;", conf)]
    for path in ("/api/query/v1/thesis-checks/abc", "/api/query//v1//thesis-checks/abc"):
        assert any(b.search(path) for b in blocked), path
    for path in ("/api/query/v1/workspace-items/abc/checks",
                 "/api/query/v1/workspace-items/abc/checks/def"):
        assert not any(b.search(path) for b in blocked), path
