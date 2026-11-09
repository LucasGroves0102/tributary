"""Filing alerts' rules (query_api/alerts.py) and the routes that need no database: which 8-Ks
are material, the key figures read from a recap, red lines (formula checks and evaluation),
what a notification shows without access, the webhook form refusing private and plain-http
URLs before anything is stored. test_alerts_db.py runs the tables, their RLS, the routes end to
end and the job."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.alerts import (
    check_formula,
    crossed,
    evaluate,
    item_info,
    key_figures,
    material,
    message,
    notification_out,
    plain_value,
)
from query_api.app import create_app

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

NVDA = "0001045810"
ACC = "0001045810-26-000101"
OLD = "0001045810-26-000075"


def recap_row(metric, comparison, value, span="quarter", unit="USD", acc=ACC,
              end=date(2026, 10, 26)):
    return {"comparison": comparison, "metric": metric, "span": span, "value": Decimal(value),
            "unit": unit, "period_end": end, "accession_no": acc, "source_accessions": [acc],
            "not_meaningful": None}


def test_material_8k_items():
    assert material(["2.02", "9.01"]) is True
    assert material(["5.07"]) is False
    assert material(["5.07", "9.01"]) is False
    assert material(["5.02"]) is True
    assert material(None) is None and material([]) is None
    assert item_info(["2.02"])[0] == {"item": "2.02", "kind": "earnings",
                                      "title": "Results of Operations and Financial Condition"}


def test_key_figures_wait_until_gold_cites_the_filing():
    older = [recap_row("revenue", "current", "30000000000", acc=OLD)]
    assert key_figures("10-Q", older, ACC) is None


def test_key_figures_for_a_10q():
    rows = [recap_row("revenue", "current", "57006000000"),
            recap_row("revenue", "year_ago", "35082000000", acc=OLD, end=date(2025, 10, 26)),
            recap_row("gross_margin", "current", "0.735", unit="ratio"),
            recap_row("gross_margin", "year_ago", "0.745", unit="ratio", acc=OLD),
            recap_row("operating_margin", "current", "0.63", unit="ratio"),
            recap_row("eps_diluted", "current", "1.30", unit="USD/shares"),
            recap_row("revenue", "current", "187000000000", span="ttm")]
    figures = key_figures("10-Q", rows, ACC)
    assert [f["metric"] for f in figures] == ["revenue", "gross_margin", "eps_diluted"]
    rev = figures[0]
    assert rev["value"] == "57006000000" and rev["pct_change"] == "0.6249"
    gm = figures[1]
    assert gm["change"] == "-0.010" and gm["pct_change"] is None  # ratios change in points


def test_key_figures_for_a_10k_read_the_year():
    rows = [recap_row("revenue", "current", "130497000000", span="ttm"),
            recap_row("operating_margin", "current", "0.62", span="ttm", unit="ratio"),
            recap_row("net_income", "current", "72880000000", span="ttm"),
            recap_row("eps_diluted", "current", "0.89", unit="USD/shares"),
            recap_row("revenue", "current", "39331000000")]
    figures = key_figures("10-K", rows, ACC)
    assert [(f["metric"], f["span"]) for f in figures] == [
        ("revenue", "ttm"), ("operating_margin", "ttm"), ("net_income", "ttm")]


@pytest.mark.parametrize("formula, view, problem", [
    ("gross_margin", "ttm", None),
    ("receivables / receivables[-4q] - revenue / revenue[-4q]", "quarter", None),
    ("NVDA.gross_margin", "ttm", "leave out the ticker"),
    ("gross_margn", "ttm", "Unknown metric"),
    ("eps_diluted", "ttm", "no trailing-twelve-month values"),
    ("1 + 2", "ttm", "at least one metric"),
    ("gross_margin; DROP TABLE red_lines", "ttm", ""),
])
def test_red_line_formulas_are_checked_like_chart_formulas(formula, view, problem):
    got = check_formula(formula, view)
    if problem is None:
        assert got is None
    else:
        assert got is not None and problem in got


def rule(op, threshold, formula="gross_margin"):
    return {"rule_id": "r1", "label": "GM floor", "formula": formula, "view": "ttm", "op": op,
            "threshold": Decimal(threshold)}


POINTS = [{"period_end": date(2026, 7, 27), "value": Decimal("0.72")},
          {"period_end": date(2026, 10, 26), "value": Decimal("0.735")}]


def test_a_red_line_at_the_filings_period():
    out = evaluate(rule("<", "0.75"), POINTS, date(2026, 10, 25))
    assert (out["status"], out["crossed"], out["value"], out["period_end"]) == (
        "ok", True, "0.735", "2026-10-26")
    assert evaluate(rule("<", "0.70"), POINTS, date(2026, 10, 26))["crossed"] is False
    # no point at the filing's period: no value, never another quarter's
    assert evaluate(rule("<", "0.75"), POINTS, date(2027, 1, 25))["status"] == "no_value"
    # on demand: the latest point
    assert evaluate(rule(">=", "0.735"), POINTS, None)["crossed"] is True
    assert evaluate(rule("<", "1"), [], None)["status"] == "no_value"


def test_amounts_in_another_currency_say_it():
    """057: a euro or yuan filer's figures in a message are in their currency, never "$" and
    never a bare number; a red line on an amount says its currency, a ratio's says none."""
    assert plain_value("2097429000", "EUR") == "€2.1B"
    assert plain_value("996347000000", "CNY") == "CNY 996.3B"
    assert plain_value("1.22", "EUR/shares") == "€1.22 a share"
    assert plain_value("120.5", "JPY/shares") == "¥120.50 a share"
    assert plain_value("0.735", "ratio") == "73.5%"
    yuan = [{"period_end": date(2026, 9, 30), "value": Decimal("996347000000"),
             "inputs": [{"input": 0, "unit": "CNY"}]}]
    out = evaluate(rule("<", "1000000000000", formula="revenue"), yuan, None)
    assert out["unit"] == "CNY" and out["crossed"] is True
    text = message(notification(detail={"figures": [], "red_lines": [
        {**out, "label": "Revenue floor"}]}), "Alibaba", {})
    assert "Red line Revenue floor: CROSSED (CNY 996.3B)" in text
    assert "unit" not in evaluate(rule("<", "0.75"), POINTS, None)  # a ratio: no currency
    dollars = [{**yuan[0], "inputs": [{"input": 0, "unit": "USD"}]}]
    assert evaluate(rule("<", "1", formula="revenue"), dollars, None)["unit"] == "USD"


def test_crossed_operators():
    assert crossed(Decimal(1), "<", Decimal(2)) and crossed(Decimal(2), "<=", Decimal(2))
    assert crossed(Decimal(3), ">", Decimal(2)) and crossed(Decimal(2), ">=", Decimal(2))
    assert not crossed(Decimal(2), ">", Decimal(2))


def notification(**kw):
    n = {"notification_id": "n1", "company_id": NVDA, "accession_no": ACC, "form": "10-Q",
         "filed": date(2026, 11, 19), "period_end": date(2026, 10, 26), "items": None,
         "sources": ["watchlist"], "status": "ready",
         "detail": {"figures": [{"metric": "revenue", "label": "Revenue", "value": "1",
                                 "unit": "USD", "pct_change": "0.5", "change": "1"}],
                    "figures_status": "ready",
                    "red_lines": [{**evaluate(rule("<", "0.75"), POINTS, None)}]},
         "created_at": datetime(2026, 11, 19, tzinfo=UTC), "ready_at": None, "read_at": None}
    return {**n, **kw}


def test_figures_are_stripped_once_the_company_isnt_readable():
    shown = notification_out(notification(), can_read_financials=True)
    assert shown["figures"] and shown["crossed"] == 1
    hidden = notification_out(notification(), can_read_financials=False)
    assert hidden["figures"] is None and hidden["red_lines"] == []
    assert hidden["figures_status"] == "no_access" and hidden["crossed"] == 0


def test_the_channel_message():
    text = message(notification(), "NVIDIA (NVDA)", {"Open": "https://x/#/company/1"})
    assert text.splitlines()[0] == ("NVIDIA (NVDA) filed a 10-Q on 2026-11-19"
                                    " (period ended 2026-10-26)")
    assert "Revenue: $1 (+50.0% on a year earlier)" in text
    assert "Red line GM floor: CROSSED (73.5%)" in text
    eight_k = message(notification(form="8-K", items=["5.02"], detail={}), "NVIDIA", {})
    assert "Items: 5.02 Departure of Directors" in eight_k
    assert plain_value("57006000000", "USD") == "$57.0B"
    assert plain_value("1.3", "USD/shares") == "$1.30 a share"


# --- routes without a database ---------------------------------------------------------------

def client(alerts=None):
    app = create_app(FakeAuthorizer({}), FakeStore(), SECRET, alerts=alerts,
                     webhook_resolver=lambda *_a, **_k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    return TestClient(app)


@pytest.mark.parametrize("url, code", [
    ("http://169.254.169.254", "scheme"),
    ("http://169.254.169.254/latest/meta-data/iam/security-credentials/", "scheme"),
    ("https://169.254.169.254/latest/meta-data/", "private_address"),
    ("https://[::ffff:169.254.169.254]/", "private_address"),
    ("https://localhost/hook", "private_address"),
    ("https://hooks.example.com/x", "private_address"),  # resolves to 10.0.0.5 here
    ("https://hooks.example.com:8080/x", "port"),
])
def test_webhook_form_refuses_private_and_plain_http_urls(token, url, code):
    # refused before anything is stored: with no app database a valid URL would be a 503
    r = client().post("/v1/alerts/webhooks", headers=token("alice"), json={"url": url})
    assert r.status_code == 422 and r.json()["code"] == code
    assert url not in r.text


def test_webhook_body_is_parsed_by_hand_and_never_echoed(token):
    url = "https://discord.com/api/webhooks/1/" + "s" * 40
    r = client().post("/v1/alerts/webhooks", headers=token("alice"),
                      json={"url": url, "extra": 1})
    assert r.status_code == 422 and "s" * 40 not in r.text
    r = client().post("/v1/alerts/webhooks", content=b"x" * 5000,
                      headers={**token("alice"), "Content-Type": "application/json"})
    assert r.status_code == 413


def test_alert_routes_need_a_token_and_the_app_database(token):
    c = client()
    assert c.get("/v1/alerts/unread").status_code == 401
    assert c.get("/v1/alerts/unread", headers=token("alice")).status_code == 503
    assert c.get("/v1/alerts/settings", headers=token("alice")).status_code == 503


def test_red_line_bodies_are_strict(token):
    c = client()
    bad = {"company_id": NVDA, "label": "x", "formula": "gross_margin", "op": "<",
           "threshold": "0.5", "user_id": "bob"}
    assert c.post("/v1/alerts/red-lines", headers=token("alice"), json=bad).status_code == 422
    nan = {**{k: v for k, v in bad.items() if k != "user_id"}, "threshold": "NaN"}
    assert c.post("/v1/alerts/red-lines", headers=token("alice"), json=nan).status_code == 422
    ticker = {**nan, "threshold": "0.5", "formula": "MSFT.gross_margin"}
    r = c.post("/v1/alerts/red-lines", headers=token("alice"), json=ticker)
    assert r.status_code == 422 and "ticker" in r.json()["detail"]


def test_a_demo_sandbox_cant_add_a_webhook(token):
    # anonymous day-long users don't get to make the server post to a URL of their choosing
    r = client().post("/v1/alerts/webhooks", headers=token("demo-0123456789abcdef"),
                      json={"url": "https://discord.com/api/webhooks/1/x"})
    assert r.status_code == 403 and r.json()["code"] == "permanent_account"


@pytest.mark.parametrize("field, value", [("label", "GM\x01 floor"), ("label", "GM\x85"),
                                          ("formula", "gross_margin\x1c"),
                                          ("formula", "gross_margin\u3000")])
def test_red_lines_refuse_control_characters(token, field, value):
    body = {"company_id": NVDA, "label": "GM floor", "formula": "gross_margin", "op": "<",
            "threshold": "0.5", field: value}
    assert client().post("/v1/alerts/red-lines", headers=token("alice"), json=body).status_code \
        == 422


def test_webhook_attempts_are_rate_limited_before_any_lookup(token):
    c = client()  # the resolver answers a private address: each attempt is a refusal
    codes = [c.post("/v1/alerts/webhooks", headers=token("alice"),
                    json={"url": "https://hooks.example.com/x"}).json()["code"]
             for _ in range(21)]
    assert codes[:20] == ["private_address"] * 20 and codes[20] == "rate_limited"
    # what needs no lookup is still refused for what it is
    r = c.post("/v1/alerts/webhooks", headers=token("alice"), json={"url": "http://x.example.com"})
    assert r.json()["code"] == "scheme"
