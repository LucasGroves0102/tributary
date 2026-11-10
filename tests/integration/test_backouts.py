"""db/backout: the files run newest first, each puts back exactly the bodies the older migration
created, and each refuses (changing nothing) out of order, twice, or as a login that doesn't own the
functions. Skipped unless TEST_DATABASE_URL is set (CI: a superuser; the migrations ran as the
non-superuser `migrator`, which owns the functions). Every test is rolled back."""

import hashlib
import importlib.util
import os
import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db" / "migrations"
_spec = importlib.util.spec_from_file_location("backout_guards",
                                               ROOT / "scripts" / "backout_guards.py")
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)

# What each file must put back: the function's body as an older migration created it (None: the
# function is gone). Independent of the guards, which are measured from the files themselves.
RESTORES = {
    "revenue_part_064.sql": {"gold.best_facts(text[])": "039_gold_fact_selection.sql"},
    "rebuild_speed_063.sql": {
        "silver.refresh_financial_facts(text[])": "056_ifrs_currencies.sql",
        "gold.refresh_derived_financials(text[])": "056_ifrs_currencies.sql",
    },
    "currency_speed_061.sql": {
        "gold.amount_currency(text)": "060_annual_filer_currency.sql",
        "gold.latest_reading(text,text,text,text,date)": "060_annual_filer_currency.sql",
        "gold.amount_currency_of(text)": None,
    },
    "annual_currency_060.sql": {
        "gold.amount_currency(text)": "057_currency_readings.sql",
        "gold.latest_reading(text,text,text,text,date)": "057_currency_readings.sql",
    },
    "banks_058.sql": {"gold.not_applicable_metrics(text,text)": "049_kpis_cash_flow.sql"},
    "currency_readings_057.sql": {
        "gold.latest_reading(text,text,text,text,date)": "056_ifrs_currencies.sql",
        "gold.not_meaningful(text,numeric,jsonb,numeric)": "035_value_readings.sql",
        "gold.not_meaningful(text,numeric,jsonb,numeric,text)": None,
        "gold.filing_figures(text,text)": "051_filing_figures.sql",
        "ops.check_financials(text[])": "050_filers_debt_signs.sql",
        "gold.amount_currency(text)": None,
    },
    "ifrs_currencies_056.sql": {
        "silver.refresh_financial_facts(text[])": "011_bulk_financials.sql",
        "silver.refresh_filing_facts(text[])": "055_filing_xbrl_facts.sql",
        "gold.usable_facts(text[])": "055_filing_xbrl_facts.sql",
        "gold.refresh_derived_financials(text[])": "049_kpis_cash_flow.sql",
        "gold.latest_reading(text,text,text,text,date)": "035_value_readings.sql",
        "gold.figure_facts(text,text,date)": "049_kpis_cash_flow.sql",
        "gold.annual_per_share(text,integer)": "036_annual_per_share.sql",
    },
    "filing_xbrl_facts_055.sql": {"gold.usable_facts(text[])": "050_filers_debt_signs.sql"},
    "filers_debt_050.sql": {
        "gold.usable_facts(text[])": "043_interest_expense_sign.sql",
        "ops.check_financials(text[])": "041_quality_findings.sql",
    },
    "kpis_049.sql": {
        "gold.refresh_derived_financials(text[])": "048_bank_metrics.sql",
        "gold.not_applicable_metrics(text,text)": "035_value_readings.sql",
    },
    "bank_metrics_048.sql": {
        "gold.refresh_derived_financials(text[])": "040_gold_derived_rules.sql"},
    "interest_expense_043.sql": {
        "gold.usable_facts(text[])": "039_gold_fact_selection.sql",
        "gold.refresh_quarterly_financials(text[])": "039_gold_fact_selection.sql",
    },
    "gold_rules_038_041.sql": {
        "gold.refresh_quarterly_financials(text[])": "031_gold_one_quarter_series.sql",
        "gold.refresh_derived_financials(text[])": "014_gold_calendar.sql",
        "ops.check_financials(text[])": "023_data_notes.sql",
    },
    "segments_speed_062.sql": {
        "gold.refresh_segment_financials(text[])": "053_segment_same_basis.sql"},
    "segments_same_basis_053.sql": {
        "gold.refresh_segment_financials(text[])": "018_segments.sql"},
    "insider_queue_059.sql": {
        "silver.xml_date(text)": "054_insider_transactions.sql",
        "silver.refresh_insider_filings(text[])": "054_insider_transactions.sql",
    },
}


ALIASES = {"int": "integer", "int4": "integer"}


def migration_md5(migration: str, signature: str) -> str:
    """md5 of the function's source as the migration file writes it (what pg_proc.prosrc holds)."""
    name, args = signature.split("(", 1)
    want = [a for a in args.rstrip(")").split(",") if a]
    sql = (MIGRATIONS / migration).read_text()
    bodies = []
    for m in re.finditer(rf"CREATE (?:OR REPLACE )?FUNCTION {re.escape(name)}\s*\(", sql):
        params = sql[m.end():sql.index(")", m.end())]
        types = [ALIASES.get(t, t) for t in
                 (p.split()[1] for p in params.replace("\n", " ").split(",") if p.strip())]
        if types != want:
            continue
        quote = re.compile(r"\$(\w*)\$").search(sql, m.end())
        close = sql.index(quote.group(0), quote.end())
        bodies.append(sql[quote.end():close])
    assert bodies, f"{migration} defines no {signature}"
    return hashlib.md5(bodies[-1].encode()).hexdigest()


def run(cur, name: str) -> None:
    cur.execute(guards.statements((guards.BACKOUT / name).read_text()))


@pytest.fixture
def cur():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True), \
            conn.cursor() as c:
        yield c


def as_owner(cur) -> str:
    owner = guards.owner(cur)
    cur.execute(f'SET ROLE "{owner}"')
    return owner


def test_every_backout_is_guarded_ordered_and_documented():
    files = sorted(p.name for p in guards.BACKOUT.glob("*.sql"))
    assert sorted(guards.CHAIN + guards.INDEPENDENT) == files
    assert set(RESTORES) == set(files)
    for name in files:
        assert (guards.BACKOUT / name).read_text().count(guards.START) == 1, name
    order = (guards.BACKOUT / "README.md").read_text().split("## Order", 1)[1]
    listed = re.findall(r"^\d+\. `([a-z0-9_]+\.sql)`", order, re.M)
    assert listed == guards.CHAIN


def test_the_chain_runs_newest_first_and_restores_the_older_bodies(cur):
    owner = as_owner(cur)
    for name in guards.CHAIN + guards.INDEPENDENT:
        run(cur, name)
        now = guards.snapshot(cur)
        for signature, migration in RESTORES[name].items():
            want = None if migration is None else migration_md5(migration, signature)
            assert now.get(signature) == want, f"{name}: {signature} is not {migration}'s"
    cur.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = ANY(%s) AND pg_get_userbyid(p.proowner) <> %s",
        (list(guards.SCHEMAS), owner))
    assert cur.fetchall() == []
    # a later migration (the migrate login) can still replace what the backouts created
    cur.execute("SELECT pg_get_functiondef(%s::regprocedure)",
                ("gold.latest_reading(text,text,text,text,date)",))
    cur.execute(cur.fetchone()[0])


def test_each_backout_refuses_out_of_order_and_changes_nothing(cur):
    as_owner(cur)
    head = guards.snapshot(cur)
    newer_first = guards.CHAIN[1:] + [n for group in guards.INDEPENDENT_CHAINS for n in group[1:]]
    for name in newer_first:
        with pytest.raises(psycopg.errors.RaiseException, match="run the newer backouts first"):
            with cur.connection.transaction():
                run(cur, name)
        assert guards.snapshot(cur) == head


def test_a_backout_refuses_to_run_twice(cur):
    as_owner(cur)
    for name in (guards.CHAIN[0], guards.INDEPENDENT[0]):
        run(cur, name)
        with pytest.raises(psycopg.errors.RaiseException, match="this one only once"):
            with cur.connection.transaction():
                run(cur, name)


def test_backouts_refuse_a_login_that_does_not_own_the_functions(cur):
    owner = guards.owner(cur)
    cur.execute("SELECT current_user")
    if cur.fetchone()[0] == owner:
        pytest.skip("connected as the functions' owner")
    head = guards.snapshot(cur)
    for name in guards.CHAIN + guards.INDEPENDENT:
        with pytest.raises(psycopg.errors.RaiseException, match="the migrate login"):
            with cur.connection.transaction():
                run(cur, name)
    assert guards.snapshot(cur) == head
