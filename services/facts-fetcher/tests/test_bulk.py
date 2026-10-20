"""The companyfacts.zip sweep: download, reading entries, and resumable runs. Zips are built
from the trimmed real payloads in fixtures/. DB tests are skipped unless TEST_DATABASE_URL is
set; every test is rolled back."""

import json
import os
import zipfile
from datetime import date
from email.utils import formatdate
from pathlib import Path

import httpx
import pytest

from facts_fetcher.bulk import (
    BULK_URL,
    RunStats,
    Unchanged,
    checkpoint,
    download,
    entry_names,
    read_entry,
    run,
    run_rounds,
)
from facts_fetcher.payload import Payload, digest, project

FIXTURES = Path(__file__).parent / "fixtures"
NVDA, COST, JPM, EMPTY = "0001045810", "0000909832", "0000019617", "0000002110"
XOM, XOM_HOLDINGS = "0000034088", "0002115436"
TAGS = {("us-gaap", "Revenues"), ("us-gaap", "Assets")}
DSN = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def raw(cik: str) -> bytes:
    return (FIXTURES / f"CIK{cik}.json").read_bytes()


def make_zip(path: Path, payloads: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "not an entry")
        for cik, data in payloads.items():
            z.writestr(f"CIK{cik}.json", data)
    return path


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_streams_to_disk_and_reports_last_modified(tmp_path):
    body = b"PK" + b"x" * 5000
    stamp = formatdate(usegmt=True)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=body, headers={"Last-Modified": stamp})

    got = download(client(handler), tmp_path / "companyfacts.zip", None)
    assert got is not None
    assert got.path.read_bytes() == body and got.size_bytes == len(body)
    assert got.last_modified == stamp
    assert str(seen[0].url) == BULK_URL and "If-Modified-Since" not in seen[0].headers
    assert not (tmp_path / "companyfacts.zip.part").exists()


def test_download_skips_an_unchanged_file(tmp_path):
    def handler(req):
        assert req.headers["If-Modified-Since"] == "Mon, 19 Oct 2026 06:00:00 GMT"
        return httpx.Response(304)

    assert download(client(handler), tmp_path / "f.zip", "Mon, 19 Oct 2026 06:00:00 GMT") is None
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_a_truncated_body(tmp_path):
    def handler(req):
        return httpx.Response(200, content=b"short", headers={"Content-Length": "100"})

    with pytest.raises((OSError, httpx.HTTPError)):
        download(client(handler), tmp_path / "f.zip", None)
    assert not (tmp_path / "f.zip").exists()


def test_download_raises_on_error(tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        download(client(lambda req: httpx.Response(403)), tmp_path / "f.zip", None)


def test_project_keeps_only_mapped_tags():
    doc = json.loads(raw(NVDA))
    kept = project(doc, TAGS)
    assert set(kept["facts"]["us-gaap"]) == {"Revenues", "Assets"}
    assert kept["facts"]["us-gaap"]["Revenues"] == doc["facts"]["us-gaap"]["Revenues"]
    assert kept["entityName"] == "NVIDIA CORP"
    assert project(json.loads(raw(EMPTY)), TAGS)["facts"] == {}
    assert project({}, TAGS) == {"cik": None, "entityName": None, "facts": {}}


def test_entry_names_are_sorted_ciks_after_the_cursor(tmp_path):
    path = make_zip(tmp_path / "c.zip", {NVDA: raw(NVDA), EMPTY: raw(EMPTY), JPM: raw(JPM)})
    with zipfile.ZipFile(path) as zf:
        assert entry_names(zf) == [f"CIK{c}.json" for c in (EMPTY, JPM, NVDA)]
        assert entry_names(zf, f"CIK{JPM}.json") == [f"CIK{NVDA}.json"]


def test_read_entry_skips_known_payloads(tmp_path):
    path = make_zip(tmp_path / "c.zip", {NVDA: raw(NVDA), JPM: raw(JPM)})
    known = {NVDA: digest(raw(NVDA)), JPM: "stale"}
    with zipfile.ZipFile(path) as zf:
        assert read_entry(zf, f"CIK{NVDA}.json", known, TAGS) == Unchanged(NVDA, known[NVDA])
        jpm = read_entry(zf, f"CIK{JPM}.json", known, TAGS)
    assert isinstance(jpm, Payload)
    assert (jpm.cik, jpm.sha256, jpm.size_bytes) == (JPM, digest(raw(JPM)), len(raw(JPM)))


# --- runs against Postgres ---

@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE gold.quarterly_financials, silver.financial_facts,"
                  " bronze.company_facts, bronze.bulk_loads, silver.company_aliases,"
                  " silver.company_ciks, silver.companies")
        for company_id, ciks in {NVDA: [NVDA], JPM: [JPM], XOM: [XOM, XOM_HOLDINGS]}.items():
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                      " VALUES (%s, %s, %s)", (company_id, company_id, ciks[-1]))
            for cik in ciks:
                c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                          (cik, company_id))
        yield c


ALL = (EMPTY, JPM, XOM, COST, NVDA, XOM_HOLDINGS)


@pytest.fixture
def bulk_zip(tmp_path):
    return make_zip(tmp_path / "companyfacts.zip", {c: raw(c) for c in ALL})


def counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM bronze.company_facts),"
        " (SELECT count(*) FROM silver.financial_facts),"
        " (SELECT count(*) FROM gold.quarterly_financials)").fetchone()


@needs_db
def test_full_run_loads_every_filer(conn, bulk_zip):
    stats = run(conn, bulk_zip, "test", "v1")
    assert (stats.entries, stats.changed, stats.completed) == (6, 6, True)
    bronze, silver, gold = counts(conn)
    assert bronze == 6 and silver > 0 and gold > 0
    # Costco has facts in silver but no company: no gold rows, no error
    assert conn.execute("SELECT count(*) FROM silver.financial_facts WHERE cik = %s",
                        (COST,)).fetchone()[0] > 0
    assert conn.execute("SELECT DISTINCT company_id FROM gold.quarterly_financials"
                        " ORDER BY 1").fetchall() == [(JPM,), (XOM,), (NVDA,)]
    assert checkpoint(conn, "test").completed


@needs_db
def test_rerun_touches_nothing_and_converges(conn, bulk_zip):
    run(conn, bulk_zip, "test", "v1")
    before = counts(conn)
    stats = run(conn, bulk_zip, "test", "v1")
    assert (stats.entries, stats.changed, stats.facts, stats.gold_rows) == (6, 0, 0, 0)
    assert counts(conn) == before


@needs_db
def test_limit_splits_a_load_into_resumable_runs(conn, bulk_zip):
    first = run(conn, bulk_zip, "test", "v1", limit=2, batch_size=1)
    assert (first.changed, first.completed) == (2, False)
    assert checkpoint(conn, "test").cursor == f"CIK{JPM}.json"
    second = run(conn, bulk_zip, "test", "v1", limit=2, batch_size=1)
    assert second.entries == 2 and not second.completed  # XOM, COST
    third = run(conn, bulk_zip, "test", "v1", limit=2, batch_size=1)
    assert (third.entries, third.completed) == (2, True)
    stepwise = counts(conn)

    conn.execute("TRUNCATE gold.quarterly_financials, silver.financial_facts,"
                 " bronze.company_facts, bronze.bulk_loads")
    run(conn, bulk_zip, "test", "v1")
    assert counts(conn) == stepwise


@needs_db
def test_a_crash_keeps_committed_batches_and_the_rerun_resumes(conn, tmp_path, bulk_zip):
    broken = make_zip(tmp_path / "broken.zip",
                      {**{c: raw(c) for c in ALL}, COST: b"{not json"})
    with pytest.raises(json.JSONDecodeError):
        run(conn, broken, "test", "v1", batch_size=1)
    assert checkpoint(conn, "test").cursor == f"CIK{XOM}.json"  # committed before COST
    assert counts(conn)[0] == 3

    stats = run(conn, bulk_zip, "test", "v1")
    assert stats.entries == 3 and stats.completed  # resumed after XOM
    resumed = counts(conn)
    conn.execute("TRUNCATE gold.quarterly_financials, silver.financial_facts,"
                 " bronze.company_facts, bronze.bulk_loads")
    run(conn, bulk_zip, "test", "v1")
    assert counts(conn) == resumed


@needs_db
def test_a_new_version_reads_everything_but_only_refreshes_changes(conn, tmp_path, bulk_zip):
    run(conn, bulk_zip, "test", "v1")
    nvda = json.loads(raw(NVDA))
    revenues = nvda["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    latest_quarter = max((r for r in revenues if "start" in r and
                          (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
                          < 120), key=lambda r: r["end"])
    revenues.append({**latest_quarter, "val": 1, "accn": "new-filing", "filed": "2026-10-01"})
    v2 = make_zip(tmp_path / "v2.zip", {**{c: raw(c) for c in ALL},
                                        NVDA: json.dumps(nvda).encode()})
    stats = run(conn, v2, "test", "v2")
    assert (stats.entries, stats.changed, stats.facts) == (6, 1, 1)
    assert conn.execute("SELECT value, accession_no FROM gold.quarterly_financials"
                        " WHERE company_id = %s AND metric = 'revenue'"
                        " ORDER BY period_end DESC LIMIT 1", (NVDA,)).fetchone() \
        == (1, "new-filing")


@needs_db
def test_a_cik_mapped_later_gets_gold_rows_on_the_next_complete_run(conn, bulk_zip):
    run(conn, bulk_zip, "test", "v1")
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                 " VALUES (%s, 'COSTCO', %s)", (COST, COST))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                 (COST, COST))
    stats = run(conn, bulk_zip, "test", "v1")
    assert stats.changed == 0 and stats.gold_rows > 0
    assert conn.execute("SELECT count(*) FROM gold.quarterly_financials"
                        " WHERE company_id = %s", (COST,)).fetchone()[0] > 0


def test_rounds_pause_between_runs_and_stop_once_complete():
    results = iter([RunStats(changed=500), RunStats(changed=500), RunStats(changed=7,
                                                                           completed=True)])
    paused = []
    stats = run_rounds(lambda: next(results), rounds=5, pause_seconds=600, sleep=paused.append)
    assert stats.completed and stats.changed == 7
    assert paused == [600, 600]


def test_rounds_stop_at_the_limit_without_a_trailing_pause():
    paused = []
    stats = run_rounds(lambda: RunStats(changed=500), rounds=2, pause_seconds=60,
                       sleep=paused.append)
    assert not stats.completed
    assert paused == [60]
