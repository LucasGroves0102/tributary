"""deploy/platform-setup/setup.py kafka: the ACLs and users it derives from kafka-acls.yaml."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACLS = ROOT / "deploy/helm/platform/files/kafka-acls.yaml"
_spec = importlib.util.spec_from_file_location("platform_setup",
                                               ROOT / "deploy/platform-setup/setup.py")
assert _spec and _spec.loader
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


@pytest.fixture(scope="module")
def spec():
    return setup.load_acl_spec(str(ACLS))


def ops(acls, principal, resource="topic"):
    return {(name, op) for r, name, _, p, op in acls if p == f"User:{principal}" and r == resource}


def test_every_client_has_a_user(spec):
    assert setup.kafka_users(spec) == sorted([
        "authz-sync", "bronze-loader", "connect", "doc-crawler", "doc-fetcher", "doc-fetcher-seed",
        "kafka-exporter", "parser", "parser-backfill", "poller", "poller-backfill"])


def test_producers_and_consumers(spec):
    acls = setup.kafka_acls(spec)
    assert ops(acls, "poller") == {("filings.raw", "write"), ("filings.raw", "create")}
    assert ops(acls, "doc-fetcher") == {("filings.raw", "read"), ("documents.fetched", "write"),
                                        ("documents.fetched", "create")}
    assert ops(acls, "doc-fetcher", "group") == {("doc-fetcher", "read")}
    assert ops(acls, "parser") == {("documents.fetched", "read")}
    assert ops(acls, "parser", "group") == {("parser-python", "read")}
    assert ops(acls, "parser-backfill") == {("documents.backfill", "read")}


def test_authz_sync_can_only_read_the_cdc_topics(spec):
    acls = setup.kafka_acls(spec)
    assert {op for _, op in ops(acls, "authz-sync")} == {"read"}
    assert {name for name, _ in ops(acls, "authz-sync")} == {
        "app.public.users", "app.public.subscriptions", "app.public.company_grants",
        "app.public.plan_tiers"}


def test_only_connect_writes_cdc_topics(spec):
    acls = setup.kafka_acls(spec)
    writers = {p for r, name, pattern, p, op in acls
               if r == "topic" and op in ("write", "all")
               and (name.startswith("app.") or (pattern == "prefixed" and "app.".startswith(name))
                    or name == "*")}
    assert writers == {"User:connect"}


def test_exporter_only_describes(spec):
    acls = setup.kafka_acls(spec)
    exporter = {(r, op) for r, _, _, p, op in acls if p == "User:kafka-exporter"}
    assert exporter == {("topic", "describe"), ("group", "describe"), ("cluster", "describe")}


def test_no_principal_gets_all_or_cluster_writes(spec):
    for r, _, _, _, op in setup.kafka_acls(spec):
        assert op not in ("all", "alter", "alter_configs", "delete", "cluster_action")
        assert r != "cluster" or op == "describe"


@pytest.mark.parametrize("grants,error", [
    ({"poduce": ["t"]}, "unknown keys"),
    ({"acls": [{"resource": "topic", "name": "t", "operations": ["reed"]}]}, "unknown operation"),
    ({"acls": [{"resource": "broker", "name": "t", "operations": ["read"]}]}, "bad ACL"),
    ({"acls": [{"resource": "topic", "name": "t", "pattern": "match",
                "operations": ["read"]}]}, "bad ACL"),
    ({"acls": [{"resource": "cluster", "name": "c", "operations": ["describe"]}]}, "kafka-cluster"),
])
def test_bad_specs_fail(grants, error):
    with pytest.raises(ValueError, match=error):
        setup.kafka_acls({"principals": {"x": grants}})


def test_admin_is_not_listed():
    with pytest.raises(ValueError, match="super user"):
        setup.kafka_users({"principals": {"admin": {}}})


def test_diff_deletes_extra_and_creates_missing():
    keep = ("topic", "t", "literal", "User:a", "read")
    old = ("topic", "t", "literal", "User:a", "write")
    new = ("group", "g", "literal", "User:a", "read")
    assert setup.acl_diff({keep, old}, {keep, new}) == ({old}, {new})


def test_passwords_come_from_secret_files(tmp_path):
    (tmp_path / "kafka-password-poller").write_text("0123456789abcdef0123\n")
    assert setup.read_password(str(tmp_path), "poller") == "0123456789abcdef0123"
    with pytest.raises(SystemExit, match="add kafka-password-parser to the platform secret"):
        setup.read_password(str(tmp_path), "parser")
    (tmp_path / "kafka-password-short").write_text("abc")
    with pytest.raises(SystemExit, match="shorter than 16"):
        setup.read_password(str(tmp_path), "short")


def test_chart_json_matches_yaml(tmp_path, spec):
    import json
    path = tmp_path / "kafka-acls.json"
    path.write_text(json.dumps(spec))
    assert setup.load_acl_spec(str(path)) == spec
