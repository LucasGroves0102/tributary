"""Agent tools over the Query API. The user comes from the bearer token, never a tool
argument."""

import copy
import json
import re
from typing import Any

import httpx

SECTION_KEY = re.compile(r"(?:[IV]+-)?[0-9]{1,2}[A-C]?(?:\.[0-9]{2})?|EX-99\.[0-9]{1,2}")
ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
COMPANY_ID = {"type": "string", "pattern": r"^\d{10}$",
              "description": "10-digit canonical company_id from search_companies or whoami"}
MAX_CHANGES = 50

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
                       "filing (accession_no, form, filed, xbrl_tag) and freshness, and is "
                       "labelled with its fiscal and calendar quarter. Derived metrics list "
                       "their inputs. To compare companies with different fiscal years, use "
                       "basis=calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                # enum: the Query API's /v1/metrics (with_metrics)
                "metrics": {"type": "array", "items": {"type": "string"},
                            "description": "default: all"},
                "quarters": {"type": "integer", "minimum": 1, "maximum": 40,
                             "description": "default 4"},
                "view": {"type": "string", "enum": ["quarter", "ttm"],
                         "description": "quarter (default), or trailing twelve months ending "
                                        "each quarter"},
                "basis": {"type": "string", "enum": ["fiscal", "calendar"],
                          "description": "fiscal (default): every fiscal quarter; calendar: "
                                         "one row per calendar quarter"},
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
    {
        "name": "get_section_changes",
        "description": "What changed in a 10-K's or 10-Q's Risk Factors and MD&A since the "
                       "comparable earlier filing: paragraphs that are new, removed or "
                       "rewritten (with a word diff: \"-\" old words, \"+\" new words), each "
                       "cited in both filings. Unchanged paragraphs are only counted; changes "
                       "that only update numbers or years count as unchanged. Get the "
                       "accession_no from list_filings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "accession_no": {"type": "string", "pattern": r"^\d{10}-\d{2}-\d{6}$",
                                 "description": "the later filing"},
                "section_key": {"type": "string",
                                "description": '10-K "1A" or "7", 10-Q "II-1A" or "I-2";'
                                               " default: all compared sections"},
                "basis": {"type": "string", "enum": ["prior", "year_ago"],
                          "description": "prior (default): previous 10-K / 10-Q; year_ago: "
                                         "the 10-Q for the same quarter a year earlier"},
                "kind": {"type": "string", "enum": ["new", "removed", "rewritten"]},
                "max_changes": {"type": "integer", "minimum": 1, "maximum": MAX_CHANGES,
                                "description": "default 20"},
            },
            "required": ["company_id", "accession_no"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_filings",
        "description": "Full-text search over the filing sections of the companies the user "
                       "can read filings for: the best passage per section, ranked, with a "
                       "snippet (matched words between « and ») and a citation. Query syntax: "
                       'words, "an exact phrase", or, -excluded.',
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "company_id": COMPANY_ID,
                "form": {"type": "string", "enum": ["10-K", "10-Q", "8-K"]},
                "section_key": {"type": "string"},
                "filed_from": {"type": "string", "description": "YYYY-MM-DD"},
                "filed_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20,
                          "description": "default 10"},
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def with_metrics(metrics: list[str]) -> list[dict]:
    """TOOLS with get_financials' metric names limited to these."""
    tools = copy.deepcopy(TOOLS)
    for tool in tools:
        if tool["name"] == "get_financials":
            tool["input_schema"]["properties"]["metrics"]["items"]["enum"] = metrics
    return tools


class MetricTools:
    """with_metrics(the Query API's metric names), fetched once; plain TOOLS until it answers."""

    def __init__(self) -> None:
        self._tools: list[dict] | None = None

    def get(self, api: "QueryApi") -> list[dict]:
        if self._tools is None:
            names = api.metric_names()
            if not names:
                return TOOLS
            self._tools = with_metrics(names)
        return self._tools


class QueryApi:
    """Runs tool calls against the Query API as the user whose token this holds."""

    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}"}

    def metric_names(self) -> list[str] | None:
        """Every metric get_financials accepts (/v1/metrics); None if the API can't say."""
        try:
            r = self._http.get("/v1/metrics", headers=self._headers)
            r.raise_for_status()
            body = r.json()
            return list(body["metrics"]) + list(body["derived_metrics"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

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
            body = r.json()
            if name == "get_section_changes":
                body = _first_changes(body, int(args.get("max_changes", 20)))
            return json.dumps(body, separators=(",", ":"), default=str), False
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
        for key in ("view", "basis"):
            if a.get(key):
                params[key] = a[key]
        return f"/v1/companies/{company()}/financials", params
    if name == "list_filings":
        return f"/v1/companies/{company()}/filings", {"limit": a.get("limit", 8)}
    if name == "get_section":
        key = section_key(a["section_key"])
        params = {"max_chars": min(int(a.get("max_chars", 8000)), 20_000)}
        for k in ("form", "accession_no"):
            if a.get(k):
                params[k] = a[k]
        return f"/v1/companies/{company()}/sections/{key}", params
    if name == "get_section_changes":
        acc = str(a["accession_no"])
        if not ACCESSION.fullmatch(acc):
            raise ValueError("accession_no looks like 0001045810-26-000021")
        params = {"basis": a.get("basis", "prior")}
        if a.get("section_key"):
            params["section"] = section_key(a["section_key"])
        if a.get("kind"):
            params["kind"] = a["kind"]
        return f"/v1/companies/{company()}/filings/{acc}/changes", params
    if name == "search_filings":
        params = {"q": str(a["query"]), "limit": min(int(a.get("limit", 10)), 20),
                  "offset": int(a.get("offset", 0))}
        if a.get("company_id"):
            params["company"] = company()
        if a.get("form"):
            params["form"] = a["form"]
        if a.get("section_key"):
            params["section"] = section_key(a["section_key"])
        for arg, param in (("filed_from", "from"), ("filed_to", "to")):
            if a.get(arg):
                if not DATE.fullmatch(str(a[arg])):
                    raise ValueError(f"{arg} must be YYYY-MM-DD")
                params[param] = a[arg]
        return "/v1/search", params
    raise ValueError(f"unknown tool {name}")


def section_key(value: Any) -> str:
    key = str(value)
    if not SECTION_KEY.fullmatch(key):
        raise ValueError('section_key looks like "1A", "II-1A", "2.02" or "EX-99.1"')
    return key


def _first_changes(body: dict, limit: int) -> dict:
    """Keep the first `limit` changes across sections (reading order), saying how many more
    there are, so a long MD&A diff doesn't flood the context."""
    left = max(1, min(limit, MAX_CHANGES))
    for s in body.get("sections") or []:
        changes = s.get("changes") or []
        s["changes"], left = changes[:left], max(0, left - len(changes))
        if len(changes) > len(s["changes"]):
            s["changes_omitted"] = len(changes) - len(s["changes"])
    return body


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))
    except ValueError:
        return r.text[:200]
