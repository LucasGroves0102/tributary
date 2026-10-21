"""Kafka client settings (librdkafka / confluent-kafka) from the environment.

Every Python service builds its Producer/Consumer config with `client_config`, so connection and
authentication settings come from one place. The Helm chart's `uses: kafka` sets the variables:

- `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`, the local stack)
- `KAFKA_SECURITY_PROTOCOL`: `PLAINTEXT` (default), `SASL_PLAINTEXT` or `SASL_SSL`
- `KAFKA_SASL_MECHANISM` (default `SCRAM-SHA-512`), `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD`:
  required with a SASL protocol, rejected without one (a half-applied rollout fails loudly)
- `KAFKA_SSL_CA_LOCATION`: CA file for `SASL_SSL` (default: the system store)

services/parser-rs reads the same variables (`src/bin/parser/consumer.rs`).
"""

import os
from collections.abc import Mapping
from typing import Any

DEFAULT_BOOTSTRAP = "localhost:9092"
SASL_PROTOCOLS = ("SASL_PLAINTEXT", "SASL_SSL")
PROTOCOLS = ("PLAINTEXT", *SASL_PROTOCOLS)
MECHANISMS = ("SCRAM-SHA-512", "SCRAM-SHA-256")
SASL_VARS = ("KAFKA_SASL_MECHANISM", "KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD")

__all__ = ["bootstrap_servers", "client_config"]


def bootstrap_servers(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("KAFKA_BOOTSTRAP_SERVERS") or DEFAULT_BOOTSTRAP


def client_config(settings: Mapping[str, Any] | None = None,
                  env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Connection and auth settings from `env` (default os.environ), plus the client's own
    `settings` (group.id, enable.idempotence, ...). Raises ValueError on an inconsistent env."""
    env = os.environ if env is None else env
    config: dict[str, Any] = {"bootstrap.servers": bootstrap_servers(env)}
    protocol = (env.get("KAFKA_SECURITY_PROTOCOL") or "PLAINTEXT").upper()
    if protocol not in PROTOCOLS:
        raise ValueError(f"KAFKA_SECURITY_PROTOCOL={protocol!r}: expected one of {PROTOCOLS}")
    if protocol in SASL_PROTOCOLS:
        mechanism = (env.get("KAFKA_SASL_MECHANISM") or "SCRAM-SHA-512").upper()
        if mechanism not in MECHANISMS:
            raise ValueError(f"KAFKA_SASL_MECHANISM={mechanism!r}: expected one of {MECHANISMS}")
        username, password = env.get("KAFKA_SASL_USERNAME"), env.get("KAFKA_SASL_PASSWORD")
        if not username or not password:
            raise ValueError(f"{protocol} needs KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD")
        config |= {"security.protocol": protocol, "sasl.mechanism": mechanism,
                   "sasl.username": username, "sasl.password": password}
        if protocol == "SASL_SSL" and env.get("KAFKA_SSL_CA_LOCATION"):
            config["ssl.ca.location"] = env["KAFKA_SSL_CA_LOCATION"]
    elif set_vars := [v for v in SASL_VARS if env.get(v)]:
        raise ValueError(f"{', '.join(set_vars)} set but KAFKA_SECURITY_PROTOCOL is {protocol}")
    for key in settings or {}:
        if key in config:
            raise ValueError(f"{key} comes from the environment, not from client settings")
    return config | dict(settings or {})
