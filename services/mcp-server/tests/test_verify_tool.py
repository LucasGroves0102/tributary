"""verify_figures: the agent's verify.py, unchanged, over evidence read as the key's user."""

import ast
import sys

import tributary_agent.verify as agent_verify

KEY = "trb_" + "A" * 43
NVDA = "0001045810"
AMD = "0000002488"
ACC = "0001045810-26-000021"
YEAR_AGO = "0001045810-25-000209"
TEN_K = "0001045810-26-000003"


def row(metric: str, end: str, value: float, acc: str, view: str = "quarter") -> dict:
    return {"metric": metric, "period_end": end, "value": value, "unit": "USD",
            "citation": {"accession_no": acc, "form": "10-Q", "filed": "2026-08-27"}}


def answer(view: str) -> dict:
    rows = [row("revenue", "2026-07-27", 46_743_000_000, ACC),
            row("revenue", "2025-07-27", 30_040_000_000, YEAR_AGO)] if view == "quarter" else \
        [row("revenue", "2026-07-27", 165_218_000_000, ACC)]
    return {"company_id": NVDA, "view": view, "basis": "fiscal", "rows": rows}


def evidence(h) -> None:
    h.fake.answer(f"/v1/companies/{NVDA}/financials",
                  lambda r: answer(r.url.params.get("view", "quarter")))
    h.fake.answer(f"/v1/companies/{NVDA}/annual-per-share", {"company_id": NVDA, "rows": [
        {"metric": "eps_diluted", "period_end": "2026-01-25", "value": 2.94, "unit": "USD/shares",
         "citation": {"accession_no": TEN_K, "form": "10-K", "filed": "2026-02-25"}}]})


def test_figures_are_verified_derived_or_unverified_with_their_sources(h):
    evidence(h)
    text = (f"NVIDIA's revenue was $46.7B in the quarter ({ACC}), up $16.7B from a year "
            f"earlier; TTM revenue $165.2 billion. Diluted EPS for the year was $2.94. "
            f"It also made $12.3B in widgets (0000000000-26-000001).")
    out = h.data(h.call("verify_figures", {"text": text, "company_ids": [NVDA],
                                           "metrics": ["revenue"], "quarters": 4}))
    d = out["data"]
    by_text = {f["text"]: f for f in d["figures"]}
    assert by_text["$46.7B"]["status"] == "verified"
    assert by_text["$46.7B"]["source"] == {"company_id": NVDA, "metric": "revenue",
                                           "period_end": "2026-07-27", "accession_no": ACC,
                                           "form": "10-Q", "filed": "2026-08-27"}
    assert by_text["$16.7B"]["status"] == "derived"
    assert by_text["$165.2 billion"]["status"] == "verified"
    assert by_text["$2.94"]["status"] == "verified"
    assert by_text["$12.3B"]["status"] == "unverified" and "source" not in by_text["$12.3B"]
    assert d["unverified_accessions"] == ["0000000000-26-000001"]
    assert d["all_verified"] is False
    assert d["counts"] == {"verified": 3, "derived": 1, "quoted": 0, "unverified": 1}
    assert {c["accession_no"] for c in out["citations"]} == {ACC, YEAR_AGO, TEN_K}
    # the reads: quarters and TTM for the metrics asked, annual per share, as the caller
    reads = [(s.path, s.params.get("view"), s.headers["authorization"]) for s in h.fake.seen]
    assert sorted(reads, key=str) == sorted([
        (f"/v1/companies/{NVDA}/financials", ["quarter"], f"Bearer {KEY}"),
        (f"/v1/companies/{NVDA}/financials", ["ttm"], f"Bearer {KEY}"),
        (f"/v1/companies/{NVDA}/annual-per-share", None, f"Bearer {KEY}")], key=str)
    fin = next(s for s in h.fake.seen if s.path.endswith("/financials"))
    assert fin.params["metrics"] == ["revenue"] and fin.params["quarters"] == ["4"]
    assert "readings" not in fin.params  # the JSON the agent's verify.py was written for


def test_a_figure_quoted_from_a_named_section_is_quoted(h):
    evidence(h)
    h.fake.answer(f"/v1/companies/{NVDA}/sections/7", {"company_id": NVDA, "sections": [{
        "section_key": "7", "text": "Data Center revenue was $41.1 billion, up 56%.",
        "citation": {"accession_no": ACC, "form": "10-Q", "filed": "2026-08-27",
                     "section_key": "7"}}]})
    out = h.data(h.call("verify_figures", {
        "text": "Data Center revenue was $41.1 billion.", "company_ids": [NVDA],
        "sections": [{"company_id": NVDA, "section_key": "7", "accession_no": ACC}]}))
    assert out["data"]["figures"] == [{"text": "$41.1 billion", "value": 41.1e9,
                                       "status": "quoted"}]
    assert out["data"]["all_verified"] is True
    sec = next(s for s in h.fake.seen if "/sections/" in s.path)
    assert sec.params == {"accession_no": [ACC], "limit": ["1"], "max_chars": ["20000"]}


def test_a_company_the_user_cant_read_is_noted_not_used(h):
    evidence(h)
    for path in ("financials", "annual-per-share"):
        h.fake.answer(f"/v1/companies/{AMD}/{path}", {"detail": "no access"}, status=403)
    out = h.data(h.call("verify_figures", {"text": "AMD made $7.7B.",
                                           "company_ids": [NVDA, AMD]}))
    assert out["data"]["figures"][0]["status"] == "unverified"
    not_read = out["data"]["checked_against"]["not_read"]
    assert len(not_read) == 3 and all("Permission denied" in n["error"] for n in not_read)


def test_when_nothing_can_be_read_the_refusal_is_the_answer(h):
    h.fake.answer(f"/v1/companies/{NVDA}/financials", {"detail": "invalid token"}, status=401)
    h.fake.answer(f"/v1/companies/{NVDA}/annual-per-share", {"detail": "invalid token"},
                  status=401)
    text = h.error_text(h.call("verify_figures", {"text": "$1B", "company_ids": [NVDA]}))
    assert "refused the API key" in text


def test_no_dollar_figures_says_so(h):
    evidence(h)
    out = h.data(h.call("verify_figures", {"text": "Revenue grew strongly.",
                                           "company_ids": [NVDA]}))
    assert out["data"]["figures"] == [] and "No dollar figures" in out["data"]["note"]


def test_verify_py_needs_only_the_standard_library():
    """The image installs the agent package with --no-deps for this module alone."""
    tree = ast.parse(open(agent_verify.__file__ or "", encoding="utf-8").read())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom) and n.level == 0}
    assert names and all(n in sys.stdlib_module_names for n in names), names
