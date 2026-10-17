"""Turn filings.raw messages into bronze.filings rows."""

import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FilingRow:
    accession_no: str
    cik: str
    form_type: str
    filed_at: datetime
    payload: dict
    kafka_partition: int
    kafka_offset: int


def to_row(value: bytes, partition: int, offset: int) -> FilingRow:
    """Raises ValueError (including json.JSONDecodeError) or KeyError on a malformed event."""
    payload = json.loads(value)
    return FilingRow(
        accession_no=payload["accession_no"],
        cik=payload["cik"],
        form_type=payload["form_type"],
        filed_at=datetime.fromisoformat(payload["filed_at"]),
        payload=payload,
        kafka_partition=partition,
        kafka_offset=offset,
    )
