"""Run the Query API with real OpenFGA and Postgres."""

import os

import uvicorn
from psycopg_pool import ConnectionPool

from query_api.app import create_app
from query_api.auth import check_secret
from query_api.authorizer import FgaAuthorizer
from query_api.store import Store
from tributary_authz import FgaClient


def build_app():
    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=10, open=True)
    return create_app(
        authorizer=FgaAuthorizer(FgaClient.from_env()),
        store=Store(pool),
        jwt_secret=check_secret(os.environ["QUERY_API_JWT_SECRET"]),
        demo_users=frozenset(u for u in os.environ.get("DEMO_USERS", "").split(",") if u),
    )


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
