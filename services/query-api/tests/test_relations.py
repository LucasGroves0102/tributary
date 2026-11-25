"""GET /v1/companies/{id}/relations: the companies a business section names (migration 160), both
ways, each with its sentence and filing. Fake layers: the tier check, grouping, truncation."""
from datetime import date

from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_ui_api import UIStore

TSMC, MU = "0001046179", "0000723125"


def row(company_id, named_id, start, sentence, *, ownership=False, other=("Other", ["OTH"]),
        acc="0001045810-26-000021"):
    return {"company_id": company_id, "named_id": named_id, "accession_no": acc,
            "section_key": "1", "char_start": start, "char_end": start + len(sentence or ""),
            "ownership": ownership, "form": "10-K", "filed": date(2026, 2, 25),
            "cik": company_id, "sentence": sentence,
            "other_name": other[0], "other_tickers": other[1]}


class RelationStore(UIStore):
    def __init__(self, names=None, named_by=None):
        super().__init__()
        self._names = names or []
        self._named_by = named_by or []

    def business_relations(self, user_id, company_id, limit):
        self.calls.append(("business_relations", company_id, limit))
        return {"names": self._names[:limit + 1], "named_by": self._named_by[:limit + 1]}


def client(grants, store):
    c = TestClient(create_app(FakeAuthorizer(grants), store, SECRET))
    c.store = store
    return c


def test_relations_need_the_filings_tier(token):
    store = RelationStore()
    c = client({"alice": {NVDA: {"financials"}}}, store)
    assert c.get(f"/v1/companies/{NVDA}/relations", headers=token("alice")).status_code == 403
    assert not [x for x in store.calls if x[0] == "business_relations"]  # no read before require


def test_relations_group_sentences_by_company_both_ways(token):
    store = RelationStore(
        names=[row(NVDA, TSMC, 100, "We utilize foundries, such as TSMC.",
                   other=("Taiwan Semiconductor Manufacturing Co Ltd", ["TSM"])),
               row(NVDA, TSMC, 900, "TSMC produces our wafers.",
                   other=("Taiwan Semiconductor Manufacturing Co Ltd", ["TSM"])),
               row(NVDA, MU, 400, "We purchase memory from Micron.",
                   other=("Micron Technology Inc", None))],
        named_by=[row(TSLA, NVDA, 50, None, ownership=True, other=("Nvidia", ["NVDA"]),
                      acc="0001318605-26-000010")])
    c = client({"alice": {NVDA: {"financials", "filings"}}}, store)
    r = c.get(f"/v1/companies/{NVDA}/relations", headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] == NVDA and body["truncated"] is False
    assert "doesn't classify direction" in body["note"]
    tsmc, mu = body["names"]
    assert tsmc["company_id"] == TSMC and tsmc["tickers"] == ["TSM"] and tsmc["ownership"] is False
    assert [s["sentence"] for s in tsmc["sentences"]] == [
        "We utilize foundries, such as TSMC.", "TSMC produces our wafers."]
    first = tsmc["sentences"][0]
    assert first["accession_no"] == "0001045810-26-000021" and first["form"] == "10-K"
    assert first["filed"] == "2026-02-25" and first["section_key"] == "1"
    assert first["char_end"] - first["char_start"] == len("We utilize foundries, such as TSMC.")
    assert first["filing_url"].endswith("/1045810/000104581026000021/")
    assert mu["tickers"] == []  # none listed: an empty list, never null
    (tesla,) = body["named_by"]
    assert tesla["company_id"] == TSLA and tesla["ownership"] is True
    assert tesla["sentences"][0]["sentence"] is None  # no served section holds the offsets


def test_relations_say_when_a_side_was_cut(token):
    names = [row(NVDA, f"00000000{i:02d}", i * 10, f"Sentence {i}.") for i in range(5)]
    store = RelationStore(names=names)
    c = client({"alice": {NVDA: {"financials", "filings"}}}, store)
    body = c.get(f"/v1/companies/{NVDA}/relations?limit=3", headers=token("alice")).json()
    assert body["truncated"] is True and len(body["names"]) == 3
    assert ("business_relations", NVDA, 3) in store.calls
    assert c.get(f"/v1/companies/{NVDA}/relations?limit=501",
                 headers=token("alice")).status_code == 422
