# tributary-telemetry

Logging and Prometheus helpers shared by every Python service.

- `configure_logging()`: JSON lines on stdout with `ts`, `level`, `logger`, `message`, the
  current `request_id`, and any `extra={...}` fields.
- `serve_metrics()`: a `/metrics` server on `METRICS_PORT`, for workers without an HTTP app.
  Unset (local runs, one-shot jobs), it does nothing.
- `CONSUMED`, `PRODUCED`, `SKIPPED`: Kafka message counters, labelled by topic.
- `tributary_telemetry.http` (FastAPI services; needs fastapi): `instrument(app)` adds `/metrics`, per-route
  request metrics, an access log line and the `X-Request-ID` request ID;
  `forward_request_id` is an httpx request hook that passes the ID to the next service.
