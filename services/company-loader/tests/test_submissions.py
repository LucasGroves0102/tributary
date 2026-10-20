import json
from datetime import date

from company_loader.submissions import content_hash, entry_cik, parse

APPLE = "0000320193"


def test_entry_cik():
    assert entry_cik("CIK0000320193.json") == (APPLE, False)
    assert entry_cik("CIK0000320193-submissions-001.json") == (APPLE, True)
    assert entry_cik("placeholder.txt") is None


def test_parse_main_and_pages(bulk_entries):
    blobs = [bulk_entries["CIK0000320193.json"], bulk_entries["CIK0000320193-submissions-001.json"]]
    sub = parse(APPLE, blobs, content_hash(blobs))
    assert "filings" not in sub.metadata and sub.metadata["fiscalYearEnd"] == "0926"
    assert len(sub.filings) == 5
    by_acc = {r[0]: r for r in sub.filings}
    ten_q = by_acc["0000320193-94-000002"]
    assert ten_q[1:4] == (APPLE, "10-Q", date(1994, 1, 26))
    assert ten_q[5] == date(1993, 12, 31)
    form_144 = by_acc["0001950047-26-010286"]
    assert form_144[5] is None and form_144[8] is None  # "" → NULL
    assert form_144[11] is False


def test_hash_covers_pages(bulk_entries):
    main = bulk_entries["CIK0000320193.json"]
    page = json.loads(bulk_entries["CIK0000320193-submissions-001.json"])
    page["form"][0] = "4/A"
    assert content_hash([main, json.dumps(page).encode()]) != content_hash(
        [main, bulk_entries["CIK0000320193-submissions-001.json"]])


def test_filer_without_filings(bulk_entries):
    blobs = [bulk_entries["CIK0000004926.json"]]
    sub = parse("0000004926", blobs, content_hash(blobs))
    assert sub.filings == [] and sub.metadata["entityType"] == "other"
