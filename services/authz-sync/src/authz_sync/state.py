"""App state rebuilt from CDC events, and the permissions it implies. Pure, no I/O."""

from dataclasses import dataclass, field

from tributary_authz import Tuple

TIERS = ("financials", "filings")


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
        else:
            raise ValueError(f"unexpected table {table!r}")

    def tiers_of(self, user_id: str) -> set[str]:
        plan = self.subscriptions.get(user_id)
        return {tier for p, tier in self.plan_tiers if p == plan}

    def desired_tuples(self) -> set[Tuple]:
        """OpenFGA tuples for authz/model.fga. Watchlists are deliberately absent."""
        tuples: set[Tuple] = set()
        for user_id, org_id in self.users.items():
            if org_id:
                tuples.add(Tuple(f"user:{user_id}", "member", f"organization:{org_id}"))
            for tier in self.tiers_of(user_id):
                tuples.add(Tuple(f"user:{user_id}", "subscriber", f"tier:{tier}"))
        for g in self.grants.values():
            company = f"company:{g.company_id}"
            grantee = f"user:{g.user_id}" if g.user_id else f"organization:{g.org_id}#member"
            tuples.add(Tuple(grantee, "viewer", company))
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
