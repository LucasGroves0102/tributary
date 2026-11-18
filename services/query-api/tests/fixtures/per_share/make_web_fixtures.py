"""Writes the web app's per-share fixtures
(services/web/src/company/fixtures/nvda-per-share-*.json): this service's own GET
/v1/companies/{id}/per-share, in process, over nvda.json here (production's Store.per_share_inputs
answer of 2026-11-15) and nvda-originals.json (gold.per_share_originals, 2026-11-18). Run from
the repo root: PYTHONPATH=services/query-api/src python
services/query-api/tests/fixtures/per_share/make_web_fixtures.py Never hand-edit the outputs."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.auth import mint

HERE = Path(__file__).parent
WEB = HERE.parents[3] / "web" / "src" / "company" / "fixtures"
NVDA = "0001045810"
SECRET = "web-fixtures-only-secret-0123456789"


class Allow:
    def can(self, *_):
        return True


class Store:
    def per_share_inputs(self, user_id, company_id, as_of=None):
        # Store reads the originals in the same session (per_share.py, 1 and 5)
        return {**json.loads((HERE / "nvda.json").read_text()),
                "originals": json.loads((HERE / "nvda-originals.json").read_text())["originals"]}


def main() -> None:
    client = TestClient(create_app(Allow(), Store(), SECRET))
    headers = {"Authorization": f"Bearer {mint('fixtures', SECRET)}"}
    for view, quarters, shares in (("quarter", 12, "adjusted"), ("ttm", 8, "adjusted"),
                                   ("annual", 10, "adjusted"), ("annual", 10, "as_filed")):
        r = client.get(f"/v1/companies/{NVDA}/per-share", headers=headers,
                       params={"view": view, "quarters": quarters, "shares": shares})
        r.raise_for_status()
        body = r.json()
        body["as_of"] = "2026-11-15T00:00:00Z"
        name = f"nvda-per-share-{view}{'-filed' if shares == 'as_filed' else ''}.json"
        (WEB / name).write_text(json.dumps(body, indent=1) + "\n")
        print(name, len(body["rows"]))


if __name__ == "__main__":
    main()
