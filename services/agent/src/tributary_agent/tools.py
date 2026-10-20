"""Agent tools over the Query API. The user comes from the bearer token, never a tool
argument."""

import json
import re
from typing import Any

import httpx

SECTION_KEY = re.compile(r"(?:[IV]+-)?[0-9]{1,2}[A-C]?(?:\.[0-9]{2})?|EX-99\.[0-9]{1,2}")
METRICS = [
    # income statement
    "revenue", "cost_of_revenue", "gross_profit", "research_and_development", "sga",
    "operating_income", "interest_expense", "pretax_income", "income_tax", "net_income",
    "eps_basic", "eps_diluted", "weighted_shares_basic", "weighted_shares_diluted",
    # balance sheet
    "cash", "short_term_investments", "receivables", "inventory", "current_assets",
    "total_assets", "current_liabilities", "long_term_debt_current", "long_term_debt",
    "total_liabilities", "stockholders_equity", "shares_outstanding",
    # cash flow
    "operating_cash_flow", "capex", "depreciation_amortization", "stock_based_compensation",
    "dividends_paid", "share_buybacks",
]
COMPANY_ID = {"type": "string", "pattern": r"^\d{10}$",
              "description": "10-digit canonical company_id from search_companies or whoami"}

TOOLS: list[dict] = [
    {
        "name": "whoami",
        "description": "The companies the current user can read, with the tiers they can read "
                       "for each (financials, filings).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_companies",
        "description": "Resolve a ticker, company name (current or former) or CIK to a "
                       "company_id. Each result's \"access\" lists the tiers the current "
                       "user can read; it says nothing about other users.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "e.g. NVDA, Alphabet"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_financials",
        "description": "Quarterly financials from XBRL, most recent first. Each row cites its "
                       "filing (accession_no, form, filed, xbrl_tag) and freshness.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "metrics": {"type": "array", "items": {"type": "string", "enum": METRICS},
                            "description": "default: all"},
                "quarters": {"type": "integer", "minimum": 1, "maximum": 40,
                             "description": "default 4"},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_filings",
        "description": "Recent 10-K, 10-Q and 8-K filings, each with the section keys it "
                       "contains and their lengths. Use it to find a section_key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20,
                          "description": "default 8"},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_section",
        "description": 'Text of one filing section, latest filing first. 10-K keys look like '
                       '"1A" (risk factors) or "7" (MD&A), 10-Q keys "I-2" or "II-1A", 8-K '
                       'keys "2.02", and 8-K exhibits "EX-99.1" (usually the earnings press '
                       'release). Text is cut at max_chars; "truncated" says if there is '
                       "more.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "section_key": {"type": "string"},
                "form": {"type": "string", "enum": ["10-K", "10-Q", "8-K"]},
                "accession_no": {"type": "string", "description": "a specific filing"},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000,
                              "description": "default 8000"},
            },
            "required": ["company_id", "section_key"],
            "additionalProperties": False,
        },
    },
]


class QueryApi:
    """Runs tool calls against the Query API as the user whose token this holds."""

    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}"}

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """(tool_result content, is_error). Never raises for API errors: the model is told."""
        try:
            path, params = _route(name, args)
        except (KeyError, ValueError) as e:
            return f"invalid call to {name}: {e}", True
        try:
            r = self._http.get(path, params=params, headers=self._headers)
        except httpx.HTTPError as e:
            return f"Query API unreachable: {type(e).__name__}", True
        if r.status_code == 200:
            return json.dumps(r.json(), separators=(",", ":"), default=str), False
        detail = _detail(r)
        if r.status_code == 403:
            return f"permission denied: {detail}. The user's plan and grants don't allow " \
                   "this; do not answer it from other sources.", True
        return f"Query API error {r.status_code}: {detail}", True


def _route(name: str, a: dict[str, Any]) -> tuple[str, dict]:
    def company() -> str:
        cid = str(a["company_id"])
        if not (cid.isdigit() and len(cid) == 10):
            raise ValueError("company_id must be 10 digits")
        return cid

    if name == "whoami":
        return "/v1/me", {}
    if name == "search_companies":
        return "/v1/companies/search", {"q": a["query"]}
    if name == "get_financials":
        params: dict[str, Any] = {"quarters": a.get("quarters", 4)}
        if a.get("metrics"):
            params["metrics"] = a["metrics"]
        return f"/v1/companies/{company()}/financials", params
    if name == "list_filings":
        return f"/v1/companies/{company()}/filings", {"limit": a.get("limit", 8)}
    if name == "get_section":
        key = str(a["section_key"])
        if not SECTION_KEY.fullmatch(key):
            raise ValueError('section_key looks like "1A", "II-1A", "2.02" or "EX-99.1"')
        params = {"max_chars": min(int(a.get("max_chars", 8000)), 20_000)}
        for k in ("form", "accession_no"):
            if a.get(k):
                params[k] = a[k]
        return f"/v1/companies/{company()}/sections/{key}", params
    raise ValueError(f"unknown tool {name}")


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))
    except ValueError:
        return r.text[:200]
