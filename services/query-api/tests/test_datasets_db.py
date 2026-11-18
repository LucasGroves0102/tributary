"""Your data (B2c, B2d) against real Postgres: cross-user adversarial cases first (two users; no
read, write, list, formula, screener or SQL path reaches the other's dataset or metric), then
uploads end to end (resolution, point in time, append versions, quotas), formulas and screener
columns over a user's own data.

The tributary database (TEST_DATABASE_URL) holds the datasets under forced row-level security;
the app database (APP_TEST_DATABASE_URL) the saved metrics. Skipped unless TEST_DATABASE_URL is
set (the metric tests also need APP_TEST_DATABASE_URL); every test is rolled back."""

import io
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api import datasets as datasets_mod
from query_api.app import create_app
from query_api.datasets import AppUserData
from query_api.store import Store
from query_api.workspaces import MAX_METRICS, AppWorkspaces

from .conftest import SECRET
from .test_api_keys import FakeKeys, bearer, make_key
from .test_rls import AllowAll

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
APP_DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

ANN, BEN = "t-data-ann", "t-data-ben"  # two users
SANDBOX = "demo-t-data-sandbox"
CO, OTHER = "9000000001", "9000000002"  # tickers TSTA, TSTB
TODAY = date(2026, 11, 16)

STORES = (b"ticker,date,stores,sales\n"
          b"TSTA,2026-03-31,10,1000\nTSTA,2026-06-30,12,1500\n"
          b"TSTB,2026-06-30,7,700\nNOPE,2026-06-30,1,1\n")


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@dataclass
class World:
    client: TestClient
    conn: object
    app_conn: object | None


def _seed(conn):
    for cid, ticker in ((CO, "TSTA"), (OTHER, "TSTB")):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                     " fiscal_year_end) VALUES (%s, %s, %s, '1231')", (cid, f"Test {ticker}", cid))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
        conn.execute("INSERT INTO silver.company_aliases (company_id, cik, alias_type, alias,"
                     " source) VALUES (%s, %s, 'ticker', %s, 'curated')", (cid, cid, ticker))
        for end, value, filed in (("2026-03-31", 100, "2026-05-01"),
                                  ("2026-06-30", 150, "2026-08-01")):
            acc = f"{cid}-26-{end[5:7]}0000"
            conn.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                " period_end, value, unit, value_source, source_tag, accession_no, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'revenue', %s::date - 90, %s,"
                " %s, 'USD', 'reported', 'us-gaap:Revenues', %s, %s, ARRAY[%s], now())",
                (cid, end, end, value, acc, filed, acc))
    conn.execute("SELECT gold.refresh_screen(%s)", ([CO, OTHER],))
    conn.execute("DELETE FROM authz.public_access")
    conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-data-%%'")
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                 " SELECT u, c, 'financials' FROM unnest(%s::text[]) u, unnest(%s::text[]) c",
                 ([ANN, BEN, SANDBOX], [CO, OTHER]))


@pytest.fixture
def world():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        _seed(conn)
        if APP_DSN:
            with psycopg.connect(APP_DSN) as app_conn, \
                    app_conn.transaction(force_rollback=True):
                owner = app_conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname ="
                                         " 'public' AND tablename = 'workspace_items'"
                                         ).fetchone()[0]
                app_conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
                app_conn.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@x.test'),"
                                 " (%s, 'b@x.test')", (ANN, BEN))
                yield _world(conn, AppWorkspaces(OneConnection(app_conn)), app_conn)
        else:
            yield _world(conn, None, None)


def _world(conn, workspaces, app_conn) -> World:
    app = create_app(AllowAll(), Store(OneConnection(conn)), SECRET,
                     userdata=AppUserData(OneConnection(conn)), workspaces=workspaces,
                     api_keys=FakeKeys(users=(ANN, BEN)), today=lambda: TODAY)
    return World(TestClient(app), conn, app_conn)


def upload(w, token, user=ANN, data=STORES, path="/v1/datasets", headers=None, **fields):
    form = {"name": "stores", **fields}
    return w.client.post(path, files={"file": ("x.csv", io.BytesIO(data), "text/csv")},
                         data=form, headers=headers or token(user))


def stored(w, token, user=ANN, **fields) -> dict:
    r = upload(w, token, user, **fields)
    assert r.status_code == 200, r.text
    return r.json()["dataset"]


def evaluate(w, token, user, formula, **body):
    return w.client.post("/v1/formulas/evaluate", headers=token(user),
                         json={"formula": formula, "default_company": CO, **body})


def as_user(conn, user):
    conn.execute("SET LOCAL ROLE query_api")
    conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))


def reset(conn):
    conn.execute("RESET ROLE")
    conn.execute("SELECT set_config('app.user_id', '', true)")


# --- cross-user: nothing reaches another user's dataset ------------------------------------

def test_another_users_dataset_is_the_same_404_on_every_route(world, token):
    ds = stored(world, token)
    c, mine = world.client, token(ANN)
    theirs, unknown = token(BEN), "00000000-0000-4000-8000-000000000000"
    assert [d["name"] for d in c.get("/v1/datasets", headers=mine).json()["datasets"]] == \
        ["stores"]
    assert c.get("/v1/datasets", headers=theirs).json()["datasets"] == []
    for path in (f"/v1/datasets/{ds['dataset_id']}", f"/v1/datasets/{ds['dataset_id']}/rows"):
        assert c.get(path, headers=mine).status_code == 200
        r, u = c.get(path, headers=theirs), c.get(path.replace(ds["dataset_id"], unknown),
                                                     headers=theirs)
        assert (r.status_code, r.json()) == (u.status_code, u.json()) == (404, r.json())
    assert c.get(f"/v1/companies/{CO}/datasets", headers=theirs).json()["datasets"] == []
    assert c.patch(f"/v1/datasets/{ds['dataset_id']}", json={"title": "mine now"},
                   headers=theirs).status_code == 404
    assert c.delete(f"/v1/datasets/{ds['dataset_id']}", headers=theirs).status_code == 404
    r = upload(world, token, BEN, path=f"/v1/datasets/{ds['dataset_id']}/upload")
    assert r.status_code == 404
    # still Ann's, unchanged
    again = c.get(f"/v1/datasets/{ds['dataset_id']}", headers=mine).json()
    assert again["title"] == "stores" and again["rows"] == 3


def test_a_formula_naming_another_users_dataset_reads_like_a_typo(world, token):
    stored(world, token)
    r = evaluate(world, token, BEN, "mine.stores.sales / revenue")
    assert r.status_code == 422
    err = r.json()["detail"]["errors"][0]
    assert err["code"] == "unknown_dataset" and err["message"] == "You have no dataset 'stores'"
    typo = evaluate(world, token, BEN, "mine.stoers.sales / revenue").json()["detail"]
    assert typo["errors"][0]["code"] == "unknown_dataset"
    assert "stores" not in str(typo)  # no suggestion from another user's names
    v = world.client.post("/v1/formulas/validate", headers=token(BEN),
                          json={"formula": "mine.stores.sales", "default_company": CO}).json()
    assert v["valid"] is False and v["errors"][0]["code"] == "unknown_dataset"
    # Ben's own dataset of the same name is his, never Ann's rows
    stored(world, token, BEN, data=b"ticker,date,sales\nTSTA,2026-06-30,5\n")
    points = evaluate(world, token, BEN, "mine.stores.sales").json()["points"]
    assert [p["value"] for p in points] == ["5"]


def test_row_level_security_alone_hides_and_protects_another_users_rows(world, token):
    ds = stored(world, token)
    conn = world.conn
    for table in ("datasets", "rows", "uploads"):
        forced = conn.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
                              " WHERE oid = %s::regclass", (f"userdata.{table}",)).fetchone()[0]
        assert forced, table
    with conn.transaction(force_rollback=True):
        as_user(conn, BEN)
        for table in ("datasets", "rows", "uploads", "series"):
            assert conn.execute(f"SELECT count(*) FROM userdata.{table}").fetchone()[0] == 0
        assert conn.execute("UPDATE userdata.rows SET vals = '{0,0}'").rowcount == 0
        assert conn.execute("DELETE FROM userdata.datasets").rowcount == 0
        assert conn.execute("SELECT count(*) FROM userdata.field_points(%s, %s, 1)",
                            (ds["dataset_id"], CO)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            # a row for Ann (or one under Ann's dataset) can't be written as Ben
            conn.execute("INSERT INTO userdata.rows (dataset_id, user_id, company_id, day,"
                         " known_at, vals) VALUES (%s, %s, %s, '2026-09-30', '2026-11-16',"
                         " '{1,1}')", (ds["dataset_id"], ANN, CO))
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute("INSERT INTO userdata.rows (dataset_id, user_id, company_id, day,"
                         " known_at, vals) VALUES (%s, %s, %s, '2026-09-30', '2026-11-16',"
                         " '{1,1}')", (ds["dataset_id"], BEN, CO))
        reset(conn)
    with conn.transaction(force_rollback=True):
        conn.execute("SET LOCAL ROLE query_api")  # no user at all: nothing
        assert conn.execute("SELECT count(*) FROM userdata.rows").fetchone()[0] == 0
        reset(conn)
    with conn.transaction(force_rollback=True):
        as_user(conn, ANN)
        assert conn.execute("SELECT count(*) FROM userdata.rows").fetchone()[0] == 3
        reset(conn)


def test_a_pipeline_login_sees_no_users_data(world, token):
    """FORCE: the tables' owner (the pipeline jobs' login) reads nothing without naming the
    user, and writes nothing."""
    stored(world, token)
    conn = world.conn
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'userdata'"
                         " AND tablename = 'rows'").fetchone()[0]
    exempt = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                          (owner,)).fetchone()[0]
    if exempt:
        pytest.skip(f"the tables' owner {owner} bypasses row-level security")
    with conn.transaction(force_rollback=True):
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
        conn.execute("SELECT set_config('app.user_id', '', true)")
        for table in ("datasets", "rows", "uploads"):
            assert conn.execute(f"SELECT count(*) FROM userdata.{table}").fetchone()[0] == 0
        assert conn.execute("DELETE FROM userdata.datasets").rowcount == 0
        reset(conn)


def test_a_dataset_or_field_name_is_never_sql(world, token):
    stored(world, token)
    for formula in ("mine.stores;drop.sales", "mine.\"stores\".sales", "mine.stores.sales'--",
                    "mine.stores.sales) OR (1=1", "mine.pg_class.relname"):
        r = evaluate(world, token, ANN, formula)
        assert r.status_code == 422, formula
    # names reach SQL only as bound ids: the compiled text holds neither
    from query_api.formula import analyze, compile_formula, parse
    from query_api.formula.check import DatasetInfo
    info = DatasetInfo("00000000-0000-4000-8000-000000000001", "stores", ("sales",), ("money",))
    ast = parse("mine.stores.sales")
    compiled = compile_formula(ast, analyze(ast, "quarter", {"stores": info}), {None: CO}, "fiscal")
    assert "stores" not in compiled.sql and "sales" not in compiled.sql
    # an upload's names are simplified to letters, digits and _ before they're stored
    r = upload(world, token, name="x'); DROP TABLE userdata.rows; --",
               data=b"ticker,date,\"a\"\"; DROP\"\nTSTA,2026-06-30,1\n")
    assert r.status_code == 200, r.text
    assert r.json()["dataset"]["name"] == "x_drop_table_userdata_rows"
    assert [f["name"] for f in r.json()["dataset"]["fields"]] == ["a_drop"]


def test_another_users_metric_is_not_a_column(world, token):
    if world.app_conn is None:
        pytest.skip("APP_TEST_DATABASE_URL not set")
    stored(world, token)
    item = save_metric(world, token, ANN, "Sales per store", "mine.stores.sales / revenue")
    ok = world.client.get("/v1/screener", params={"metric": item}, headers=token(ANN))
    assert ok.status_code == 200, ok.text
    theirs = world.client.get("/v1/screener", params={"metric": item}, headers=token(BEN))
    unknown = world.client.get("/v1/screener", headers=token(BEN),
                               params={"metric": "00000000-0000-4000-8000-000000000000"})
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json()
    # Ben's own metric over a dataset name only Ann has: no dataset, never Ann's rows
    his = save_metric(world, token, BEN, "Copy", "mine.stores.sales / revenue")
    r = world.client.get("/v1/screener", params={"metric": his}, headers=token(BEN))
    assert r.status_code == 422 and "You have no dataset 'stores'" in r.json()["detail"]


def test_a_read_key_cant_upload_and_a_write_key_can(world, token):
    ds = stored(world, token)
    read = make_key(world.client, token, user=ANN, scopes=("read",))["key"]
    write = make_key(world.client, token, user=ANN, scopes=("read", "write"))["key"]
    assert world.client.get("/v1/datasets", headers=bearer(read)).status_code == 200
    assert world.client.get(f"/v1/datasets/{ds['dataset_id']}/rows",
                            headers=bearer(read)).status_code == 200
    r = upload(world, token, headers=bearer(read), name="other")
    assert r.status_code == 403
    r = upload(world, token, headers=bearer(read),
               path=f"/v1/datasets/{ds['dataset_id']}/upload")
    assert r.status_code == 403
    assert world.client.patch(f"/v1/datasets/{ds['dataset_id']}", json={"title": "t"},
                              headers=bearer(read)).status_code == 403
    assert upload(world, token, headers=bearer(write), name="other").status_code == 200
    # deleting stays sign-in only, even for a write key
    assert world.client.delete(f"/v1/datasets/{ds['dataset_id']}",
                               headers=bearer(write)).status_code == 403
    # Ben's write key reaches none of Ann's
    bens = make_key(world.client, token, user=BEN, scopes=("read", "write"))["key"]
    assert world.client.get(f"/v1/datasets/{ds['dataset_id']}",
                            headers=bearer(bens)).status_code == 404


def test_a_demo_sandbox_cant_upload_or_see_anyone_elses(world, token):
    stored(world, token)
    r = upload(world, token, SANDBOX)
    assert r.status_code == 403 and r.json()["code"] == "permanent_account"
    listed = world.client.get("/v1/datasets", headers=token(SANDBOX)).json()
    assert listed["datasets"] == [] and listed["uploads_allowed"] is False
    assert world.client.get(f"/v1/companies/{CO}/datasets",
                            headers=token(SANDBOX)).json()["datasets"] == []
    assert evaluate(world, token, SANDBOX, "mine.stores.sales").status_code == 422


# --- uploads ------------------------------------------------------------------------------

def test_an_upload_resolves_tickers_and_lists_the_rest_by_row(world, token):
    preview = upload(world, token, dry_run="true")
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["preview"] is True and p["rows"] == 3 and p["companies"] == 2
    assert p["left_out"] == {"rows": [5], "count": 1, "companies": 1}
    assert "NOPE" not in preview.text  # rows by number, never the cell
    assert world.client.get("/v1/datasets", headers=token(ANN)).json()["datasets"] == []
    r = upload(world, token, units="sales:money")
    body = r.json()
    assert body["stored"] == 3 and body["left_out"]["rows"] == [5]
    ds = body["dataset"]
    assert (ds["rows"], ds["values"], ds["companies"]) == (3, 6, 2)
    assert ds["fields"] == [{"name": "stores", "unit": "number"},
                            {"name": "sales", "unit": "money"}]
    assert ds["known_from"] == "upload" and ds["first_day"] == "2026-03-31"
    rows = world.client.get(f"/v1/datasets/{ds['dataset_id']}/rows", params={"company_id": CO},
                            headers=token(ANN)).json()["rows"]
    assert rows == [
        {"company_id": CO, "day": "2026-06-30", "known_at": "2026-11-16",
         "values": {"stores": "12", "sales": "1500"}},
        {"company_id": CO, "day": "2026-03-31", "known_at": "2026-11-16",
         "values": {"stores": "10", "sales": "1000"}}]


def test_a_bad_file_is_refused_whole_by_row_and_column(world, token):
    r = upload(world, token, data=b"ticker,date,x\nTSTA,2026-06-30,=cmd|' /C calc'!A0\n"
                                  b"TSTA,2026-13-01,1\n")
    assert r.status_code == 422
    body = r.json()
    assert body["count"] == 2
    assert [(e["row"], e["column"], e["code"]) for e in body["errors"]] == [
        (2, 3, "number"), (3, 2, "date")]
    assert "cmd" not in r.text and "calc" not in r.text
    assert world.client.get("/v1/datasets", headers=token(ANN)).json()["datasets"] == []
    dupes = upload(world, token, data=b"ticker,date,x\nTSTA,2026-06-30,1\n9000000001,"
                                      b"2026-06-30,2\n")
    assert dupes.status_code == 422 and dupes.json()["errors"][0]["code"] == "same_row"


def test_point_in_time_a_row_is_never_read_before_it_was_known(world, token):
    stored(world, token)  # known from the upload day, 2026-11-16
    now = evaluate(world, token, ANN, "mine.stores.stores").json()
    assert [p["value"] for p in now["points"]] == ["10", "12"]
    assert now["points"][0]["inputs"][0]["value_source"] == "yours"
    assert now["points"][0]["inputs"][0]["known_at"] == "2026-11-16"
    before = evaluate(world, token, ANN, "mine.stores.stores", as_of="2026-11-15").json()
    assert before["points"] == [] and before["known_on"] == "2026-11-15"
    # a declared publication date: each row from its own day
    stored(world, token, name="ests", published_column="published",
           data=b"ticker,date,published,eps\nTSTA,2026-06-30,2026-05-01,1.0\n"
                b"TSTA,2026-06-30,2026-07-15,1.2\nTSTA,2026-09-30,2026-08-01,1.4\n")
    def at(day=None):
        body = {"as_of": day} if day else {}
        return [(p["period_end"], p["value"])
                for p in evaluate(world, token, ANN, "mine.ests.eps", **body).json()["points"]]
    assert at("2026-04-30") == []
    assert at("2026-05-01") == [("2026-06-30", "1.0")]
    assert at("2026-07-31") == [("2026-06-30", "1.2")]
    assert at() == [("2026-06-30", "1.2"), ("2026-09-30", "1.4")]
    rows = world.client.get("/v1/datasets", headers=token(ANN)).json()["datasets"]
    assert next(d for d in rows if d["name"] == "ests")["known_from"] == "column"
    # a publication date after today is refused
    r = upload(world, token, name="later", published_column="published",
               data=b"ticker,date,published,eps\nTSTA,2026-06-30,2026-11-17,1.0\n")
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "published_later"


def test_append_keeps_versions_and_skips_unchanged_rows(world, token):
    ds = stored(world, token, name="ests", published_column="published",
                data=b"ticker,date,published,eps\nTSTA,2026-06-30,2026-05-01,1.0\n")
    r = upload(world, token, path=f"/v1/datasets/{ds['dataset_id']}/upload", mode="append",
               published_column="published",
               data=b"ticker,date,published,eps\nTSTA,2026-06-30,2026-06-01,1.00\n"
                    b"TSTA,2026-06-30,2026-07-01,1.1\n")
    assert r.status_code == 200, r.text
    assert (r.json()["stored"], r.json()["unchanged"]) == (1, 1)
    versions = world.client.get(f"/v1/datasets/{ds['dataset_id']}/rows",
                                params={"versions": "true"}, headers=token(ANN)).json()["rows"]
    assert [(v["known_at"], v["values"]["eps"]) for v in versions] == [
        ("2026-07-01", "1.1"), ("2026-05-01", "1.0")]
    wrong = upload(world, token, path=f"/v1/datasets/{ds['dataset_id']}/upload", mode="append",
                   published_column="published",
                   data=b"ticker,date,published,other\nTSTA,2026-06-30,2026-06-01,1\n")
    assert wrong.status_code == 422 and wrong.json()["code"] == "fields_differ"
    replaced = upload(world, token, path=f"/v1/datasets/{ds['dataset_id']}/upload",
                      data=b"ticker,date,other\nTSTB,2026-06-30,3\n")
    assert replaced.status_code == 200
    d = replaced.json()["dataset"]
    assert d["fields"] == [{"name": "other", "unit": "number"}] and d["rows"] == 1
    assert d["known_from"] == "upload" and d["name"] == "ests"


def test_quotas_and_the_hourly_limit(world, token, monkeypatch):
    stored(world, token)
    monkeypatch.setattr(datasets_mod, "MAX_DATASETS", 1)
    r = upload(world, token, name="second")
    assert r.status_code == 409 and r.json()["code"] == "limit"
    monkeypatch.setattr(datasets_mod, "MAX_DATASETS", 50)
    monkeypatch.setattr(datasets_mod, "MAX_USER_BYTES", len(STORES) + 10)
    r = upload(world, token, name="second")
    assert r.status_code == 409 and "MB" in r.json()["detail"]
    monkeypatch.setattr(datasets_mod, "MAX_USER_BYTES", 200 * 2**20)
    taken = upload(world, token)
    assert taken.status_code == 409 and taken.json()["code"] == "name_taken"
    monkeypatch.setattr(datasets_mod, "REQUESTS_PER_HOUR", 4)
    r = upload(world, token, name="third", dry_run="true")
    assert r.status_code == 429  # four requests so far this hour (each upload above)
    assert r.json()["code"] == "too_many"
    monkeypatch.setattr(datasets_mod, "REQUESTS_PER_HOUR", 30)
    monkeypatch.setattr(datasets_mod, "UPLOADS_PER_HOUR", 4)
    assert upload(world, token, name="third", dry_run="true").status_code == 200
    assert upload(world, token, name="third").status_code == 429  # four stored this hour
    # Ben's hour is his own
    assert upload(world, token, BEN, dry_run="true").status_code == 200


def test_the_space_for_everyone_refuses_politely(world, token):
    world.client.app  # noqa: B018
    ud = AppUserData(OneConnection(world.conn), space_bytes=1)
    app = create_app(AllowAll(), Store(OneConnection(world.conn)), SECRET, userdata=ud,
                     today=lambda: TODAY)
    r = TestClient(app).post("/v1/datasets", headers=token(ANN), data={"name": "stores"},
                             files={"file": ("x.csv", io.BytesIO(STORES), "text/csv")})
    assert r.status_code == 507 and r.json()["code"] == "full"
    assert str(2**20) not in r.text


def test_an_upload_must_be_bounded_multipart(world, token):
    c = world.client
    r = c.post("/v1/datasets", content=STORES, headers={**token(ANN), "content-type": "text/csv"})
    assert r.status_code == 415
    big = {**token(ANN), "content-type": "multipart/form-data; boundary=x",
           "content-length": str(30 * 2**20)}
    assert c.post("/v1/datasets", content=b"", headers=big).status_code == 413
    r = upload(world, token, colour="red")
    assert r.status_code == 422 and "red" not in r.text


# --- joined to Tributary's figures --------------------------------------------------------

def test_a_formula_joins_your_data_to_ours_by_quarter(world, token):
    stored(world, token, units="sales:money")
    body = evaluate(world, token, ANN, "revenue / mine.stores.stores").json()
    assert [(p["period"], Decimal(p["value"])) for p in body["points"]] == [
        ("FY2026 Q1", 10), ("FY2026 Q2", Decimal("12.5"))]
    assert body["inputs"][1]["dataset"] == "stores"
    assert body["unit"] == "USD"
    ours, yours = body["points"][1]["inputs"]
    assert ours["citation"]["accession_no"] and yours["citation"] is None
    # money in two currencies can't be combined (Tributary converts none)
    world.client.patch(f"/v1/datasets/{body['inputs'][1]['dataset_id']}",
                       json={"currency": "EUR"}, headers=token(ANN))
    r = evaluate(world, token, ANN, "mine.stores.sales - revenue")
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "currency"


def test_the_company_panel_shows_only_your_rows_for_that_company(world, token):
    stored(world, token)
    panel = world.client.get(f"/v1/companies/{CO}/datasets", headers=token(ANN)).json()
    [ds] = panel["datasets"]
    assert [r["day"] for r in ds["rows_here"]] == ["2026-06-30", "2026-03-31"]
    early = world.client.get(f"/v1/companies/{CO}/datasets", params={"as_of": "2026-11-01"},
                             headers=token(ANN)).json()
    assert early["datasets"] == [] and early["known_on"] == "2026-11-01"


def save_metric(w, token, user, name, formula, view="quarter"):
    ws = w.client.post("/v1/workspaces", json={"name": "Metrics"}, headers=token(user)).json()
    r = w.client.post(f"/v1/workspaces/{ws['workspace_id']}/items", headers=token(user),
                      json={"kind": "metric", "name": name,
                            "state": {"formula": formula, "view": view}})
    assert r.status_code == 201, r.text
    return r.json()["item_id"]


def test_an_own_metric_is_a_sortable_screener_column(world, token):
    if world.app_conn is None:
        pytest.skip("APP_TEST_DATABASE_URL not set")
    stored(world, token, units="sales:money")
    item = save_metric(world, token, ANN, "Revenue per store", "revenue / mine.stores.stores")
    key = "m_" + item.replace("-", "")
    r = world.client.get("/v1/screener", headers=token(ANN),
                         params={"metric": item, "columns": ["revenue_q"], "sort": f"-{key}",
                                 "company_id": [CO, OTHER]})
    assert r.status_code == 200, r.text
    body = r.json()
    info = next(c for c in body["columns"] if c["key"] == key)
    assert info["own"]["name"] == "Revenue per store" and info["unit"] == "USD"
    values = {row["company_id"]: round(Decimal(row["values"][key]), 4) for row in body["rows"]}
    assert values == {CO: Decimal("12.5"), OTHER: Decimal("21.4286")}
    assert [row["company_id"] for row in body["rows"]] == [OTHER, CO]
    # a company with no figure for an input says which (Riley, test 10, finding 26)
    nameless = save_metric(world, token, ANN, "Per share", "revenue / shares_outstanding")
    nkey = "m_" + nameless.replace("-", "")
    blank = world.client.get("/v1/screener", headers=token(ANN),
                             params={"metric": nameless, "company_id": [CO]}).json()["rows"][0]
    assert blank["values"][nkey] is None
    assert blank["readings"][nkey] == {"status": "not_reported",
                                        "reason": "shares_outstanding: no figure on file"}
    filtered = world.client.get("/v1/screener", headers=token(ANN),
                                params={"metric": item, "where": f"{key}:lt:20",
                                        "company_id": [CO, OTHER]}).json()
    assert [row["company_id"] for row in filtered["rows"]] == [CO]
    # a metric with a lag or a ticker isn't a column
    lag = save_metric(world, token, ANN, "Lagged", "revenue / revenue[-4q]")
    r = world.client.get("/v1/screener", params={"metric": lag}, headers=token(ANN))
    assert r.status_code == 422 and "latest figures" in r.json()["detail"]


def test_at_most_twenty_metrics(world, token):
    if world.app_conn is None:
        pytest.skip("APP_TEST_DATABASE_URL not set")
    ws = world.client.post("/v1/workspaces", json={"name": "M"}, headers=token(ANN)).json()
    for n in range(MAX_METRICS):
        r = world.client.post(f"/v1/workspaces/{ws['workspace_id']}/items", headers=token(ANN),
                              json={"kind": "metric", "name": f"m{n}",
                                    "state": {"formula": "revenue"}})
        assert r.status_code == 201, r.text
    r = world.client.post(f"/v1/workspaces/{ws['workspace_id']}/items", headers=token(ANN),
                          json={"kind": "metric", "name": "one more",
                                "state": {"formula": "revenue"}})
    assert r.status_code == 409 and "20 metrics" in r.json()["detail"]
    bad = world.client.post(f"/v1/workspaces/{ws['workspace_id']}/items", headers=token(BEN),
                            json={"kind": "metric", "name": "x", "state": {"formula": "a +"}})
    assert bad.status_code in (404, 422)
