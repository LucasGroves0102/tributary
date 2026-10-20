# tributary-telemetry

Logging and Prometheus helpers shared by every Python service. Prometheus (from
[deploy/helm/monitoring](../../deploy/helm/monitoring)) scrapes the metrics through the
PodMonitors in the tributary chart; Alloy ships the JSON logs to Loki.

- `configure_logging()`: level from `LOG_LEVEL` (default `INFO`); JSON lines on stdout with `ts`, `level`, `logger`, `message`, the
  current `request_id`, and any `extra={...}` fields.
- `serve_metrics()`: a `/metrics` server on `METRICS_PORT`, for workers without an HTTP app.
  Unset (local runs, one-shot jobs), it does nothing.
- `CONSUMED`, `PRODUCED`, `SKIPPED`, `DELIVERY_FAILED`: Kafka message counters, labelled by
  topic (`SKIPPED` also by reason). `count_delivery` is the producer `on_delivery` callback
  that feeds `PRODUCED`/`DELIVERY_FAILED`.
- `tributary_telemetry.http` (FastAPI services; needs fastapi): `instrument(app)` adds `/metrics`, per-route
  request metrics, an access log line and the `X-Request-ID` request ID;
  `forward_request_id` is an httpx request hook that passes the ID to the next service.

Tests: `pytest libs/telemetry`.
