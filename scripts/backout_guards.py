"""Write the guard at the top of every file in db/backout (db/backout/README.md, "Order").

A guard refuses, changing nothing, unless the database is exactly where that backout belongs: every
function any backout of the chain replaces or drops must have the body (md5 of its source) it has
once the newer backouts have run, newest first, and this one hasn't. So a backout run out of order
or twice stops before its first statement instead of bringing back an older function over a newer
rule (048-050 on a database at 056 did). It also refuses a login that doesn't own those functions:
run as `postgres`, a DROP and CREATE would leave a function owned by postgres, which the
non-superuser migrate login can't replace later.

The expected bodies are measured, not typed: this runs the chain on a database migrated to the head
inside one transaction that is rolled back, recording each file's starting point, and rewrites the
guard blocks. Run it again whenever a migration replaces a function a backout touches, or a backout
is added (add it to CHAIN, and to the README's Order):

    python scripts/backout_guards.py postgresql:///tributary?host=...   # owner login or superuser

tests/integration/test_backouts.py runs the chain against the same rule, checks that each file puts
back the very bodies the older migration created, and that every file refuses out of order.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKOUT = ROOT / "db" / "backout"

# Newest first: the order they run in. Undoing one migration means running every file before it too.
CHAIN = [
    "currency_speed_061.sql",
    "annual_currency_060.sql",
    "banks_058.sql",
    "currency_readings_057.sql",
    "ifrs_currencies_056.sql",
    "filing_xbrl_facts_055.sql",
    "filers_debt_050.sql",
    "kpis_049.sql",
    "bank_metrics_048.sql",
    "interest_expense_043.sql",
    "gold_rules_038_041.sql",
]
# Touch only functions the chain above doesn't: each group runs newest first on its own (062's
# segments backout before 053's), checked against its own migrations' bodies.
INDEPENDENT_CHAINS = [
    ["segments_speed_062.sql", "segments_same_basis_053.sql"],
    ["insider_queue_059.sql"],
]
INDEPENDENT = [name for group in INDEPENDENT_CHAINS for name in group]

START = "-- guard (scripts/backout_guards.py writes it"
END = "-- end guard"
SCHEMAS = ("gold", "silver", "ops", "authz")


def strip_guard(sql: str) -> str:
    out, skipping = [], False
    for line in sql.splitlines(keepends=True):
        if line.startswith(START):
            skipping = True
        if not skipping:
            out.append(line)
        if skipping and line.startswith(END):
            skipping = False
    return "".join(out)


def statements(sql: str, guard: bool = True) -> str:
    """The file as one batch for a caller's own transaction: its BEGIN; and COMMIT; lines out."""
    if not guard:
        sql = strip_guard(sql)
    return "".join(line for line in sql.splitlines(keepends=True)
                   if line.strip() not in ("BEGIN;", "COMMIT;"))


def snapshot(cur) -> dict[str, str]:
    """md5 of every function's source in the data schemas, keyed by its signature."""
    cur.execute(
        "SELECT p.oid::regprocedure::text, md5(p.prosrc) FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = ANY(%s)",
        (list(SCHEMAS),))
    return dict(cur.fetchall())


def owner(cur) -> str:
    cur.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc"
                " WHERE oid = 'gold.usable_facts(text[])'::regprocedure")
    return cur.fetchone()[0]


def measure(conn) -> dict[str, tuple[dict[str, str | None], list[str]]]:
    """Each file's expected starting point over the functions it must see, and the files before it.
    Runs everything in a transaction that is rolled back."""
    starts: list[dict[str, str]] = []
    touched: set[str] = set()
    result = {}
    with conn.transaction(force_rollback=True), conn.cursor() as cur:
        cur.execute(f'SET ROLE "{owner(cur)}"')
        for group in INDEPENDENT_CHAINS:
            group_starts, group_touched = [], set()
            with conn.transaction(force_rollback=True):
                for name in group:
                    before = snapshot(cur)
                    cur.execute(statements((BACKOUT / name).read_text(), guard=False))
                    after = snapshot(cur)
                    group_touched |= {f for f in before.keys() | after.keys()
                                      if before.get(f) != after.get(f)}
                    group_starts.append(before)
            for i, name in enumerate(group):
                result[name] = ({f: group_starts[i].get(f) for f in sorted(group_touched)},
                                group[:i])
        for name in CHAIN:
            before = snapshot(cur)
            cur.execute(statements((BACKOUT / name).read_text(), guard=False))
            after = snapshot(cur)
            touched |= {f for f in before.keys() | after.keys() if before.get(f) != after.get(f)}
            starts.append(before)
    for i, name in enumerate(CHAIN):
        result[name] = ({f: starts[i].get(f) for f in sorted(touched)}, CHAIN[:i])
    return result


def guard(expected: dict[str, str | None], before: list[str]) -> str:
    rows = ",\n".join(f"        ('{fn}', {'NULL' if md5 is None else repr(md5)})"
                       for fn, md5 in expected.items())
    first = ", ".join(before) if before else "none, this is the newest"
    words = (f"{START}; db/backout/README.md, Order). It refuses, changing nothing, unless every"
             f" newer backout has run (newest first: {first}) and this one hasn't, and unless the"
             " login owns the functions (the migrate login; header).").split()
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > 100:
            lines.append(line)
            line = "--"
        line = f"{line} {word}" if line else word
    lines.append(line)
    comment = "\n".join(lines)
    return f"""{comment}
DO $guard$
DECLARE
    w record;
    fn_owner text;
BEGIN
    FOR w IN SELECT v.fn, v.want, to_regprocedure(v.fn) AS id FROM (VALUES
{rows}
    ) v(fn, want) LOOP
        SELECT pg_get_userbyid(p.proowner) INTO fn_owner FROM pg_proc p WHERE p.oid = w.id;
        IF fn_owner IS NOT NULL AND fn_owner <> current_user THEN
            RAISE EXCEPTION USING MESSAGE = format('run this as %s, the owner of %s (the '
                'migrate login), not as %s: see the header. Nothing was changed.', fn_owner, w.fn,
                current_user);
        END IF;
        IF w.want IS DISTINCT FROM (SELECT md5(p.prosrc) FROM pg_proc p WHERE p.oid = w.id) THEN
            RAISE EXCEPTION USING MESSAGE = format('%s is not where this backout starts: run '
                'the newer backouts first, newest first (%s), and this one only once. Nothing was '
                'changed.', w.fn, '{first}');
        END IF;
    END LOOP;
END
$guard$;
{END}
"""


def write(name: str, block: str) -> None:
    path = BACKOUT / name
    sql = strip_guard(path.read_text())
    anchor = re.search(r"^SET LOCAL lock_timeout = '[^']+';\n", sql, re.M)
    if anchor is None:
        raise SystemExit(f"{name}: no SET LOCAL lock_timeout line to put the guard after")
    rest = sql[anchor.end():].lstrip("\n")  # one blank line either side: rerunning changes nothing
    path.write_text(sql[:anchor.end()] + "\n" + block + "\n" + rest)


def main() -> None:
    import psycopg

    with psycopg.connect(sys.argv[1]) as conn:
        measured = measure(conn)
    for name, (expected, before) in measured.items():
        write(name, guard(expected, before))
        print(f"{name}: {len(expected)} functions checked")


if __name__ == "__main__":
    main()
