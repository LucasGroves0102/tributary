"""Run the Query API with real OpenFGA and Postgres."""

import os

import httpx
import uvicorn
from psycopg_pool import ConnectionPool

from query_api.alerts import AppAlerts
from query_api.app import create_app
from query_api.auth import check_secret
from query_api.authorizer import FgaAuthorizer
from query_api.connections import AppConnections, market_data_from_env
from query_api.github import AppAccounts, GitHubConfig, HttpGitHub
from query_api.password_accounts import AppPasswordAccounts
from query_api.providers import PROVIDERS
from query_api.sandbox import AppSandboxes, SandboxConfig
from query_api.store import Store
from query_api.thesis_checks import AppThesisChecks
from query_api.views import AppViews
from query_api.watchlist import AppWatchlists
from query_api.workspaces import AppWorkspaces
from tributary_authz import FgaClient
from tributary_telemetry import configure_logging


def build_app():
    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=10, open=True)
    app_url = os.environ.get("APP_DATABASE_URL")  # watchlists, workspaces, views; unset: 503
    app_pool = ConnectionPool(app_url, min_size=1, max_size=4, open=True) if app_url else None
    sandbox_config = SandboxConfig.from_env()
    if sandbox_config.enabled and app_pool is None:
        raise RuntimeError("DEMO_SANDBOX_ENABLED needs APP_DATABASE_URL")
    github_config = GitHubConfig.from_env(os.environ)  # None unless both GITHUB_CLIENT_* set
    if github_config and app_pool is None:
        raise RuntimeError("GITHUB_CLIENT_ID needs APP_DATABASE_URL")
    # Connect your data: off unless DATA_CONNECTIONS_KEY is set (malformed: fails at start)
    provider_http = httpx.Client(timeout=20, headers={"User-Agent": "Tributary"})
    market_data = market_data_from_env(
        os.environ, AppConnections(app_pool) if app_pool else None,
        {p: make(provider_http) for p, make in PROVIDERS.items()})
    return create_app(
        authorizer=FgaAuthorizer(FgaClient.from_env()),
        store=Store(pool),
        jwt_secret=check_secret(os.environ["QUERY_API_JWT_SECRET"]),
        watchlists=AppWatchlists(app_pool) if app_pool else None,
        demo_users=frozenset(u for u in os.environ.get("DEMO_USERS", "").split(",") if u),
        sandboxes=AppSandboxes(app_pool) if app_pool else None,
        sandbox_config=sandbox_config,
        workspaces=AppWorkspaces(app_pool) if app_pool else None,
        views=AppViews(app_pool) if app_pool else None,
        github_config=github_config,
        github=HttpGitHub(github_config, httpx.Client(
            timeout=10, headers={"User-Agent": "Tributary"})) if github_config else None,
        github_accounts=AppAccounts(app_pool) if github_config and app_pool else None,
        thesis_checks=AppThesisChecks(app_pool) if app_pool else None,
        market_data=market_data,
        # owner-created accounts (query-api-account); sign-in needs the app database
        password_accounts=AppPasswordAccounts(app_pool) if app_pool else None,
        # filing alerts (query-api-alerts makes them; the routes read them and the settings)
        alerts=AppAlerts(app_pool) if app_pool else None,
    )


def main() -> None:
    configure_logging()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
