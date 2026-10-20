# tributary-authz

Minimal OpenFGA HTTP client shared by [authz-sync](../../services/authz-sync) (writes tuples)
and the [Query API](../../services/query-api) (checks). The store is found by name
(`tributary`), so no store ID is configured; a missing store raises `FgaError` telling you to
run `make authz`.

| Method | OpenFGA endpoint |
|---|---|
| `check(user, relation, object)` | `POST /stores/{id}/check` |
| `list_objects(user, relation, type)` | `POST /stores/{id}/list-objects` |
| `read_all()` | `POST /stores/{id}/read`, all pages |
| `write(writes, deletes)` | `POST /stores/{id}/write`, chunks of 100, deletes first |

`FgaClient.from_env()` reads `FGA_API_URL` and, if set, `FGA_API_TOKEN` (sent as a bearer token
for OpenFGA's preshared-key auth).

Tests: `pytest libs/authz` (mocked HTTP transport).
