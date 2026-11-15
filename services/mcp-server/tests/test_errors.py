"""Query API refusals become tool errors a model can act on; a tool error is never a crash."""

import httpx
import pytest

from tributary_mcp.api import detail_text

NVDA = "0001045810"
PATH = f"/v1/companies/{NVDA}/events"


@pytest.mark.parametrize(("status", "detail", "headers", "says"), [
    (401, "invalid token", {}, "Tributary refused the API key (unknown, expired or revoked)"),
    (403, "no access", {}, "Permission denied: no access. The user's plan, grants or API key "
                           "scope don't allow this; say so, and don't answer it from other "
                           "sources."),
    (403, "API keys can't use this route: sign in to Tributary", {},
     "Permission denied: API keys can't use this route"),
    (404, "no such item", {}, "Not found: no such item."),
    (422, "too broad", {}, "Tributary didn't take these arguments: too broad. Change them"),
    (429, "this API key made 60 requests this minute; try again in 23 seconds",
     {"Retry-After": "23"}, "Try again in 23 seconds (Retry-After: 23)."),
    (429, "slow", {}, "Rate limit reached for this API key: slow. Try again later."),
    (500, "", {}, "Tributary is unavailable right now (status 500)."),
    (503, "query took longer than 5s; narrow it", {},
     "Tributary is unavailable right now (status 503: query took longer than 5s; narrow it)."),
])
def test_query_api_answers_map_to_clear_tool_errors(h, status, detail, headers, says):
    h.fake.answer(PATH, {"detail": detail}, status=status, headers=headers)
    text = h.error_text(h.call("get_events_since_report", {"company_id": NVDA}))
    assert text.startswith("Error executing tool get_events_since_report: ")
    assert says in text


def test_no_answer_and_a_timeout_say_tributary_is_unavailable(h):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    h.fake.answer(PATH, down)
    assert "isn't answering right now" in h.error_text(
        h.call("get_events_since_report", {"company_id": NVDA}))

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    h.fake.answer(PATH, slow)
    assert "took too long" in h.error_text(h.call("get_events_since_report",
                                                  {"company_id": NVDA}))


def test_details_read_as_words():
    assert detail_text("plain") == "plain"
    assert detail_text([{"loc": ["query", "quarters"], "msg": "must be <= 120",
                         "input": "999"}]) == "quarters: must be <= 120"
    assert detail_text({"errors": [{"message": "KO isn't one of your companies",
                                    "hint": "Did you mean KOF?"}]}) == \
        "KO isn't one of your companies (Did you mean KOF?)"
    assert len(detail_text("x" * 5000)) == 1000


def test_a_formula_refusal_carries_its_errors(h):
    h.fake.answer("/v1/formulas/evaluate", {"detail": {"errors": [
        {"message": "These figures are in different currencies", "hint": "Chart each alone"}]}},
        status=422, method="POST")
    text = h.error_text(h.call("evaluate_formula", {"formula": "SAP.revenue / ORCL.revenue"}))
    assert "different currencies (Chart each alone)" in text


def test_tool_outcomes_are_counted(h):
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value("tributary_mcp_tool_calls_total",
                                       {"tool": "get_events_since_report",
                                        "outcome": "denied"}) or 0
    h.fake.answer(PATH, {"detail": "no access"}, status=403)
    h.call("get_events_since_report", {"company_id": NVDA})
    assert REGISTRY.get_sample_value("tributary_mcp_tool_calls_total",
                                     {"tool": "get_events_since_report",
                                      "outcome": "denied"}) == before + 1
