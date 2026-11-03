"""Layer 1: OpenFGA. Every data request is checked here before any query runs."""

from typing import NamedTuple, Protocol

import httpx

from tributary_authz import FgaClient, FgaError

RELATIONS = {"financials": "can_read_financials", "filings": "can_read_filings"}
# The public catalog (authz/model.fga): every company with data, readable by every user while
# catalog:sec#reader@user:* exists (the app database's public_catalogs switch).
CATALOG = "catalog:sec"


class AuthzUnavailable(Exception):
    """OpenFGA could not answer. Callers must deny (fail closed)."""


class Access(NamedTuple):
    """What a user can read, without enumerating the catalog.
    public: the tiers under which every catalog company is readable (empty: catalog closed to
      this user, or no tiers). The catalog's company ids come from Postgres (app.py).
    listed: companies readable individually → tiers. Catalog closed: everything the user can
      read. Catalog public: the user's private grants (what they read beyond the catalog)."""
    public: frozenset[str]
    listed: dict[str, set[str]]


class Authorizer(Protocol):
    def can(self, user_id: str, tier: str, company_id: str) -> bool: ...
    def access(self, user_id: str) -> Access: ...


class FgaAuthorizer:
    def __init__(self, fga: FgaClient) -> None:
        self._fga = fga

    def can(self, user_id: str, tier: str, company_id: str) -> bool:
        """A plain Check: OpenFGA resolves grants and `reader from catalog` itself."""
        try:
            return self._fga.check(f"user:{user_id}", RELATIONS[tier], f"company:{company_id}")
        except (FgaError, httpx.HTTPError) as e:
            raise AuthzUnavailable(str(e)) from e

    def access(self, user_id: str) -> Access:
        """One Check on the catalog, then small lists only. ListObjects over the catalog would
        return ~6,000 objects (OpenFGA caps it at 1,000 by default, and it is slow), so with
        the catalog public the tiers come from the user's tier subscriptions and the private
        grants from `granted`, which never includes the catalog. Every granted or catalog
        company points at both tier objects (authz-sync), so this equals can_read_*."""
        user = f"user:{user_id}"
        try:
            if self._fga.check(user, "reader", CATALOG):
                subscribed = self._fga.list_objects(user, "subscriber", "tier")
                tiers = frozenset(o.removeprefix("tier:") for o in subscribed) & set(RELATIONS)
                granted = self._fga.list_objects(user, "granted", "company") if tiers else []
                return Access(tiers, {o.removeprefix("company:"): set(tiers) for o in granted})
            listed: dict[str, set[str]] = {}
            for tier, relation in RELATIONS.items():
                for obj in self._fga.list_objects(user, relation, "company"):
                    listed.setdefault(obj.removeprefix("company:"), set()).add(tier)
            return Access(frozenset(), listed)
        except (FgaError, httpx.HTTPError) as e:
            raise AuthzUnavailable(str(e)) from e
