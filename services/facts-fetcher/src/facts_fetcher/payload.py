"""Turn a raw companyfacts payload into the bronze row both the API and bulk paths store."""

import gzip
import hashlib
import json
from collections.abc import Collection
from dataclasses import dataclass

# (taxonomy, tag), e.g. ("us-gaap", "Revenues")
Tag = tuple[str, str]


@dataclass(frozen=True)
class Payload:
    cik: str
    sha256: str       # of the raw bytes, as fetched
    facts_json: str   # the payload restricted to mapped tags (bronze.company_facts.payload)
    raw_gzip: bytes
    size_bytes: int


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def project(doc: dict, tags: Collection[Tag]) -> dict:
    """The payload with only the facts of the given tags; same shape as the SEC's."""
    facts = doc.get("facts") or {}
    kept: dict[str, dict] = {}
    for taxonomy, tag in sorted(tags):
        fact = (facts.get(taxonomy) or {}).get(tag)
        if fact is not None:
            kept.setdefault(taxonomy, {})[tag] = fact
    return {"cik": doc.get("cik"), "entityName": doc.get("entityName"), "facts": kept}


def prepare(cik: str, raw: bytes, tags: Collection[Tag], sha256: str | None = None) -> Payload:
    return Payload(
        cik=cik,
        sha256=sha256 or digest(raw),
        facts_json=json.dumps(project(json.loads(raw), tags), separators=(",", ":")),
        raw_gzip=gzip.compress(raw, compresslevel=6),
        size_bytes=len(raw),
    )


def reproject(payload_raw_gzip: bytes, tags: Collection[Tag]) -> str:
    """facts_json for a stored raw payload, under the current tags."""
    return json.dumps(project(json.loads(gzip.decompress(payload_raw_gzip)), tags),
                      separators=(",", ":"))
