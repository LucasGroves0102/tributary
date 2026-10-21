import pytest

from tributary_kafka import bootstrap_servers, client_config

SASL = {
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.platform.svc.cluster.local:9094",
    "KAFKA_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
    "KAFKA_SASL_USERNAME": "poller",
    "KAFKA_SASL_PASSWORD": "secret",
}


def test_defaults_to_the_local_plaintext_stack():
    assert client_config(env={}) == {"bootstrap.servers": "localhost:9092"}
    assert bootstrap_servers({}) == "localhost:9092"


def test_plaintext_keeps_todays_config():
    env = {"KAFKA_BOOTSTRAP_SERVERS": "kafka:9092", "KAFKA_SECURITY_PROTOCOL": "plaintext"}
    assert client_config({"group.id": "g"}, env=env) == {"bootstrap.servers": "kafka:9092",
                                                         "group.id": "g"}


def test_sasl_scram_from_env():
    config = client_config({"enable.idempotence": True}, env=SASL)
    assert config == {
        "bootstrap.servers": "kafka.platform.svc.cluster.local:9094",
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": "SCRAM-SHA-512",
        "sasl.username": "poller",
        "sasl.password": "secret",
        "enable.idempotence": True,
    }


def test_sasl_ssl_with_ca_and_other_mechanism():
    env = SASL | {"KAFKA_SECURITY_PROTOCOL": "SASL_SSL", "KAFKA_SASL_MECHANISM": "scram-sha-256",
                  "KAFKA_SSL_CA_LOCATION": "/ca.pem"}
    config = client_config(env=env)
    assert config["security.protocol"] == "SASL_SSL"
    assert config["sasl.mechanism"] == "SCRAM-SHA-256"
    assert config["ssl.ca.location"] == "/ca.pem"


@pytest.mark.parametrize("missing", ["KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD"])
def test_sasl_without_credentials_fails(missing):
    env = {k: v for k, v in SASL.items() if k != missing}
    with pytest.raises(ValueError, match="needs KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD"):
        client_config(env=env)


def test_credentials_without_sasl_fail():
    env = {k: v for k, v in SASL.items() if k != "KAFKA_SECURITY_PROTOCOL"}
    with pytest.raises(ValueError, match="KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD set"):
        client_config(env=env)


@pytest.mark.parametrize("var,value", [("KAFKA_SECURITY_PROTOCOL", "SSL"),
                                       ("KAFKA_SASL_MECHANISM", "PLAIN"),
                                       ("KAFKA_SASL_MECHANISM", "GSSAPI")])
def test_unsupported_values_fail(var, value):
    with pytest.raises(ValueError, match=var):
        client_config(env=SASL | {var: value})


def test_settings_cannot_override_connection_or_credentials():
    with pytest.raises(ValueError, match="sasl.username comes from the environment"):
        client_config({"sasl.username": "admin"}, env=SASL)


def test_reads_os_environ_by_default(monkeypatch):
    for k, v in SASL.items():
        monkeypatch.setenv(k, v)
    assert client_config()["sasl.username"] == "poller"
