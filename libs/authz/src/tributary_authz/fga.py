"""Minimal OpenFGA HTTP client."""

import os
from collections.abc import Set as AbstractSet
from typing import NamedTuple

import httpx

STORE_NAME = "tributary"
WRITE_LIMIT = 100  # OpenFGA's default max tuples per write request


class Tuple(NamedTuple):
    user: str      # "user:alice", "organization:acme#member", "tier:financials"
    relation: str  # "viewer", "member", "subscriber", ...
    object: str    # "company:0001045810", ...

    def key(self) -> dict[str, str]:
        return {"user": self.user, "relation": self.relation, "object": self.object}


class FgaError(RuntimeError):
    pass


class FgaClient:
    def __init__(self, api_url: str, store_name: str = STORE_NAME,
                 client: httpx.Client | None = None, api_token: str | None = None) -> None:
        """api_token is sent as a bearer token (OpenFGA preshared-key auth)."""
        self._http = client or httpx.Client(base_url=api_url, timeout=10.0)
        if api_token:
            self._http.headers["Authorization"] = f"Bearer {api_token}"
        self.store_id = self._find_store(store_name)

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> "FgaClient":
        """FGA_API_URL, and FGA_API_TOKEN if set."""
        return cls(os.environ["FGA_API_URL"], client=client,
                   api_token=os.environ.get("FGA_API_TOKEN") or None)

    def check(self, user: str, relation: str, object: str) -> bool:
        body = self._post("check", {"tuple_key": {"user": user, "relation": relation,
                                                  "object": object}})
        return body["allowed"]

    def list_objects(self, user: str, relation: str, type: str) -> list[str]:
        body = self._post("list-objects", {"user": user, "relation": relation, "type": type})
        return body["objects"]

    def read_all(self) -> set[Tuple]:
        tuples: set[Tuple] = set()
        token = ""
        while True:
            body = self._post("read", {"page_size": 100, "continuation_token": token})
            # keys can carry extra fields (e.g. "condition"); we use none of them
            tuples.update(Tuple(t["key"]["user"], t["key"]["relation"], t["key"]["object"])
                          for t in body["tuples"])
            token = body.get("continuation_token") or ""
            if not token:
                return tuples

    def write(self, writes: AbstractSet[Tuple] = frozenset(),
              deletes: AbstractSet[Tuple] = frozenset()) -> None:
        """Apply in chunks of WRITE_LIMIT. Each chunk is atomic; the whole call is not."""
        # deletes first: when a change both revokes and grants, never widen access in between
        ops = [("deletes", t) for t in sorted(deletes)] + [("writes", t) for t in sorted(writes)]
        for i in range(0, len(ops), WRITE_LIMIT):
            body: dict = {}
            for kind, t in ops[i:i + WRITE_LIMIT]:
                body.setdefault(kind, {"tuple_keys": []})["tuple_keys"].append(t.key())
            self._post("write", body)

    def _find_store(self, name: str) -> str:
        resp = self._http.get("/stores")
        resp.raise_for_status()
        for store in resp.json()["stores"]:
            if store["name"] == name:
                return store["id"]
        raise FgaError(f"OpenFGA store {name!r} not found; run `make authz`")

    def _post(self, path: str, body: dict) -> dict:
        resp = self._http.post(f"/stores/{self.store_id}/{path}", json=body)
        if resp.status_code >= 400:
            raise FgaError(f"{path} failed ({resp.status_code}): {resp.text}")
        return resp.json()
