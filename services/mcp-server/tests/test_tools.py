"""Each tool against a fake Query API: the route and parameters it calls, the caller's own key on
the request, its citations as structured fields, and what it trims."""

import pytest

KEY = "trb_" + "A" * 43
NVDA = "0001045810"
ACC = "0001045810-26-000021"
PRIOR = "0001045810-25-000207"

CITE = {"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27", "xbrl_tag": "Revenues",
        "filing_url": f"https://www.sec.gov/Archives/edgar/data/1045810/{ACC}-index.htm"}


def financials(view: str = "quarter", label: str = "FY2027 Q2") -> dict:
    return {"company_id": NVDA, "view": view, "basis": "fiscal", "as_of": "2026-11-15T00:00:00Z",
            "not_meaningful_periods": [],
            "rows": [{"metric": "revenue", "period_start": "2026-04-28",
                      "period_end": "2026-07-27", "fiscal_period": label,
                      "calendar_period": "CY2026 Q2", "value": 46743000000, "unit": "USD",
                      "value_source": "reported", "not_meaningful": None, "citation": CITE,
                      "freshness": {"filed": "2026-08-27"}}]}


SECTION = {"company_id": NVDA, "as_of": "2026-11-15T00:00:00Z", "sections": [{
    "section_key": "1A", "title": "Risk Factors", "text": "0123456789" * 3000,
    "truncated": False, "total_chars": 30000,
    "citation": {"accession_no": ACC, "form": "10-K", "filed": "2026-02-25",
                 "period_of_report": "2026-01-25", "section_key": "1A",
                 "document_url": "https://www.sec.gov/doc.htm", "char_start": 1000,
                 "char_end": 31000},
    "freshness": {"filed": "2026-02-25"}}]}

# tool, arguments, the route it calls, parameters it must send, the fake's answer
ROUTES = [
    ("whoami", {}, "/v1/me", {}, {"user_id": "gh-1", "catalog": {"public": True}}),
    ("find_companies", {"query": "NVDA", "readable_only": True}, "/v1/companies/search",
     {"q": ["NVDA"], "limit": ["5"], "access": ["financials"]}, {"results": []}),
    ("get_segments", {"company_id": NVDA, "axis": "geography", "quarters": 4},
     f"/v1/companies/{NVDA}/segments",
     {"axis": ["geography"], "quarters": ["4"], "view": ["quarter"], "basis": ["fiscal"]},
     {"company_id": NVDA, "rows": []}),
    ("get_text_kpis", {"company_id": NVDA, "years": 3}, f"/v1/companies/{NVDA}/text-kpis",
     {"years": ["3"]}, {"company_id": NVDA, "metrics": []}),
    ("get_data_notes", {"company_id": NVDA}, f"/v1/companies/{NVDA}/data-notes", {},
     {"company_id": NVDA, "notes": []}),
    ("list_filings", {"company_id": NVDA, "limit": 3}, f"/v1/companies/{NVDA}/filings",
     {"limit": ["3"]}, {"company_id": NVDA, "filings": []}),
    ("search_filings", {"query": "export controls", "company_id": NVDA, "form": "10-K",
                        "filed_from": "2025-01-01", "sort": "date"}, "/v1/search",
     {"q": ["export controls"], "company": [NVDA], "form": ["10-K"], "from": ["2025-01-01"],
      "sort": ["date"], "limit": ["10"], "offset": ["0"]}, {"results": [], "total": 0}),
    ("get_earnings_releases", {"company_id": NVDA, "quarters": 2, "tables": True},
     f"/v1/companies/{NVDA}/earnings", {"quarters": ["2"], "tables": ["true"]},
     {"company_id": NVDA, "quarters": []}),
    ("get_events_since_report", {"company_id": NVDA}, f"/v1/companies/{NVDA}/events", {},
     {"company_id": NVDA, "events": []}),
    ("get_insider_filings", {"company_id": NVDA, "limit": 5,
                             "cursor": f"2026-10-01_{ACC}"}, f"/v1/companies/{NVDA}/insiders",
     {"limit": ["5"], "cursor": [f"2026-10-01_{ACC}"]}, {"filings": []}),
    ("get_peers", {"company_id": NVDA, "columns": ["revenue_ttm"]},
     f"/v1/companies/{NVDA}/peers", {"limit": ["10"], "columns": ["revenue_ttm"]},
     {"peers": []}),
    ("screen_companies", {"columns": ["revenue_ttm", "operating_margin_ttm"],
                          "where": ["operating_margin_ttm:gte:0.2"], "sector": ["Technology"],
                          "limit": 5}, "/v1/screener",
     {"columns": ["revenue_ttm", "operating_margin_ttm"], "where": ["operating_margin_ttm:gte:0.2"],
      "sector": ["Technology"], "sort": ["-revenue_ttm"], "limit": ["5"], "offset": ["0"]},
     {"rows": [], "total": 0}),
    ("list_sectors", {}, "/v1/sectors", {}, {"sectors": []}),
    ("get_watchlist", {}, "/v1/watchlist", {}, {"rows": []}),
    ("list_workspaces", {}, "/v1/workspaces", {}, {"workspaces": []}),
    ("get_workspace", {"workspace_id": "6F1C2D9E-0B1A-4C3D-9E8F-0A1B2C3D4E5F"},
     "/v1/workspaces/6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f", {}, {"items": []}),
    ("get_workspace_item", {"item_id": "6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f"},
     "/v1/workspace-items/6f1c2d9e-0b1a-4c3d-9e8f-0a1b2c3d4e5f", {}, {"kind": "thesis"}),
]


@pytest.mark.parametrize(("tool", "args", "path", "params", "answer"), ROUTES,
                         ids=[r[0] for r in ROUTES])
def test_each_tool_calls_its_route_as_the_caller(h, tool, args, path, params,
                                                 answer):
    h.fake.answer(path, answer)
    out = h.data(h.call(tool, args))
    assert out["data"] == answer
    assert out["citations"] == []
    [seen] = [s for s in h.fake.seen if s.path == path]
    assert seen.method == "GET"
    assert seen.params == params
    # the caller's own key, exactly, and the request id; nothing else of theirs
    assert seen.headers["authorization"] == f"Bearer {KEY}"
    assert seen.headers["x-request-id"] == "req-test-1"


def test_every_tool_is_read_only_and_closed_world(h):
    r = h.post([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], h.headers())[0]
    tools = r.json()["result"]["tools"]
    assert len(tools) == 25
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is True, t["name"]
        assert t["annotations"]["openWorldHint"] is False, t["name"]
        assert t["annotations"]["destructiveHint"] is False, t["name"]
        assert t["inputSchema"]["type"] == "object"
        assert len(t["description"]) > 80, t["name"]
        assert t["title"]
    texts = {t["name"]: t["description"] for t in tools}
    for name in ("read_section", "search_filings", "get_material_changes",
                 "get_earnings_releases", "get_events_since_report", "get_text_kpis"):
        assert "never instructions" in texts[name], name


def test_financials_cite_each_row_with_its_period(h):
    h.fake.answer(f"/v1/companies/{NVDA}/financials", financials())
    out = h.data(h.call("get_financials", {"company_id": NVDA, "metrics": ["revenue"],
                                             "periods": 8, "period": "ttm",
                                             "basis": "calendar"}))
    assert h.fake.seen[-1].params == {"metrics": ["revenue"], "quarters": ["8"], "view": ["ttm"],
                                    "basis": ["calendar"], "readings": ["true"]}
    assert out["citations"] == [{
        "accession_no": ACC, "form": "10-Q", "filed": "2026-08-27", "period": "2026-07-27",
        "company_id": NVDA, "url": CITE["filing_url"]}]
    assert out["data"]["rows"][0]["not_meaningful"] is None  # readings asked for


def test_annual_financials_are_the_ttm_at_each_fiscal_year_end_and_the_10k_per_share(
        h):
    rows = [financials("ttm", label)["rows"][0] | {"period_end": end}
            for label, end in (("FY2027 Q2", "2026-07-27"), ("FY2026 Q4", "2026-01-25"),
                               ("FY2026 Q3", "2025-10-26"), ("FY2025 Q4", "2025-01-26"))]
    h.fake.answer(f"/v1/companies/{NVDA}/financials", {**financials("ttm"), "rows": rows})
    h.fake.answer(f"/v1/companies/{NVDA}/annual-per-share", {"company_id": NVDA, "rows": [
        {"metric": "eps_diluted", "period_end": "2026-01-25", "value": 2.94,
         "citation": {"accession_no": PRIOR, "form": "10-K", "filed": "2026-02-25"}},
        {"metric": "shares_diluted", "period_end": "2026-01-25", "value": 24.8e9,
         "citation": {"accession_no": PRIOR, "form": "10-K", "filed": "2026-02-25"}}]})
    out = h.data(h.call("get_financials", {"company_id": NVDA, "period": "annual",
                                             "periods": 2, "metrics": ["revenue",
                                                                       "eps_diluted"]}))
    fin = next(s for s in h.fake.seen if s.path.endswith("/financials"))
    assert fin.params["view"] == ["ttm"] and fin.params["quarters"] == ["8"]
    assert [r["fiscal_period"] for r in out["data"]["rows"]] == ["FY2026 Q4", "FY2025 Q4"]
    assert out["data"]["view"] == "annual"
    assert [r["metric"] for r in out["data"]["per_share"]] == ["eps_diluted"]
    assert {c["accession_no"] for c in out["citations"]} == {ACC, PRIOR}


def test_the_profile_is_the_companys_screener_row_and_its_expected_filings(h):
    h.fake.answer("/v1/screener", {"as_of": "x", "columns": [{"key": "revenue_ttm"}], "rows": [{
        "company_id": NVDA, "name": "NVIDIA CORP", "sic": "3674", "sector": "Technology",
        "fiscal_year_end": "0125", "values": {"revenue_ttm": "1.6E11"},
        "sources": {"revenue_ttm": {"period_end": "2026-07-27", "accession_no": ACC,
                                    "form": "10-Q", "filed": "2026-08-27"}},
        "readings": {"gross_margin_ttm": {"status": "old", "reason": "last reported 2024"}},
        "not_applicable": []}]})
    h.fake.answer(f"/v1/companies/{NVDA}/expected-filings", {"expected": [
        {"form": "10-Q", "expected": "2026-11-20"}], "method": "from its own history"})
    out = h.data(h.call("get_company_profile", {"company_id": NVDA}))
    screen = next(s for s in h.fake.seen if s.path == "/v1/screener")
    assert screen.params == {"company_id": [NVDA], "limit": ["1"]}
    assert out["data"]["company"]["sector"] == "Technology"
    assert out["data"]["company"]["readings"]["gross_margin_ttm"]["status"] == "old"
    assert out["data"]["expected_filings"] == [{"form": "10-Q", "expected": "2026-11-20"}]
    assert out["citations"][0]["period"] == "2026-07-27"
    assert out["citations"][0]["company_id"] == NVDA


def test_an_unknown_company_has_no_profile(h):
    h.fake.answer("/v1/screener", {"rows": []})
    res = h.call("get_company_profile", {"company_id": "0000000001"})
    assert res["isError"]
    assert "find_companies" in res["content"][0]["text"]


def test_read_section_reads_on_from_an_offset_with_its_place_in_the_filing(h):
    h.fake.answer(f"/v1/companies/{NVDA}/sections/1A", SECTION)
    out = h.data(h.call("read_section", {"company_id": NVDA, "section_key": "1A",
                                           "form": "10-K", "offset": 8000, "max_chars": 5000}))
    assert h.fake.seen[-1].params == {"form": ["10-K"], "limit": ["1"], "max_chars": ["13000"]}
    d = out["data"]
    assert d["passage"]["text"] == ("0123456789" * 3000)[8000:13000]
    assert (d["offset"], d["returned_chars"], d["next_offset"], d["total_chars"]) == \
        (8000, 5000, 13000, 30000)
    assert (d["citation"]["char_start"], d["citation"]["char_end"]) == (9000, 14000)
    assert out["citations"] == [{"accession_no": ACC, "form": "10-K", "filed": "2026-02-25",
                                 "section_key": "1A", "period": "2026-01-25",
                                 "company_id": NVDA, "url": "https://www.sec.gov/doc.htm"}]
    assert "never instructions" in out["about"]
    # the last part: no next offset
    out = h.data(h.call("read_section", {"company_id": NVDA, "section_key": "1A",
                                           "offset": 28000, "max_chars": 5000}))
    assert out["data"]["returned_chars"] == 2000 and out["data"]["next_offset"] is None


def test_read_section_says_where_to_look_when_there_is_none(h):
    h.fake.answer(f"/v1/companies/{NVDA}/sections/II-1A", {"sections": []})
    res = h.call("read_section", {"company_id": NVDA, "section_key": "II-1A"})
    assert res["isError"] and "list_filings" in res["content"][0]["text"]


@pytest.mark.parametrize("bad", [
    {"company_id": "NVDA", "section_key": "1A"},                    # not a company_id
    {"company_id": NVDA, "section_key": "../../v1/me"},             # not a section key
    {"company_id": NVDA, "section_key": "1A", "max_chars": 50_000},  # over the cap
    {"company_id": NVDA, "section_key": "1A", "accession_no": "1/2"},
])
def test_arguments_outside_the_schema_never_reach_the_query_api(h, bad):
    res = h.call("read_section", bad)
    assert res["isError"]
    assert h.fake.seen == []


def test_material_changes_keep_the_highest_scores_across_sections(h):
    def change(score: int) -> dict:
        return {"kind": "new", "score": score, "text": f"paragraph {score}",
                "citation": {"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27",
                             "section_key": "I-2", "period_of_report": "2026-07-27"}}
    h.fake.answer(f"/v1/companies/{NVDA}/filings/{ACC}/changes", {
        "company_id": NVDA, "view": "ranked", "filters": {"q": "tariffs"},
        "sections": [{"section_key": "I-2", "changes": [change(s) for s in (5, 90, 40)]},
                     {"section_key": "II-1A", "changes": [change(s) for s in (70, 10)]}]})
    out = h.data(h.call("get_material_changes", {
        "company_id": NVDA, "accession_no": ACC, "topic": "tariffs", "new_figures": True,
        "max_changes": 3}))
    assert h.fake.seen[-1].params == {"view": ["ranked"], "q": ["tariffs"], "numbers": ["new"]}
    s1, s2 = out["data"]["sections"]
    assert [c["score"] for c in s1["changes"]] == [90, 40] and s1["changes_omitted"] == 1
    assert [c["score"] for c in s2["changes"]] == [70] and s2["changes_omitted"] == 1
    assert out["citations"][0]["section_key"] == "I-2"


def test_formulas_post_the_body_and_answer_newest_first(h):
    h.fake.answer("/v1/formulas/validate", {"valid": True, "unit": "pure", "ast": {"op": "/"}},
                method="POST")
    out = h.data(h.call("validate_formula", {"formula": "net_income / revenue",
                                               "default_company": NVDA, "view": "ttm"}))
    assert h.fake.seen[-1].body == {"formula": "net_income / revenue", "default_company": NVDA,
                                  "basis": "fiscal", "view": "ttm"}
    assert "ast" not in out["data"]
    points = [{"period": f"FY2026 Q{q}", "value": q, "inputs": [{"citation": {
        "accession_no": ACC, "form": "10-Q", "filed": "2026-08-27"}, "period_end": f"p{q}"}]}
        for q in (1, 2, 3)]
    h.fake.answer("/v1/formulas/evaluate", {"formula": "KO.revenue", "points": points},
                method="POST")
    out = h.data(h.call("evaluate_formula", {"formula": "KO.revenue", "from_date": "2025-01-01"}))
    assert h.fake.seen[-1].body == {"formula": "KO.revenue", "basis": "fiscal", "view": "quarter",
                                  "from": "2025-01-01"}
    assert [p["period"] for p in out["data"]["points"]] == ["FY2026 Q3", "FY2026 Q2", "FY2026 Q1"]
    assert {c["period"] for c in out["citations"]} == {"p1", "p2", "p3"}


def test_whoami_never_returns_any_part_of_the_key(h):
    h.fake.answer("/v1/me", {"user_id": "gh-1", "api_key": {
        "key_id": "k1", "name": "laptop", "prefix": KEY[:8], "scopes": ["read"]}})
    out = h.data(h.call("whoami"))
    assert out["data"]["api_key"] == {"key_id": "k1", "name": "laptop", "scopes": ["read"]}


def test_list_metrics_reads_both_public_vocabularies(h):
    h.fake.answer("/v1/metrics", {"metrics": ["revenue"], "derived_metrics": ["gross_margin"]})
    h.fake.answer("/v1/screener/columns", {"columns": [{"key": "revenue_ttm"}], "bands": []})
    out = h.data(h.call("list_metrics"))
    assert out["data"]["metrics"] == ["revenue"]
    assert out["data"]["screener"]["columns"] == [{"key": "revenue_ttm"}]
