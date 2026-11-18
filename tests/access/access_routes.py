"""Every Query API route, classified, and the probes that try to reach Ann's things through each
owner-only and account route. tests/access/README.md says how to classify a new one.

Classes (what a route serves, not who may call it):
- PUBLIC: no credential at all (sign-in flows, status, the OAuth server's own endpoints).
- SEC: SEC data, one company or many: layer 1 (OpenFGA require()/readable()) then RLS.
- OWNER: the caller's own saved work and data (workspaces, items, versions, checks, datasets,
  the watchlist, /v1/me). API keys and connected apps reach some of them (api_keys.KEY_ROUTES).
- ACCOUNT: the account and what it runs (keys, alerts, webhooks, views, market-data
  connections and prices, portfolio and journal, watched screens, connected apps, the
  assistant's quota and agent-api's writes). Never reachable with a key or an app's token.

A probe is one request that names Ann's things: "@name" anywhere in its path, query or body is
Ann's object (access_world World.ids). The suite sends it as each other identity and as each of
Ann's own keys and apps, and the same request with every "@name" replaced by a value nobody has
(`missing`). "@ben_*" names are Ben's own things (a request mixing his and hers); the owner's
control replaces each with her twin (access_world.BEN_TWIN)."""

import re
from dataclasses import dataclass, field
from typing import Any

from access_world import ACC, CO, CO2

# --- classification ---------------------------------------------------------------------------

PUBLIC = frozenset({
    ("GET", "/health"), ("GET", "/metrics"), ("GET", "/v1/status"), ("GET", "/v1/status/data"),
    ("GET", "/v1/metrics"), ("GET", "/v1/screener/columns"), ("GET", "/v1/openapi-keys.json"),
    ("GET", "/v1/demo/users"), ("POST", "/v1/demo/token"),
    ("GET", "/v1/demo/sandbox"), ("POST", "/v1/demo/sandbox"),
    ("POST", "/v1/auth/password"), ("GET", "/v1/auth/providers"),
    ("GET", "/v1/auth/github/start"), ("GET", "/v1/auth/github/callback"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("OPTIONS", "/.well-known/oauth-authorization-server"),
    ("GET", "/oauth/authorize"), ("POST", "/oauth/register"), ("POST", "/oauth/token"),
    ("POST", "/oauth/revoke"), ("OPTIONS", "/oauth/register"), ("OPTIONS", "/oauth/token"),
    ("OPTIONS", "/oauth/revoke"),
})

SEC = frozenset({
    ("GET", "/v1/companies/search"),
    ("GET", "/v1/companies/{company_id}/financials"),
    ("GET", "/v1/companies/{company_id}/annual-per-share"),
    ("GET", "/v1/companies/{company_id}/per-share"),
    ("GET", "/v1/companies/{company_id}/restatements"),
    ("GET", "/v1/companies/{company_id}/segments"),
    ("GET", "/v1/companies/{company_id}/components"),
    ("GET", "/v1/companies/{company_id}/filings"),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/figures"),
    ("GET", "/v1/companies/{company_id}/filings/{accession_no}/changes"),
    ("GET", "/v1/companies/{company_id}/sections/{section_key}"),
    ("GET", "/v1/companies/{company_id}/filing-index"),
    ("GET", "/v1/companies/{company_id}/expected-filings"),
    ("GET", "/v1/companies/{company_id}/peers"),
    ("GET", "/v1/companies/{company_id}/peers/check/{candidate_id}"),
    ("GET", "/v1/companies/{company_id}/data-notes"),
    ("GET", "/v1/companies/{company_id}/events"),
    ("GET", "/v1/companies/{company_id}/earnings"),
    ("GET", "/v1/companies/{company_id}/insiders"),
    ("GET", "/v1/companies/{company_id}/text-kpis"),
    ("POST", "/v1/companies/{company_id}/model"),
    ("GET", "/v1/filings/latest"), ("GET", "/v1/filings/{accession_no}/recap"),
    ("GET", "/v1/filing-index"), ("GET", "/v1/calendar/expected-filings"),
    ("GET", "/v1/data-notes"), ("GET", "/v1/search"), ("GET", "/v1/screener"),
    ("GET", "/v1/sectors"), ("POST", "/v1/formulas/validate"),
    ("POST", "/v1/formulas/evaluate"), ("POST", "/v1/sheets/values"),
})

OWNER = frozenset({
    ("GET", "/v1/me"),
    ("GET", "/v1/watchlist"), ("PUT", "/v1/watchlist/{company_id}"),
    ("DELETE", "/v1/watchlist/{company_id}"),
    ("GET", "/v1/workspaces"), ("POST", "/v1/workspaces"),
    ("PATCH", "/v1/workspaces/{workspace_id}"), ("DELETE", "/v1/workspaces/{workspace_id}"),
    ("POST", "/v1/workspaces/{workspace_id}/duplicate"),
    ("POST", "/v1/workspaces/{workspace_id}/items"), ("GET", "/v1/workspaces/{workspace_id}"),
    ("GET", "/v1/workspace-items/{item_id}"), ("POST", "/v1/workspace-items/{item_id}/open"),
    ("PUT", "/v1/workspace-items/{item_id}"),
    ("GET", "/v1/workspace-items/{item_id}/versions"),
    ("GET", "/v1/workspace-items/{item_id}/versions/{version_id}"),
    ("POST", "/v1/workspace-items/{item_id}/duplicate"),
    ("DELETE", "/v1/workspace-items/{item_id}"),
    ("GET", "/v1/workspace-items/{item_id}/checks"),
    ("GET", "/v1/workspace-items/{item_id}/checks/{check_id}"),
    ("GET", "/v1/recent"), ("PUT", "/v1/recent/filings"),
    ("GET", "/v1/datasets"), ("POST", "/v1/datasets"),
    ("POST", "/v1/datasets/{dataset_id}/upload"), ("GET", "/v1/datasets/{dataset_id}"),
    ("PATCH", "/v1/datasets/{dataset_id}"), ("DELETE", "/v1/datasets/{dataset_id}"),
    ("GET", "/v1/datasets/{dataset_id}/rows"), ("GET", "/v1/companies/{company_id}/datasets"),
})

ACCOUNT = frozenset({
    # agent-api's writes (nginx keeps them off the public site)
    ("POST", "/v1/workspaces/{workspace_id}/reports"),
    ("PUT", "/v1/workspaces/{workspace_id}/reports/{item_id}"),
    ("POST", "/v1/thesis-checks/{item_id}"),
    ("POST", "/v1/demo/agent-quota"), ("POST", "/v1/demo/agent-quota/refund"),
    ("GET", "/v1/demo/agent-quota"),
    # preferences, market data
    ("GET", "/v1/views"), ("PUT", "/v1/views/{view}"), ("DELETE", "/v1/views/{view}"),
    ("GET", "/v1/connections"), ("PUT", "/v1/connections/{provider}"),
    ("DELETE", "/v1/connections/{provider}"), ("GET", "/v1/companies/{company_id}/prices"),
    ("GET", "/v1/companies/{company_id}/valuation"),
    # portfolio and journal
    ("GET", "/v1/portfolio"), ("GET", "/v1/portfolio/position/{company_id}"),
    ("POST", "/v1/portfolio/holdings"), ("DELETE", "/v1/portfolio/holdings/{holding_id}"),
    ("POST", "/v1/portfolio/holdings/{holding_id}/lots"),
    ("DELETE", "/v1/portfolio/lots/{lot_id}"),
    ("POST", "/v1/portfolio/holdings/{holding_id}/sells"),
    ("DELETE", "/v1/portfolio/sells/{sell_id}"),
    ("PUT", "/v1/portfolio/holdings/{holding_id}/price"),
    ("DELETE", "/v1/portfolio/holdings/{holding_id}/price"),
    ("PUT", "/v1/portfolio/holdings/{holding_id}/link"),
    ("POST", "/v1/portfolio/import/preview"), ("POST", "/v1/portfolio/import"),
    ("POST", "/v1/portfolio/prices/refresh"),
    ("GET", "/v1/portfolio/journal"), ("POST", "/v1/portfolio/journal"),
    ("PATCH", "/v1/portfolio/journal/{entry_id}"),
    ("DELETE", "/v1/portfolio/journal/{entry_id}"),
    # filing alerts, webhooks, watched screens
    ("GET", "/v1/alerts/unread"), ("GET", "/v1/alerts/notifications"),
    ("POST", "/v1/alerts/notifications/read"), ("GET", "/v1/alerts/settings"),
    ("PUT", "/v1/alerts/settings/{company_id}"), ("GET", "/v1/alerts/red-lines"),
    ("POST", "/v1/alerts/red-lines"), ("DELETE", "/v1/alerts/red-lines/{rule_id}"),
    ("GET", "/v1/alerts/webhooks"), ("POST", "/v1/alerts/webhooks"),
    ("PATCH", "/v1/alerts/webhooks/{webhook_id}"),
    ("POST", "/v1/alerts/webhooks/{webhook_id}/rotate"),
    ("DELETE", "/v1/alerts/webhooks/{webhook_id}"),
    ("POST", "/v1/alerts/webhooks/{webhook_id}/test"), ("GET", "/v1/alerts/deliveries"),
    ("GET", "/v1/watches"), ("PUT", "/v1/watches/screen/{item_id}"),
    ("DELETE", "/v1/watches/screen/{item_id}"), ("GET", "/v1/alerts/screens"),
    # keys and connected apps
    ("GET", "/v1/api-keys"), ("POST", "/v1/api-keys"), ("DELETE", "/v1/api-keys/{key_id}"),
    ("POST", "/v1/oauth/consent"), ("POST", "/v1/oauth/consent/approve"),
    ("POST", "/v1/oauth/consent/deny"), ("GET", "/v1/oauth/connections"),
    ("DELETE", "/v1/oauth/connections/{connection_id}"),
})

# --- probes -------------------------------------------------------------------------------------

ANNS = re.compile(r"@(?!ben_)[a-z]")


@dataclass(frozen=True)
class Probe:
    method: str
    route: str
    path: dict[str, str] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    json: Any = None
    form: dict[str, str] | None = None  # multipart: with a CSV `file`
    content: bytes | None = None  # a raw body (routes that parse theirs by hand)
    # what another identity gets for Ann's thing, the same as for a missing one
    deny: frozenset[int] = frozenset({403, 404})
    # what Ann gets (the control: the request is well-formed and would reach her thing)
    owner: frozenset[int] = frozenset({200, 201, 204})
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.method, self.route

    @property
    def names_anns(self) -> bool:
        """Whether the request names one of Ann's things (not only Ben's own)."""
        return ANNS.search(repr((self.path, self.query, self.json, self.form))) is not None

    def id(self) -> str:
        bits = [f"{self.method} {self.route}"]
        bits += [f"{k}={v}" for k, v in {**self.path, **self.query}.items()]
        if self.note:
            bits.append(self.note)
        return " ".join(bits)


def P(method: str, route: str, **kw: Any) -> Probe:
    for name in ("deny", "owner"):
        if name in kw:
            kw[name] = frozenset(kw[name])
    return Probe(method, route, **kw)


CHART = {"title": "x", "layers": [{"kind": "metric", "companyId": CO, "metric": "revenue"}]}
CSV = "ticker,date,stores,sales\nTACA,2026-09-30,13,1600\n"
FIDELITY = ("Account Number,Account Name,Symbol,Description,Quantity,Last Price,Current Value,"
            "Cost Basis Total\nX1,IRA,TACA,ACCESS TEST,2,$101.50,$203.00,$150.00\n")
OK200 = {200}
SAME = {200}  # a filter or a mark: an empty answer for Ann's id, as for a missing one
REFUSED = {403, 404, 422}  # a body naming her thing: the same refusal as naming nobody's

PROBES: list[Probe] = [
    # who am I
    P("GET", "/v1/me"),
    # watchlist: keyed by company, only ever the caller's own rows
    P("GET", "/v1/watchlist"),
    P("PUT", "/v1/watchlist/{company_id}", path={"company_id": CO2}),
    P("DELETE", "/v1/watchlist/{company_id}", path={"company_id": CO}, owner=OK200),
    # workspaces
    P("GET", "/v1/workspaces"),
    P("POST", "/v1/workspaces", json={"name": "Ben's folder"}),
    P("GET", "/v1/workspaces/{workspace_id}", path={"workspace_id": "@ws"}),
    P("GET", "/v1/workspaces/{workspace_id}", path={"workspace_id": "@project"}),
    P("PATCH", "/v1/workspaces/{workspace_id}", path={"workspace_id": "@ws"},
      json={"name": "renamed"}),
    P("PATCH", "/v1/workspaces/{workspace_id}", path={"workspace_id": "@project"},
      json={"project": {"companyId": CO2}}),
    P("DELETE", "/v1/workspaces/{workspace_id}", path={"workspace_id": "@ws"}),
    P("POST", "/v1/workspaces/{workspace_id}/duplicate", path={"workspace_id": "@ws"}),
    P("POST", "/v1/workspaces/{workspace_id}/items", path={"workspace_id": "@ws"},
      json={"kind": "chart", "name": "planted", "state": CHART}),
    # items of every kind
    *[P("GET", "/v1/workspace-items/{item_id}", path={"item_id": f"@{k}"})
      for k in ("chart", "thesis", "screen", "metric", "model", "comps", "report")],
    P("POST", "/v1/workspace-items/{item_id}/open", path={"item_id": "@thesis"}),
    P("PUT", "/v1/workspace-items/{item_id}", path={"item_id": "@thesis"},
      json={"state": {"companyId": CO, "markdown": "overwritten"}}),
    P("PUT", "/v1/workspace-items/{item_id}", path={"item_id": "@chart"},
      json={"name": "renamed"}),
    P("GET", "/v1/workspace-items/{item_id}/versions", path={"item_id": "@thesis"}),
    P("GET", "/v1/workspace-items/{item_id}/versions/{version_id}",
      path={"item_id": "@thesis", "version_id": "@version"}),
    P("GET", "/v1/workspace-items/{item_id}/versions/{version_id}",
      path={"item_id": "@ben_chart", "version_id": "@version"}, owner={404},
      note="her version under his item"),
    P("POST", "/v1/workspace-items/{item_id}/duplicate", path={"item_id": "@chart"}),
    P("POST", "/v1/workspace-items/{item_id}/duplicate", path={"item_id": "@ben_chart"},
      json={"workspace_id": "@ws"}, note="his item into her folder"),
    P("DELETE", "/v1/workspace-items/{item_id}", path={"item_id": "@chart"}),
    P("GET", "/v1/workspace-items/{item_id}/checks", path={"item_id": "@thesis"}),
    P("GET", "/v1/workspace-items/{item_id}/checks/{check_id}",
      path={"item_id": "@thesis", "check_id": "@check"}),
    P("GET", "/v1/recent"),
    P("PUT", "/v1/recent/filings", json={"company_id": CO, "accession_no": ACC}),
    # agent-api's writes: a report into her folder, over her report, a check on her thesis
    P("POST", "/v1/workspaces/{workspace_id}/reports", path={"workspace_id": "@ws"},
      json={"name": "planted", "state": {"$report": ["@ws", "@chart"]}}),
    P("PUT", "/v1/workspaces/{workspace_id}/reports/{item_id}",
      path={"workspace_id": "@ws", "item_id": "@report"},
      json={"state": {"$report": ["@ws", "@chart"]}}),
    P("PUT", "/v1/workspaces/{workspace_id}/reports/{item_id}",
      path={"workspace_id": "@ben_ws", "item_id": "@report"},
      json={"state": {"$report": ["@ben_ws", "@chart"]}}, note="her report, his folder"),
    P("PUT", "/v1/workspaces/{workspace_id}/reports/{item_id}",
      path={"workspace_id": "@ws", "item_id": "@ben_report"},
      json={"state": {"$report": ["@ws", "@chart"]}}, note="his report, her folder"),
    P("POST", "/v1/thesis-checks/{item_id}", path={"item_id": "@thesis"},
      json={"result": {"$check": "@thesis"}}),
    P("POST", "/v1/demo/agent-quota"),
    P("POST", "/v1/demo/agent-quota/refund", json={"questions": 1}),
    P("GET", "/v1/demo/agent-quota"),
    # Your data
    P("GET", "/v1/datasets"),
    P("POST", "/v1/datasets", form={"name": "visits"}),
    P("POST", "/v1/datasets/{dataset_id}/upload", path={"dataset_id": "@dataset"},
      form={"mode": "append"}),
    P("GET", "/v1/datasets/{dataset_id}", path={"dataset_id": "@dataset"}),
    P("PATCH", "/v1/datasets/{dataset_id}", path={"dataset_id": "@dataset"},
      json={"title": "renamed"}),
    P("DELETE", "/v1/datasets/{dataset_id}", path={"dataset_id": "@dataset"}),
    P("GET", "/v1/datasets/{dataset_id}/rows", path={"dataset_id": "@dataset"}),
    P("GET", "/v1/companies/{company_id}/datasets", path={"company_id": CO}),
    # her own metric and dataset named from SEC routes
    P("GET", "/v1/screener", query={"metric": "@metric"}),
    P("POST", "/v1/formulas/evaluate", deny=REFUSED,
      json={"formula": "mine.@dataset_name.sales / revenue", "default_company": CO}),
    P("POST", "/v1/formulas/validate", deny=REFUSED | SAME,
      json={"formula": "mine.@dataset_name.sales / revenue", "default_company": CO}),
    # preferences and market data: keyed by name, only ever the caller's own rows
    P("GET", "/v1/views"),
    P("PUT", "/v1/views/{view}", path={"view": "home"},
      json={"state": {"cards": [{"id": "status", "hidden": True}]}}),
    P("DELETE", "/v1/views/{view}", path={"view": "home"}),
    P("GET", "/v1/connections"),
    P("PUT", "/v1/connections/{provider}", path={"provider": "tiingo"},
      content=b'{"api_key": "def456def456def456def456def456"}'),
    P("DELETE", "/v1/connections/{provider}", path={"provider": "tiingo"}),
    P("GET", "/v1/companies/{company_id}/prices", path={"company_id": CO}),
    P("GET", "/v1/companies/{company_id}/valuation", path={"company_id": CO}),
    # portfolio
    P("GET", "/v1/portfolio"),
    P("GET", "/v1/portfolio/position/{company_id}", path={"company_id": "@co"}, deny=SAME),
    P("POST", "/v1/portfolio/holdings",
      json={"company_id": CO, "lot": {"shares": "1", "cost_total": "100"}}),
    P("DELETE", "/v1/portfolio/holdings/{holding_id}", path={"holding_id": "@holding"}),
    P("POST", "/v1/portfolio/holdings/{holding_id}/lots", path={"holding_id": "@holding"},
      json={"shares": "1", "cost_total": "100"}),
    P("DELETE", "/v1/portfolio/lots/{lot_id}", path={"lot_id": "@lot"}),
    P("POST", "/v1/portfolio/holdings/{holding_id}/sells", path={"holding_id": "@holding"},
      json={"shares": "1", "price": "120", "sold_on": "2026-11-02"}),
    P("DELETE", "/v1/portfolio/sells/{sell_id}", path={"sell_id": "@sell"}),
    P("PUT", "/v1/portfolio/holdings/{holding_id}/price", path={"holding_id": "@holding"},
      json={"price": "1", "on": "2026-11-17"}),
    P("DELETE", "/v1/portfolio/holdings/{holding_id}/price", path={"holding_id": "@holding"}),
    P("PUT", "/v1/portfolio/holdings/{holding_id}/link", path={"holding_id": "@holding"},
      json={}),
    P("PUT", "/v1/portfolio/holdings/{holding_id}/link", path={"holding_id": "@ben_holding"},
      json={"thesis_item_id": "@thesis"}, note="his holding, her thesis"),
    P("PUT", "/v1/portfolio/holdings/{holding_id}/link", path={"holding_id": "@ben_holding"},
      json={"project_id": "@project"}, note="his holding, her project"),
    P("POST", "/v1/portfolio/import/preview", content=FIDELITY.encode()),
    P("POST", "/v1/portfolio/import",
      json={"positions": [{"symbol": "TACA", "shares": "2", "cost_total": "150"}]}),
    P("POST", "/v1/portfolio/prices/refresh"),
    P("GET", "/v1/portfolio/journal"),
    P("GET", "/v1/portfolio/journal", query={"company_id": CO}),
    P("POST", "/v1/portfolio/journal", json={"company_id": CO, "kind": "note", "why": "mine"}),
    P("POST", "/v1/portfolio/journal", json={"company_id": CO, "kind": "note", "why": "fix",
                                             "corrects": "@entry"}, note="correcting hers"),
    P("POST", "/v1/portfolio/journal", deny=REFUSED,
      json={"company_id": CO, "kind": "note", "why": "x", "red_line_ids": ["@red_line"]},
      note="citing her red line"),
    P("PATCH", "/v1/portfolio/journal/{entry_id}", path={"entry_id": "@entry"},
      json={"why": "rewritten"}),
    P("PATCH", "/v1/portfolio/journal/{entry_id}", path={"entry_id": "@ben_entry"}, deny=REFUSED,
      json={"red_line_ids": ["@red_line"]}, note="his entry citing her red line"),
    P("DELETE", "/v1/portfolio/journal/{entry_id}", path={"entry_id": "@entry"}),
    # filing alerts
    P("GET", "/v1/alerts/unread"),
    P("GET", "/v1/alerts/notifications"),
    P("GET", "/v1/alerts/notifications", query={"company_id": CO}),
    P("POST", "/v1/alerts/notifications/read", json={"ids": ["@notification"]}, deny=SAME),
    P("POST", "/v1/alerts/notifications/read", json={"all": True}),
    P("POST", "/v1/alerts/notifications/read", json={"company_id": CO}),
    P("GET", "/v1/alerts/settings"),
    P("PUT", "/v1/alerts/settings/{company_id}", path={"company_id": CO}, json={"forms": []}),
    P("GET", "/v1/alerts/red-lines"),
    P("GET", "/v1/alerts/red-lines", query={"company_id": CO, "evaluate": "true"}),
    P("POST", "/v1/alerts/red-lines", json={"company_id": CO, "label": "x", "formula": "revenue",
                                            "op": ">", "threshold": "1"}),
    P("POST", "/v1/alerts/red-lines", deny=REFUSED, owner={422}, note="naming her dataset (red"
      " lines read no datasets, hers neither)",
      json={"company_id": CO, "label": "x", "formula": "mine.@dataset_name.sales",
            "op": ">", "threshold": "1"}),
    P("DELETE", "/v1/alerts/red-lines/{rule_id}", path={"rule_id": "@red_line"}),
    P("GET", "/v1/alerts/webhooks"),
    P("POST", "/v1/alerts/webhooks", json={"url": "https://hooks.example.com/ben"}),
    P("PATCH", "/v1/alerts/webhooks/{webhook_id}", path={"webhook_id": "@webhook"},
      json={"enabled": False}),
    P("POST", "/v1/alerts/webhooks/{webhook_id}/rotate", path={"webhook_id": "@webhook"}),
    P("DELETE", "/v1/alerts/webhooks/{webhook_id}", path={"webhook_id": "@webhook"}),
    P("POST", "/v1/alerts/webhooks/{webhook_id}/test", path={"webhook_id": "@webhook"}),
    P("GET", "/v1/alerts/deliveries"),
    P("GET", "/v1/alerts/deliveries", query={"webhook_id": "@webhook"}, deny=SAME),
    # watched screens
    P("GET", "/v1/watches"),
    P("PUT", "/v1/watches/screen/{item_id}", path={"item_id": "@screen"},
      json={"entering": False, "leaving": True}),
    P("DELETE", "/v1/watches/screen/{item_id}", path={"item_id": "@screen"}),
    P("GET", "/v1/alerts/screens"),
    P("GET", "/v1/alerts/screens", query={"item_id": "@screen"}, deny=SAME),
    # keys and connected apps
    P("GET", "/v1/api-keys"),
    P("POST", "/v1/api-keys", json={"name": "planted", "scopes": ["read", "write"]}),
    P("DELETE", "/v1/api-keys/{key_id}", path={"key_id": "@api_key_ann_read"}),
    P("POST", "/v1/oauth/consent", json={"request": "not-a-sealed-request"}, owner={400}),
    P("POST", "/v1/oauth/consent/approve", json={"request": "not-a-sealed-request"},
      owner={400}),
    P("POST", "/v1/oauth/consent/deny", json={"request": "not-a-sealed-request"}, owner={400}),
    P("GET", "/v1/oauth/connections"),
    P("DELETE", "/v1/oauth/connections/{connection_id}", path={"connection_id": "@client_ann"}),
    P("DELETE", "/v1/oauth/connections/{connection_id}", path={"connection_id": "@ben_client"},
      note="an app both connected: his grant only"),
]

# a request with a CSV file, for the multipart routes
UPLOAD = ("x.csv", CSV.encode(), "text/csv")
