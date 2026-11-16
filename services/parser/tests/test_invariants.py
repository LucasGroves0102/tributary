"""Invariants over the real stored filings in fixtures/ (filing_parser.invariants): every key one
the form has, every core item present. Rust agrees with Python on these documents
(test_rust_service.py, CI), so a rule both get wrong fails here, not just in production's parser
checks.

cvr-8-K is Chicago Rivet & Machine's 8-K of 2024-07-01 (0001193125-24-173280, from production's
bronze): "Item 7.01Regulation FD Disclosure", the title run into the item number. Before rule 2's
fix (PARSER_VERSION 6) both parsers keyed it "7"; that fails test_every_key_is_one_the_form_has."""

import gzip
import re
from pathlib import Path

import pytest

from filing_parser import invariants
from filing_parser.sections import exhibit_section, parse

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[3] / "db" / "migrations" / "072_data_trust_checks.sql"
# every stored filing in fixtures/, with its form
FILINGS = sorted((p.name.removesuffix(".htm.gz"), p.name.split("-", 1)[1].removesuffix(".htm.gz")
                  .removesuffix("-prior").removesuffix("-year-ago"))
                 for p in FIXTURES.glob("*.htm.gz"))


def sections(name: str, form: str):
    return parse(gzip.decompress((FIXTURES / f"{name}.htm.gz").read_bytes()), form)


def test_the_fixtures_cover_every_form():
    assert {form for _, form in FILINGS} == {"8-K", "10-K", "10-Q"}
    assert ("cvr-8-K", "8-K") in FILINGS


@pytest.mark.parametrize(("name", "form"), FILINGS)
def test_every_key_is_one_the_form_has(name, form):
    keys = [s.key for s in sections(name, form)]
    assert keys and invariants.invalid_keys(form, keys) == []


# A known gap, both parsers (production's missing_core_item counts it: 14,882 10-Qs on 2026-11-16):
# Part I Item 1 isn't found when no line starts "Item 1" in the body and its title isn't a line of
# its own there (Molina: "1. Financial Statements"; TriNet: the title only in the contents). Strict:
# a fix that finds it fails these until the marks go.
KNOWN_MISSING = {"moh-10-Q", "tnet-10-Q"}


@pytest.mark.parametrize(("name", "form"), [
    pytest.param(name, form, marks=pytest.mark.xfail(
        strict=True, reason="Part I Item 1 not found without an Item heading (known gap)"))
    if name in KNOWN_MISSING else (name, form) for name, form in FILINGS])
def test_core_items_are_present(name, form):
    assert invariants.missing_core(form, [s.key for s in sections(name, form)]) == []


@pytest.mark.parametrize(("name", "form"), FILINGS)
def test_no_section_is_empty_or_repeated(name, form):
    found = sections(name, form)
    texts = [s.text for s in found]
    assert all(t.strip() for t in texts) and len(set(texts)) == len(texts)


def test_the_run_on_item_is_7_01():
    assert [s.key for s in sections("cvr-8-K", "8-K")] == ["7.01"]


def test_the_rules_catch_what_the_parity_tests_missed():
    assert not invariants.valid_key("8-K", "7")       # "Item 7.01Regulation" before rule 2's fix
    assert not invariants.valid_key("10-Q", "I-6")    # a Part II item read as Part I
    assert not invariants.valid_key("10-K", "5.02")   # an 8-K item quoted in Item 9B
    assert invariants.valid_key("10-K", "4A") and invariants.valid_key("10-K/A", "9C")
    assert invariants.valid_key("8-K", "EX-99.1") and invariants.valid_key("8-K/A", "5.07")
    assert not invariants.valid_key("8-K", "EX-99.1 PRESS")
    assert invariants.valid_key("20-F", "anything")
    assert invariants.missing_core("10-K", ["1", "7"]) == ["1A", "8"]
    assert invariants.missing_core("10-K/A", []) == []
    assert invariants.missing_core("10-Q", ["II-1"]) == ["I-1", "I-2"]
    exhibit = exhibit_section("EX-99.1", "Press release", b"<p>Revenue grew.</p>")
    assert invariants.valid_key("8-K", exhibit.key)


def test_the_patterns_are_the_databases():
    """ops.section_key_valid (072) and ops.core_section_keys say the same, pattern for pattern."""
    sql = MIGRATION.read_text()
    body = sql[sql.index("CREATE FUNCTION ops.section_key_valid"):]
    body = body[:body.index("$$;")]
    found = dict(re.findall(r"WHEN p_form ~ '\^([0-9A-Z-]+)' THEN p_key ~ '([^']+)'", body))
    assert found == invariants.KEY_PATTERNS
    assert re.search(r"THEN p_key ~ '([^']+)'", body).group(1) == invariants.EXHIBIT
    core = sql[sql.index("CREATE FUNCTION ops.core_section_keys"):]
    core = core[:core.index("$$;")]
    assert {form: tuple(re.findall(r"'([0-9A-Z-]+)'", keys)) for form, keys in
            re.findall(r"WHEN '([0-9A-Z-]+)' THEN ARRAY\[([^\]]+)\]", core)} == \
        invariants.CORE_KEYS
