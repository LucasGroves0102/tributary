"""Agent tools over the Query API. The user comes from the bearer token, never a tool
argument."""

import copy
import difflib
import json
import re
from typing import Any, Protocol

import httpx

SECTION_KEY = re.compile(r"(?:[IV]+-)?[0-9]{1,2}[A-C]?(?:\.[0-9]{2})?|EX-99\.[0-9]{1,2}")
ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
COMPANY_ID = {"type": "string", "pattern": r"^\d{10}$",
              "description": "10-digit canonical company_id from search_companies or whoami"}
MAX_CHANGES = 50
MATERIAL_CHANGES = 15  # get_material_changes' default: the top of the ranked list
TOPIC_CHARS = 100  # the Query API's limit for a topic (q)
MAX_QUARTERS = 40  # get_financials' schema maximum, also enforced in _route
MAX_RELEASE_QUARTERS = 4  # get_earnings_release's schema maximum, enforced in _route too
SEARCH_MAX_OFFSET = 1000  # search_filings' schema maximum, enforced in _route too
# get_earnings_release: quoted adjustments (reconciliation tables) kept whole up to this many
# characters per quarter; the rest keep their heading and citation (get_section reads them)
ADJUSTMENT_CHARS = 6000
# find_companies: rows by default and at most (the schema's, enforced in _route too)
FIND_ROWS, MAX_FIND_ROWS = 15, 25
# the Query API's /v1/screener limits for the grouping's names (ui.py: at most 12 and 20)
MAX_SECTORS, MAX_INDUSTRIES = 12, 20
SECTOR_CHARS, INDUSTRY_CHARS = 40, 60
# the screener columns find_companies reads: what it shows, and total assets so a company
# before revenue (Terrestrial Energy, Deep Fission) still has a figure and is listed
FIND_COLUMNS = ["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm", "total_assets_q"]
FIND_SORTS = ("-revenue_ttm", "revenue_ttm", "-revenue_growth_ttm", "-operating_margin_ttm",
              "-total_assets_q", "name")
BANDS = ("lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b")

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
                "quarters": {"type": "integer", "minimum": 1, "maximum": MAX_QUARTERS,
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
        "name": "get_material_changes",
        "description": "What changed in a 10-K's or 10-Q's Risk Factors and MD&A, read for "
                       "materiality and ranked: a 10-Q against the same quarter a year "
                       "earlier by default (basis prior: the previous 10-Q), a 10-K against "
                       "the previous 10-K. Paragraphs only split or rejoined, calendar "
                       "rollovers (\"second\" to \"third\" quarter, dates) and figure-only "
                       "table rows are left out and counted (\"hidden\"). Each change has its "
                       "period (three, six or nine months), the figures it adds or drops, a "
                       "score and why it ranks there, and is cited in both filings; "
                       "\"comparisons\" names the filings it can be compared with. Filter by "
                       "topic (every word must appear) or to passages with new figures. Get "
                       "the accession_no from list_filings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "accession_no": {"type": "string", "pattern": r"^\d{10}-\d{2}-\d{6}$",
                                 "description": "the later filing"},
                "basis": {"type": "string", "enum": ["prior", "year_ago"],
                          "description": "default: year_ago for a 10-Q (prior when there is "
                                         "none), prior for a 10-K"},
                "section_key": {"type": "string",
                                "description": '10-K "1A" or "7", 10-Q "II-1A" or "I-2";'
                                               " default: all compared sections"},
                "topic": {"type": "string",
                          "description": 'words that must all appear, e.g. tariffs or '
                                         '"credit facility"'},
                "new_figures": {"type": "boolean",
                                "description": "only passages stating a figure the earlier "
                                               "one doesn't"},
                "max_changes": {"type": "integer", "minimum": 1, "maximum": MAX_CHANGES,
                                "description": f"default {MATERIAL_CHANGES}, highest score "
                                               "first"},
            },
            "required": ["company_id", "accession_no"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_earnings_release",
        "description": "A company's quarterly earnings releases (8-K Item 2.02), newest quarter "
                       "first: the release (accession_no, its exhibit's section_key: read it "
                       "whole with get_section), its headline, the outlook or guidance it "
                       "states and management's adjustments (non-GAAP reconciliation tables, "
                       "notes with dollar amounts), each quoted with a citation and its "
                       "quarter. Passages come from the release's own Outlook/Guidance and "
                       "reconciliation headings: none means the release has none. There are "
                       "no consensus estimates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": COMPANY_ID,
                "quarters": {"type": "integer", "minimum": 1, "maximum": MAX_RELEASE_QUARTERS,
                             "description": "default 1 (the latest)"},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_events_since_report",
        "description": "Form 8-K filings since the company's latest 10-Q or 10-K period end, "
                       "newest first: deals and acquisitions, new debt, guidance updates, "
                       "leadership changes, impairments, non-reliance, the earnings release. "
                       "Each has its items (e.g. 2.01 acquisition completed, 2.03 new debt), "
                       "event date and an excerpt of the 8-K. \"stale\" lists the "
                       "balance-sheet figures at that period end an event after it may have "
                       "changed (a flag: the filings don't give the figure after the event).",
        "input_schema": {
            "type": "object",
            "properties": {"company_id": COMPANY_ID},
            "required": ["company_id"],
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
                "offset": {"type": "integer", "minimum": 0, "maximum": SEARCH_MAX_OFFSET},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        # added after the recorded suites (they never call it); enums: with_sectors
        "name": "find_companies",
        "description": "Lists companies by industry: Tributary's sectors and industries (its own "
                       "grouping of the SEC's SIC codes, not GICS), largest TTM revenue first. "
                       "Use it, before search_filings, for \"which companies are in X\", \"list "
                       "X companies\" or \"the biggest X companies\": search_filings finds "
                       "passages that mention words, not an industry's companies. Each row: "
                       "company_id, name, tickers, sector, industry, the SIC code and its SEC "
                       "label, TTM revenue, revenue growth and operating margin (fractions) and "
                       "total assets, with the currency and any reading (old, n/m); a value is "
                       "null where the user can't read the company's financials or it reports "
                       "none. The grouping is coarse: a SIC code is the filer's own and a theme "
                       "can span several industries (renewables: Independent power & "
                       "renewables, Electric utilities, Electrical equipment, Semiconductors; "
                       "uranium miners file under Metals & mining), so ask for each and check "
                       "what a company does (get_section, its 10-K's Item 1) before you put it "
                       "in a theme. The figures are for finding and ranking: read one you state "
                       "with get_financials, which cites its filing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sectors": {"type": "array", "items": {"type": "string"},
                            "maxItems": MAX_SECTORS,
                            "description": "sector names, e.g. Utilities: the whole sector "
                                           "unless industries names some of its industries"},
                "industries": {"type": "array", "items": {"type": "string"},
                               "maxItems": MAX_INDUSTRIES,
                               "description": "industry names, e.g. Independent power & "
                                              "renewables"},
                "name": {"type": "string", "description": "name contains, or an exact ticker"},
                "revenue_band": {"type": "string", "enum": list(BANDS),
                                 "description": "TTM revenue in US dollars"},
                "sort": {"type": "string", "enum": list(FIND_SORTS),
                         "description": "default -revenue_ttm (largest first); - for "
                                        "descending"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_FIND_ROWS,
                          "description": f"default {FIND_ROWS}"},
            },
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


def with_sectors(tools: list[dict], grouping: dict[str, list[str]]) -> list[dict]:
    """`tools` with find_companies' sector and industry names limited to the grouping's."""
    tools = copy.deepcopy(tools)
    for tool in tools:
        if tool["name"] == "find_companies":
            props = tool["input_schema"]["properties"]
            props["sectors"]["items"]["enum"] = list(grouping)
            props["industries"]["items"]["enum"] = [i for s in grouping.values() for i in s]
    return tools


class Calls(Protocol):
    """QueryApi.call: one Query API request as the token's user."""

    def call(self, method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None) -> httpx.Response | None: ...


class SectorCatalog:
    """Tributary's sector grouping (/v1/sectors: sector name -> its industries' names), read
    once and kept: a classification, it changes only with a migration (the Query API reads it
    once a process too). None until the Query API answers; never hardcoded."""

    def __init__(self) -> None:
        self._grouping: dict[str, list[str]] | None = None

    def get(self, api: Calls) -> dict[str, list[str]] | None:
        if self._grouping is None:
            r = api.call("GET", "/v1/sectors")
            self._grouping = _grouping(r) if r is not None and r.status_code == 200 else None
        return self._grouping


def _grouping(r: httpx.Response) -> dict[str, list[str]] | None:
    try:
        body = r.json()
    except ValueError:
        return None
    out: dict[str, list[str]] = {}
    for s in (body.get("sectors") if isinstance(body, dict) else None) or []:
        if isinstance(s, dict) and isinstance(s.get("name"), str):
            out[s["name"]] = [i["name"] for i in s.get("industries") or []
                              if isinstance(i, dict) and isinstance(i.get("name"), str)]
    return out or None


def check_names(grouping: dict[str, list[str]] | None, sectors: list[str],
                industries: list[str]) -> str | None:
    """None when every name is one of the grouping's (or the grouping isn't known: the Query
    API then decides); else what's wrong, with the closest valid names, or every one when
    none is close (a theme, "uranium", is no industry's name)."""
    if grouping is None:
        return None
    every = [i for s in grouping.values() for i in s]
    problems = []
    for kind, names, valid in (("sector", sectors, list(grouping)), ("industry", industries,
                                                                     every)):
        for name in names:
            if name in valid:
                continue
            close = difflib.get_close_matches(name, valid, n=4, cutoff=0.5) or [
                v for v in valid if name.lower() in v.lower()][:4]
            problems.append(f"unknown {kind} {name!r}" + (
                f"; closest: {', '.join(close)}" if close else ""))
    if not problems:
        return None
    if all("closest" in p for p in problems):
        return "; ".join(problems)
    listing = "; ".join(f"{s}: {', '.join(i)}" for s, i in grouping.items())
    return "; ".join(problems) + f". The sectors and their industries: {listing}"


class MetricTools:
    """with_metrics(the Query API's metric names), fetched once; plain TOOLS until it answers.
    Given a SectorCatalog, find_companies' names too once it has them (with_sectors)."""

    def __init__(self, sectors: SectorCatalog | None = None) -> None:
        self._tools: list[dict] | None = None
        self._sectors = sectors
        self._sectored: tuple[list[dict], list[dict]] | None = None  # (base, with names)

    def get(self, api: "QueryApi") -> list[dict]:
        if self._tools is None:
            names = api.metric_names()
            if names:
                self._tools = with_metrics(names)
        base = self._tools if self._tools is not None else TOOLS
        grouping = self._sectors.get(api) if self._sectors is not None else None
        if grouping is None:
            return base
        if self._sectored is None or self._sectored[0] is not base:
            self._sectored = (base, with_sectors(base, grouping))
        return self._sectored[1]


class QueryApi:
    """Runs tool calls against the Query API as the user whose token this holds."""

    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None,
                 sectors: SectorCatalog | None = None) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}"}
        self.sectors = sectors or SectorCatalog()

    def metric_names(self) -> list[str] | None:
        """Every metric get_financials accepts (/v1/metrics); None if the API can't say."""
        try:
            r = self._http.get("/v1/metrics", headers=self._headers)
            r.raise_for_status()
            body = r.json()
            return list(body["metrics"]) + list(body["derived_metrics"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def call(self, method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None) -> httpx.Response | None:
        """One request as the token's user (ui.UiActions' checks); None if unreachable.
        Callers pass fixed paths only."""
        try:
            return self._http.request(method, path, params=params, json=json_body,
                                      headers=self._headers)
        except httpx.HTTPError:
            return None

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """(tool_result content, is_error). Never raises for API errors: the model is told."""
        try:
            path, params = _route(name, args)
            max_changes = (_whole(args, "max_changes", 20, MAX_CHANGES)
                           if name == "get_section_changes" else 0)
            if name == "find_companies":
                wrong = check_names(self.sectors.get(self), params.get("sector", []),
                                    params.get("sector_industry", []))
                if wrong:
                    raise ValueError(wrong)
        except (KeyError, ValueError) as e:
            return f"invalid call to {name}: {e}", True
        try:
            r = self._http.get(path, params=params, headers=self._headers)
        except httpx.HTTPError as e:
            return f"Query API unreachable: {type(e).__name__}", True
        if r.status_code == 200:
            body = r.json()
            if name == "get_section_changes":
                body = _first_changes(body, max_changes)
            if name == "get_material_changes":
                body = _top_changes(body, args.get("max_changes", MATERIAL_CHANGES))
            if name == "get_earnings_release":
                body = _short_adjustments(body)
            if name == "get_financials":
                body = self._no_figures(body, path, params)
            if name == "find_companies":
                body = _companies(body)
            return json.dumps(body, separators=(",", ":"), default=str), False
        detail = _detail(r)
        if r.status_code == 403:
            return f"permission denied: {detail}. The user's plan and grants don't allow " \
                   "this; do not answer it from other sources.", True
        return f"Query API error {r.status_code}: {detail}", True

    def _no_figures(self, body: Any, path: str, params: dict) -> Any:
        """Says plainly which asked-for metrics have no figures at all, when the company has
        others (a company before revenue has no revenue rows but net income, cash, assets):
        an empty answer for revenue read as "no data in Tributary" (Lucas, Terrestrial Energy).
        Names only: the other metrics' values aren't added (they are asked for, then cited)."""
        asked = [m for m in dict.fromkeys(params.get("metrics") or []) if isinstance(m, str)]
        if not asked or not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            return body
        have = {r.get("metric") for r in body["rows"] if isinstance(r, dict)}
        missing = [m for m in asked if m not in have]
        if not missing:
            return body
        others: list[str] = []
        if not have:  # nothing for what was asked: does the company have other figures?
            probe = {k: v for k, v in params.items() if k != "metrics"} | {"quarters": 1}
            try:
                r = self._http.get(path, params=probe, headers=self._headers)
                rows = r.json().get("rows") if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError, AttributeError):
                rows = None
            others = sorted({str(x["metric"]) for x in rows or []
                             if isinstance(x, dict) and x.get("metric")})
            if not others:
                return body  # no figures at all: the empty answer says that
        what = " or ".join(m.replace("_", " ") for m in missing)
        note = (f"Tributary has no {what} figures for this company: its filings may report "
                "none yet (a company before revenue reports no revenue), or the figures "
                "couldn't be read. It is not missing data for the company: ")
        note += ("the other metrics returned are its data." if have else
                 "it has figures for " + ", ".join(others) + "; ask get_financials for them.")
        body["no_figures"] = {"metrics": missing, "note": note}
        return body


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
        # the schema's 1-40, enforced here: the Query API takes up to 120 (the web's Max), and
        # every metric over 120 quarters is megabytes in the model's context
        params: dict[str, Any] = {"quarters": _quarters(a.get("quarters", 4))}
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
        params = {"max_chars": _whole(a, "max_chars", 8000, 20_000)}
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
    if name == "get_material_changes":
        acc = str(a["accession_no"])
        if not ACCESSION.fullmatch(acc):
            raise ValueError("accession_no looks like 0001045810-26-000021")
        params = {"view": "ranked"}
        if a.get("basis") in ("prior", "year_ago"):
            params["basis"] = a["basis"]
        elif a.get("basis"):
            raise ValueError('basis is "prior" or "year_ago"')
        if a.get("section_key"):
            params["section"] = section_key(a["section_key"])
        topic = " ".join(str(a.get("topic") or "").split())
        if topic:
            if not 2 <= len(topic) <= TOPIC_CHARS:
                raise ValueError(f"topic must be 2 to {TOPIC_CHARS} characters")
            params["q"] = topic
        if a.get("new_figures") is True:
            params["numbers"] = "new"
        return f"/v1/companies/{company()}/filings/{acc}/changes", params
    if name == "get_earnings_release":
        try:
            n = int(a.get("quarters", 1))
        except (TypeError, ValueError):
            raise ValueError(f"quarters must be 1 to {MAX_RELEASE_QUARTERS}") from None
        return (f"/v1/companies/{company()}/earnings",
                {"quarters": max(1, min(n, MAX_RELEASE_QUARTERS))})
    if name == "get_events_since_report":
        return f"/v1/companies/{company()}/events", {}
    if name == "search_filings":
        params = {"q": str(a["query"]), "limit": _whole(a, "limit", 10, 20),
                  # the schema's 0-1000: the model is told 1000, so the call never asks for more
                  "offset": max(0, _whole(a, "offset", 0, SEARCH_MAX_OFFSET))}
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
    if name == "find_companies":
        params = {"columns": FIND_COLUMNS, "limit": max(1, _whole(a, "limit", FIND_ROWS,
                                                                  MAX_FIND_ROWS)), "offset": 0}
        sort = a.get("sort") or "-revenue_ttm"
        if sort not in FIND_SORTS:
            raise ValueError(f"sort is one of {', '.join(FIND_SORTS)}")
        params["sort"] = sort
        for arg, param, top, chars in (("sectors", "sector", MAX_SECTORS, SECTOR_CHARS),
                                       ("industries", "sector_industry", MAX_INDUSTRIES,
                                        INDUSTRY_CHARS)):
            names = a.get(arg) or []
            if not isinstance(names, list) or not all(
                    isinstance(n, str) and 0 < len(n) <= chars for n in names):
                raise ValueError(f"{arg} is a list of names")
            if len(names) > top:
                raise ValueError(f"at most {top} {arg}")
            if names:
                params[param] = list(dict.fromkeys(names))
        query = " ".join(str(a.get("name") or "").split())
        if query:
            if len(query) > 100:
                raise ValueError("name: at most 100 characters")
            params["q"] = query
        if a.get("revenue_band"):
            if a["revenue_band"] not in BANDS:
                raise ValueError(f"revenue_band is one of {', '.join(BANDS)}")
            params["revenue_band"] = a["revenue_band"]
        return "/v1/screener", params
    raise ValueError(f"unknown tool {name}")


def _whole(a: dict[str, Any], key: str, default: int, top: int) -> int:
    """a[key] as a whole number, capped at `top`. Missing or null is the default: a model sends
    null for "no preference", and int(None) was a TypeError the caller didn't catch."""
    value = a.get(key)
    if value is None:
        return default
    try:
        return min(int(value), top)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a whole number up to {top}") from None


def _quarters(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"quarters must be a whole number from 1 to {MAX_QUARTERS}") from None
    return max(1, min(n, MAX_QUARTERS))


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


def _top_changes(body: dict, limit: Any) -> dict:
    """The `limit` highest-scored changes across sections (the ranked view's order), each left in
    its section, saying how many were left out. The echoed filters go: the topic is the model's
    own words, never evidence for a figure it writes (verify.py)."""
    try:
        keep = max(1, min(int(limit), MAX_CHANGES))
    except (TypeError, ValueError):
        keep = MATERIAL_CHANGES
    body.pop("filters", None)
    ranked = sorted(((c.get("score") or 0, i, j)
                     for i, s in enumerate(body.get("sections") or [])
                     for j, c in enumerate(s.get("changes") or [])),
                    key=lambda x: (-x[0], x[1], x[2]))
    kept = {(i, j) for _, i, j in ranked[:keep]}
    for i, s in enumerate(body.get("sections") or []):
        changes = s.get("changes") or []
        s["changes"] = [c for j, c in enumerate(changes) if (i, j) in kept]
        if len(changes) > len(s["changes"]):
            s["changes_omitted"] = len(changes) - len(s["changes"])
    return body


def _companies(body: Any) -> dict:
    """/v1/screener's rows as find_companies' compact ones: identity and the grouping (public
    metadata), then the values exactly as the API gave them (null where the user can't read
    the company's financials: the screener nulls them), then any reading and the revenue's
    filing. The values come before any nested object: the adversarial harness reads a row's
    values up to its first brace (tool_leaks)."""
    body = body if isinstance(body, dict) else {}
    rows = []
    for r in body.get("rows") or []:
        if not isinstance(r, dict) or not isinstance(r.get("company_id"), str):
            continue
        values = _dict(r.get("values"))
        readings = _dict(r.get("readings"))
        sources = _dict(r.get("sources"))
        currency = next((x.get("unit") for x in readings.values() if isinstance(x, dict)
                         and x.get("status") == "other_currency" and x.get("unit")), None)
        if currency is None and any(values.get(k) is not None
                                    for k in ("revenue_ttm", "total_assets_q")):
            currency = "USD"
        row = {"company_id": r["company_id"], "name": r.get("name"),
               "tickers": r.get("tickers") or [], "sector": r.get("sector"),
               "industry": r.get("sector_industry"), "sic": r.get("sic"),
               "sic_label": r.get("industry"),
               "revenue_ttm": values.get("revenue_ttm"),
               "revenue_growth_ttm": values.get("revenue_growth_ttm"),
               "operating_margin_ttm": values.get("operating_margin_ttm"),
               "total_assets": values.get("total_assets_q"), "currency": currency}
        notes = {k.removesuffix("_q"): x["status"] + (f": {x['reason']}" if x.get("reason") else "")
                 for k, x in readings.items()
                 if isinstance(x, dict) and isinstance(x.get("status"), str)
                 and x["status"] != "other_currency"}
        if notes:
            row["readings"] = notes
        src = sources.get("revenue_ttm") or sources.get("total_assets_q")
        if isinstance(src, dict) and src.get("accession_no"):
            row["source"] = {k: src.get(k) for k in ("accession_no", "form", "filed",
                                                     "period_end")}
        rows.append(row)
    out: dict[str, Any] = {"as_of": body.get("as_of"), "total": body.get("total"),
                           "companies": rows}
    total = body.get("total")
    if isinstance(total, int) and total > len(rows):
        out["more"] = (f"{total - len(rows)} more match: raise limit (at most "
                       f"{MAX_FIND_ROWS}), narrow the industries, or sort another way")
    out["note"] = ("No companies match: check the names, or widen the search." if not rows else
                   "Amounts in each row's currency; growth and margin are fractions. null: the "
                   "user can't read the company's financials, or it reports none.")
    return out


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _short_adjustments(body: dict) -> dict:
    """Each quarter's quoted adjustments whole up to ADJUSTMENT_CHARS, in order; past that a
    table keeps its heading and citation, and says where to read it."""
    for q in body.get("quarters") or []:
        left = ADJUSTMENT_CHARS
        for a in q.get("adjustments") or []:
            text = a.get("text") or ""
            if len(text) <= left:
                left -= len(text)
            else:
                a["text"] = None
                a["omitted"] = "too long to include: read it with get_section at its citation"
                left = 0
    return body


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))
    except ValueError:
        return r.text[:200]
