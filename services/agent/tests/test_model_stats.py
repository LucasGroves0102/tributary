"""model_stats.py since F2: no maths of its own. It asks the Query API to run a model
(`POST /v1/companies/{id}/model`, query_api/model_maths.py is the one copy) and reads which
saved case to check. The numbers are tested where they're computed: services/query-api
tests/test_model_maths.py, on B2 and B7's shared cases and real inputs."""

import json
from pathlib import Path

import httpx
import pytest

from tributary_agent.model_stats import run_model, scenario_assumptions

CASES = json.loads((Path(__file__).parents[2] / "query-api" / "tests" / "fixtures" / "model"
                    / "b2-b7-cases.json").read_text())


@pytest.mark.parametrize("case", CASES["scenario_cases"], ids=lambda c: c["name"])
def test_scenarios_pick_the_same_assumptions_as_before(case):
    for scenario in ("bear", "base", "bull"):
        assert scenario_assumptions(case["state"], scenario) == case["expected"][scenario]


def test_the_base_case_is_what_old_models_hold():
    old = {"companyId": "0001045810", "assumptions": [{"growth": 25, "margin": 30}]}
    assert scenario_assumptions(old) == [{"growth": 25.0, "margin": 30.0}]
    assert scenario_assumptions(old, "bear") is None
    assert scenario_assumptions({"assumptions": "x"}) is None


class Http:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, []

    def call(self, method, path, *, params=None, json_body=None):
        self.calls.append((method, path, json_body))
        if self.status is None:
            return None
        return httpx.Response(self.status, json=self.body if self.body is not None else {})


STATE = {"companyId": "0001045810", "assumptions": [{"growth": 25, "margin": 30}]}
RUN = {"cases": {"base": {"check": {"years": []}, "user_set": [True], "years": []}}}


def test_a_run_posts_the_saved_state_as_the_user():
    http = Http(body=RUN)
    assert run_model(http, "0001045810", STATE) == (RUN, None)
    assert http.calls == [("POST", "/v1/companies/0001045810/model", STATE)]


@pytest.mark.parametrize("status, body, why", [
    (403, {"detail": "not permitted"}, "no access to this company's financials"),
    (404, {"detail": "Not Found"}, "the Query API can't run models yet"),
    (422, {"detail": "invalid model"}, "the saved model isn't one the Query API accepts"),
    (503, {"detail": "down"}, "couldn't run this model"),
    (200, {"cases": {}}, "couldn't run this model"),
    (None, None, "couldn't run this model"),
])
def test_a_run_that_fails_says_why(status, body, why):
    assert run_model(Http(status, body), "0001045810", STATE) == (None, why)
