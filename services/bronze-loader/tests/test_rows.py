import json
from datetime import datetime

import pytest

from bronze_loader.rows import to_row

EVENT = {
    "accession_no": "0001213900-26-110436",
    "cik": "0002042022",
    "company_name": "WhiteFiber, Inc.",
    "role": "Issuer",
    "form_type": "4",
    "filed_at": "2026-10-16T21:42:41-04:00",
    "url": "https://www.sec.gov/Archives/edgar/data/2042022/000121390026110436/0001213900-26-110436-index.htm",
}


def test_to_row():
    row = to_row(json.dumps(EVENT).encode(), partition=0, offset=7)
    assert row.accession_no == EVENT["accession_no"]
    assert row.cik == EVENT["cik"]
    assert row.form_type == "4"
    assert row.filed_at == datetime.fromisoformat(EVENT["filed_at"])
    assert row.payload == EVENT
    assert (row.kafka_partition, row.kafka_offset) == (0, 7)


@pytest.mark.parametrize("value", [b"not json", json.dumps({"cik": "1"}).encode()])
def test_malformed_raises(value):
    with pytest.raises((ValueError, KeyError)):
        to_row(value, 0, 0)
