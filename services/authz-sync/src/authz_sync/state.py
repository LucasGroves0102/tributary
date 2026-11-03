"""App state rebuilt from CDC events, and the permissions it implies. Pure, no I/O."""

from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

from tributary_authz import Tuple

TIERS = ("financials", "filings")
# The one catalog: every company with data (authz.catalog_companies() in the Tributary DB).
# Opened by the app database's public_catalogs row with this id.
CATALOG = "sec"


@dataclass(frozen=True)
class Desired:
    """Everything authz-sync writes, for one reconcile."""
    tuples: set[Tuple]                       # OpenFGA
    access: set[tuple[str, str, str]]        # authz.user_access (user, company, tier)
    user_tiers: set[tuple[str, str]]         # authz.user_tiers (user, tier)
    public_access: set[tuple[str, str]]      # authz.public_access (company, tier)


@dataclass(frozen=True)
class Grant:
    company_id: str
    user_id: str | None
    org_id: str | None


@dataclass
class AppState:
    users: dict[str, str | None] = field(default_factory=dict)     # user_id → org_id
    subscriptions: dict[str, str] = field(default_factory=dict)    # user_id → plan_id
    plan_tiers: set[tuple[str, str]] = field(default_factory=set)  # (plan_id, tier)
    grants: dict[int, Grant] = field(default_factory=dict)         # grant_id → grant
    public_catalogs: dict[str, bool] = field(default_factory=dict)  # catalog_id → public

    def apply(self, table: str, event: dict) -> None:
        """Apply one Debezium envelope. c/r/u/d all reduce to: drop `before`, add `after`."""
        before, after = event.get("before"), event.get("after")
        if table == "users":
            if before:
                self.users.pop(before["user_id"], None)
            if after:
                self.users[after["user_id"]] = after["org_id"]
        elif table == "subscriptions":
            if before:
                self.subscriptions.pop(before["user_id"], None)
            if after:
                self.subscriptions[after["user_id"]] = after["plan_id"]
        elif table == "plan_tiers":
            if before:
                self.plan_tiers.discard((before["plan_id"], before["tier"]))
            if after:
                self.plan_tiers.add((after["plan_id"], after["tier"]))
        elif table == "company_grants":
            if before:
                self.grants.pop(before["grant_id"], None)
            if after:
                self.grants[after["grant_id"]] = Grant(
                    after["company_id"], after["user_id"], after["org_id"])
        elif table == "public_catalogs":
            if before:
                self.public_catalogs.pop(before["catalog_id"], None)
            if after:
                self.public_catalogs[after["catalog_id"]] = bool(after["public"])
        else:
            raise ValueError(f"unexpected table {table!r}")

    def tiers_of(self, user_id: str) -> set[str]:
        plan = self.subscriptions.get(user_id)
        return {tier for p, tier in self.plan_tiers if p == plan}

    def is_public(self, catalog_id: str = CATALOG) -> bool:
        """No row (or no topic yet) means closed."""
        return self.public_catalogs.get(catalog_id, False)

    def desired_tuples(self, catalog: AbstractSet[str] = frozenset()) -> set[Tuple]:
        """OpenFGA tuples for authz/model.fga. Watchlists are deliberately absent.
        `catalog`: the company ids in catalog:sec; their membership tuples exist whether or not
        the catalog is public, so opening or closing it is one tuple."""
        tuples: set[Tuple] = set()
        for company_id in catalog:
            company = f"company:{company_id}"
            tuples.add(Tuple(f"catalog:{CATALOG}", "catalog", company))
            for tier in TIERS:
                tuples.add(Tuple(f"tier:{tier}", f"{tier}_tier", company))
        for catalog_id, public in self.public_catalogs.items():
            if public:
                tuples.add(Tuple("user:*", "reader", f"catalog:{catalog_id}"))
        for user_id, org_id in self.users.items():
            if org_id:
                tuples.add(Tuple(f"user:{user_id}", "member", f"organization:{org_id}"))
            for tier in self.tiers_of(user_id):
                tuples.add(Tuple(f"user:{user_id}", "subscriber", f"tier:{tier}"))
        for g in self.grants.values():
            company = f"company:{g.company_id}"
            grantee = f"user:{g.user_id}" if g.user_id else f"organization:{g.org_id}#member"
            tuples.add(Tuple(grantee, "viewer", company))
            # the same grant under `granted`, which the Query API lists (never the catalog)
            tuples.add(Tuple(grantee, "granted", company))
            for tier in TIERS:  # point every granted company at its tier objects
                tuples.add(Tuple(f"tier:{tier}", f"{tier}_tier", company))
        return tuples

    def desired_access(self) -> set[tuple[str, str, str]]:
        """(user_id, company_id, tier) rows for authz.user_access: grant AND tier."""
        rows = set()
        for user_id, org_id in self.users.items():
            tiers = self.tiers_of(user_id)
            companies = {
                g.company_id for g in self.grants.values()
                if g.user_id == user_id or (org_id and g.org_id == org_id)
            }
            rows.update((user_id, c, t) for c in companies for t in tiers)
        return rows

    def desired_user_tiers(self) -> set[tuple[str, str]]:
        """(user_id, tier) rows for authz.user_tiers: the plan's tiers of every user."""
        return {(u, t) for u in self.users for t in self.tiers_of(u)}

    def desired_public_access(self, catalog: AbstractSet[str]) -> set[tuple[str, str]]:
        """(company_id, tier) rows for authz.public_access: the catalog's companies, every
        tier, only while it is public. RLS then applies each user's own tiers."""
        if not self.is_public(CATALOG):
            return set()
        return {(c, t) for c in catalog for t in TIERS}

    def desired(self, catalog: AbstractSet[str] = frozenset()) -> Desired:
        return Desired(self.desired_tuples(catalog), self.desired_access(),
                       self.desired_user_tiers(), self.desired_public_access(catalog))


def accept_catalog(current: AbstractSet[str], loaded: AbstractSet[str]) -> bool:
    """Whether to adopt a freshly loaded catalog. Companies don't lose all their data, so a
    result less than half the size of the current one (a gold rebuild mid-way, a wrong
    database) is refused rather than closing most of the catalog for everyone."""
    return len(loaded) * 2 >= len(current)
