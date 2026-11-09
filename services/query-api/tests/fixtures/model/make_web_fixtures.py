"""Regenerates the web app's model-run fixtures (services/web/src/models/fixtures) from this
service's own route: POST /v1/companies/{id}/model, in process, over
- the real inputs here ({nvda,deck,vrt}.json: production answers of 2026-11-09) for
  {vrt,vrt-old,deck,nvda}-run.json, and
- the web UI tests' synthetic NVDA history (revenue +10% a year, 20% operating margin; the
  Thesis test's also has a TTM at each Q3) for synthetic-runs.json.
Run from the repo root: PYTHONPATH=services/query-api/src python
services/query-api/tests/fixtures/model/make_web_fixtures.py. Never hand-edit the outputs."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.auth import mint

HERE = Path(__file__).parent
WEB = HERE.parents[3] / "web" / "src" / "models" / "fixtures"
IDS = {"vrt": "0001674101", "deck": "0000910521", "nvda": "0001045810"}
NVDA = IDS["nvda"]


class Allow:
    def can(self, *_):
        return True


class Store:
    synthetic: list | None = None

    def model_inputs(self, user_id, company_id, *_):
        if Store.synthetic is not None:
            return {"ttm": Store.synthetic, "quarters": [], "annual": [], "latest": {}}
        t = next(k for k, v in IDS.items() if v == company_id)
        return json.loads((HERE / f"{t}.json").read_text())


SECRET = "fixture-only-" + "x" * 30
client = TestClient(create_app(Allow(), Store(), SECRET))
AUTH = {"Authorization": f"Bearer {mint('u', SECRET)}"}


def run(state: dict) -> dict:
    r = client.post(f"/v1/companies/{state['companyId']}/model", json=state, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


REAL = {
    "vrt-run": {"companyId": IDS["vrt"], "historyYears": 6, "ytd": True,
                "assumptions": [{"growth": 53, "margin": 25.3, "note": "Q4 guide: $4,413M"},
                                {"growth": 20, "margin": 22, "taxRate": 21},
                                {"growth": 12, "margin": 21}],
                "scenarios": {"bear": [{"growth": 35, "margin": 21}, {"growth": 8, "margin": 18},
                                       {"growth": 5, "margin": 17}],
                              "bull": [{"growth": 60, "margin": 27}, {"growth": 30, "margin": 25},
                                       {"growth": 18, "margin": 24}]},
                "adjustments": [{"name": "UIG", "fy": 2026, "quarter": 4, "kind": "acquired",
                                 "amount": 150e6}],
                "price": {"value": 150, "date": "2026-11-06"}},
    "vrt-old-run": {"companyId": IDS["vrt"], "historyYears": 8,
                    "assumptions": [{"growth": 40, "margin": 21}, {"growth": 15, "margin": 20}]},
    "deck-run": {"companyId": IDS["deck"], "historyYears": 6, "ytd": True,
                 "assumptions": [{"growth": 4.4, "margin": 24.5},
                                 {"growth": 5, "margin": 22, "shareChange": -6}],
                 "adjustments": [{"name": "IEEPA refunds", "fy": 2027, "quarter": 2,
                                  "kind": "one_off", "amount": 46e6}],
                 "price": {"value": 100, "date": "2026-11-06"}},
    "nvda-run": {"companyId": IDS["nvda"], "historyYears": 8, "ytd": True,
                 "assumptions": [{"growth": 95.8, "margin": 75},
                                 {"growth": 44.2, "margin": 55.6}]},
}


def row(metric: str, end: str, value: float, fp: str) -> dict:
    fy, q = fp[2:6], fp[-1]
    return {"metric": metric, "period_end": end, "fiscal_year": int(fy), "fiscal_quarter": int(q),
            "value": str(value), "accession_no": f"0001045810-{fy[2:]}-000001", "form": "10-K",
            "filed": f"{fy}-02-26", "filer_cik": None}


def history(with_q3: bool) -> list[dict]:
    out = []
    for i, fy in enumerate([2021, 2022, 2023, 2024, 2025]):
        revenue = 100e9 * 1.1 ** i
        end = f"{fy}-01-26"
        out += [row("revenue", end, revenue, f"FY{fy} Q4"),
                row("operating_income", end, revenue * 0.2, f"FY{fy} Q4")]
        if with_q3:
            out.append(row("revenue", f"{fy}-10-26", revenue * 1.1 ** 0.75, f"FY{fy + 1} Q3"))
    return out


MODEL = [{"growth": 25, "margin": 50, "note": "Blackwell"}, {"growth": 10, "margin": 45}]
SYNTHETIC = {
    "thesis-2": (True, {"companyId": NVDA, "historyYears": 8, "assumptions": MODEL}),
    "thesis-3": (True, {"companyId": NVDA, "historyYears": 8,
                        "assumptions": [*MODEL, {"growth": 10, "margin": 45}]}),
    "thesis-new": (True, {"companyId": NVDA, "historyYears": 8, "ytd": True,
                          "assumptions": [{"growth": 0, "margin": 0}] * 3}),
    "analysis-2": (False, {"companyId": NVDA, "historyYears": 8,
                           "assumptions": [{"growth": 10, "margin": 20}] * 2}),
}

if __name__ == "__main__":
    for name, state in REAL.items():
        (WEB / f"{name}.json").write_text(
            json.dumps({"state": state, "run": run(state)}, separators=(",", ":")) + "\n")
    out: dict = {"note": "Generated by the F2 branch's POST /v1/companies/{id}/model over the UI "
                         "tests' synthetic NVDA history; regenerate, don't hand-edit."}
    for name, (with_q3, state) in SYNTHETIC.items():
        Store.synthetic = history(with_q3)
        out[name] = run(state)
    Store.synthetic = None
    (WEB / "synthetic-runs.json").write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print("wrote", sorted(p.name for p in WEB.glob("*.json")))
