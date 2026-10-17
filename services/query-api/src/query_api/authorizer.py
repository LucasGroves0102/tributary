"""Layer 1: OpenFGA. Every data request is checked here before any query runs."""

from typing import Protocol

import httpx

from tributary_authz import FgaClient, FgaError

RELATIONS = {"financials": "can_read_financials", "filings": "can_read_filings"}


class AuthzUnavailable(Exception):
    """OpenFGA could not answer. Callers must deny (fail closed)."""


class Authorizer(Protocol):
    def can(self, user_id: str, tier: str, company_id: str) -> bool: ...
    def readable(self, user_id: str) -> dict[str, set[str]]: ...


class FgaAuthorizer:
    def __init__(self, fga: FgaClient) -> None:
        self._fga = fga

    def can(self, user_id: str, tier: str, company_id: str) -> bool:
        try:
            return self._fga.check(f"user:{user_id}", RELATIONS[tier], f"company:{company_id}")
        except (FgaError, httpx.HTTPError) as e:
            raise AuthzUnavailable(str(e)) from e

    def readable(self, user_id: str) -> dict[str, set[str]]:
        """company_id → tiers the user can read."""
        out: dict[str, set[str]] = {}
        try:
            for tier, relation in RELATIONS.items():
                for obj in self._fga.list_objects(f"user:{user_id}", relation, "company"):
                    out.setdefault(obj.removeprefix("company:"), set()).add(tier)
        except (FgaError, httpx.HTTPError) as e:
            raise AuthzUnavailable(str(e)) from e
        return out
