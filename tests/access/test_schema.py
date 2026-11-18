"""The schema check (allowlist.toml): every table that holds a user's rows has RLS enabled and
forced and a policy keyed on the session's user, or says in one line why not; every SECURITY
DEFINER function is listed with a reason, a pinned search_path, its only grantee and the test
that shows its rule. Then RLS alone, table by table: as another user, nothing of Ann's can be
read, changed, deleted or planted, whatever the application code does.

Runs against the migrated test databases (CI migrates as a non-superuser, as hermes does, so
FORCE binds the tables' owner)."""

import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from access_world import ANN, APP_DSN, BEN, CO, DATA_DSN, World, make_world

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
ALLOW = tomllib.loads((HERE / "allowlist.toml").read_text())
USER_COLUMNS = ["user_id", "owner_id", "created_by"]
USER_RULE = re.compile(r"^\(?user_id = current_setting\('app\.user_id'::text, true\)\)?$")
HOLDER_RULE = re.compile(r"current_setting\('app\.(api_key_hash|oauth_code_hash|"
                         r"oauth_token_hash)'::text, true\)")
SEARCH_PATH = "search_path=pg_catalog, pg_temp"

pytestmark = pytest.mark.skipif(not (DATA_DSN and APP_DSN),
                                reason="TEST_DATABASE_URL and APP_TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    """One world, catalog closed (RLS alone doesn't depend on it)."""
    with make_world(False) as w:
        yield w


def conn_of(world: World, db: str) -> Any:
    return world.appdb if db == "app" else world.data


def tables(world: World, db: str) -> dict[str, dict]:
    """Every table that must be listed: in the app database all of them; in the tributary
    database those in `userdata` or with a user column."""
    rows = conn_of(world, db).execute(
        "SELECT CASE WHEN n.nspname = 'public' THEN c.relname"
        "            ELSE n.nspname || '.' || c.relname END,"
        "       n.nspname, c.relrowsecurity, c.relforcerowsecurity,"
        "       EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0"
        "               AND NOT a.attisdropped AND a.attname = ANY(%s))"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition"
        "   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')",
        (USER_COLUMNS,)).fetchall()
    return {name: {"schema": schema, "rls": rls, "forced": forced, "user_keyed": keyed}
            for name, schema, rls, forced, keyed in rows
            if db == "app" or schema == "userdata" or keyed}


def policies(world: World, db: str, table: str) -> list[tuple]:
    schema, _, name = table.rpartition(".")
    return conn_of(world, db).execute(
        "SELECT policyname, permissive, cmd, coalesce(qual, ''), coalesce(with_check, '')"
        " FROM pg_policies WHERE schemaname = %s AND tablename = %s",
        (schema or "public", name)).fetchall()


@pytest.mark.parametrize("db", ["app", "tributary"])
def test_every_user_table_is_listed(world: World, db: str) -> None:
    found, listed = tables(world, db), ALLOW[db]["tables"]
    assert sorted(set(found) - set(listed)) == [], \
        "a new table: list it in tests/access/allowlist.toml (owner, or why not)"
    assert sorted(set(listed) - set(found)) == [], "listed but gone: remove it"
    for name, why in listed.items():
        assert why == "owner" or len(why) > 20, (name, "say why it isn't an owner table")


def owner_tables() -> list[tuple[str, str]]:
    return [(db, t) for db in ("app", "tributary") for t, why in ALLOW[db]["tables"].items()
            if why == "owner"]


@pytest.mark.parametrize("db, table", owner_tables())
def test_owner_tables_are_forced_and_keyed_on_the_user(world: World, db: str,
                                                       table: str) -> None:
    t = tables(world, db)[table]
    assert t["rls"] and t["forced"], (table, "ENABLE and FORCE ROW LEVEL SECURITY")
    pols = policies(world, db, table)
    assert any(cmd == "ALL" and USER_RULE.match(q) and USER_RULE.match(wc)
               for _, _, cmd, q, wc in pols), (table, "a policy FOR ALL keyed on app.user_id")
    for name, permissive, cmd, qual, check in pols:
        if USER_RULE.match(qual) and (not check or USER_RULE.match(check)):
            continue
        # anything else: a SELECT-only holder rule (a key's or a token's own row, by its hash)
        assert permissive == "PERMISSIVE" and cmd == "SELECT" and HOLDER_RULE.search(qual), \
            (table, name, cmd, qual, check)


def definers(world: World, db: str) -> dict[str, dict]:
    rows = conn_of(world, db).execute(
        "SELECT n.nspname || '.' || p.proname, p.proconfig, p.prosrc,"
        "       ARRAY(SELECT a.grantee::regrole::text FROM aclexplode(p.proacl) a"
        "             WHERE a.privilege_type = 'EXECUTE' AND a.grantee <> p.proowner"
        "               AND a.grantee <> 0),"
        "       EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner)))"
        "               a WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')"
        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE p.prosecdef"
        ).fetchall()
    out: dict[str, dict] = {}
    for name, config, src, grantees, public in rows:
        assert name not in out, (name, "one SECURITY DEFINER form per name: list each")
        out[name] = {"config": config or [], "src": src, "grantees": sorted(grantees),
                     "public": public}
    return out


@pytest.mark.parametrize("db", ["app", "tributary"])
def test_every_security_definer_function_is_listed_pinned_and_tested(world: World,
                                                                    db: str) -> None:
    found = definers(world, db)
    listed = ALLOW.get(db, {}).get("definer", {})
    assert sorted(set(found) - set(listed)) == [], \
        "a new SECURITY DEFINER function: list it in tests/access/allowlist.toml"
    assert sorted(set(listed) - set(found)) == [], "listed but gone: remove it"
    for name, f in found.items():
        entry = listed[name]
        assert entry["rule"] in ("readable", "aggregate", "limiter"), name
        assert len(entry["reason"]) > 20, name
        assert SEARCH_PATH in f["config"], (name, f["config"])
        assert not f["public"], (name, "EXECUTE granted to PUBLIC")
        assert f["grantees"] == [entry["grant"]], (name, f["grantees"])
        if entry["rule"] == "readable":
            assert re.search(r"authz\.readable_companies\('(financials|filings)'\)", f["src"]), \
                (name, "applies authz.readable_companies itself")
        path, _, test = entry["test"].partition("::")
        source = (ROOT / path).read_text()
        assert re.search(rf"^(async )?def {test}\(", source, re.M), (name, entry["test"])


# --- RLS alone ----------------------------------------------------------------------------------

def as_user(world: World, db: str, user: str) -> Any:
    conn = conn_of(world, db)
    if db == "tributary":
        conn.execute("SET LOCAL ROLE query_api")
    conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
    return conn


def around_rls(world: World, db: str) -> Any:
    """The session's own (super)user, for the setup and the checks."""
    conn = conn_of(world, db)
    conn.execute("RESET ROLE")
    conn.execute("SELECT set_config('app.user_id', '', true)")
    return conn


@pytest.mark.parametrize("db, table", owner_tables())
def test_rls_alone_keeps_anns_rows_from_everyone_else(world: World, db: str,
                                                      table: str) -> None:
    """Straight SQL as the Query API's login (the app tables' owner; query_api in tributary),
    no application code: Ben's session can't see, change or delete a row of Ann's, or write
    one as hers; a session with no user reads nothing."""
    if db == "app" and not world.rls_binds:
        pytest.skip(f"the app tables' owner {world.owner} bypasses RLS (migrate as a "
                    "non-superuser, as CI does)")
    with world.rollback():
        conn = around_rls(world, db)
        theirs = conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s",
                              (ANN,)).fetchone()[0]
        assert theirs > 0, f"give Ann a row in {table} (access_world)"
        conn.execute(f"CREATE TEMP TABLE planted ON COMMIT DROP AS SELECT * FROM {table}"
                     " WHERE user_id = %s LIMIT 1", (ANN,))
        conn.execute("GRANT SELECT ON planted TO PUBLIC")
        if db == "app":
            conn.execute(f'SET ROLE "{world.owner}"')
        for user in (BEN, ""):
            c = as_user(world, db, user)
            assert c.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s",
                             (ANN,)).fetchone()[0] == 0
            if not user:
                assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            assert c.execute(f"UPDATE {table} SET user_id = user_id WHERE user_id = %s",
                             (ANN,)).rowcount == 0
            assert c.execute(f"DELETE FROM {table} WHERE user_id = %s", (ANN,)).rowcount == 0
        c = as_user(world, db, BEN)
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"), \
                c.transaction():
            c.execute(f"INSERT INTO {table} OVERRIDING SYSTEM VALUE SELECT * FROM planted")
        conn = around_rls(world, db)
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s",
                            (ANN,)).fetchone()[0] == theirs


# --- a DEFINER function no other test reads as two users ----------------------------------------

def test_disagreeing_figures_apply_the_read_rule(world: World) -> None:
    """gold.disagreeing_figures (075) reads silver's facts (no grant for query_api) for the
    companies the caller reads: Ann has a private grant on CO (catalog closed), Ben nothing."""
    with world.rollback():
        conn = around_rls(world, "tributary")
        run = conn.execute("INSERT INTO ops.quality_runs (companies) VALUES (1)"
                           " RETURNING run_id").fetchone()[0]
        acc = f"{CO}-26-000009"
        conn.execute(
            "INSERT INTO ops.quality_findings (company_id, check_name, metric, period_end,"
            " severity, detail, run_id) VALUES (%s, 'tag_disagreement', 'revenue', '2026-06-30',"
            " 'warning', jsonb_build_object('accession_no', %s::text, 'tag', 'Revenues',"
            " 'other_tag', 'SalesRevenueNet', 'period_start', '2026-04-01'), %s)",
            (CO, acc, run))
        for tag, value in (("Revenues", 150), ("SalesRevenueNet", 150000)):
            conn.execute(
                "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
                " period_end, value, accession_no, form, filed) VALUES (%s, 'revenue', %s,"
                " 'USD', '2026-04-01', '2026-06-30', %s, %s, '10-Q', '2026-08-01')",
                (CO, tag, value, acc))
        for user, want in ((ANN, 2), (BEN, 0), ("", 0)):
            c = as_user(world, "tributary", user)
            got = c.execute("SELECT count(*) FROM gold.disagreeing_figures(%s)",
                            ([CO],)).fetchone()[0]
            assert got == want, (user, got)
            around_rls(world, "tributary")
