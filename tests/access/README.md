# access

Deterministic cross-user access tests (no model, no OpenFGA, no network): can anyone but its
owner reach a user's private data? With the public catalog open, OpenFGA says yes to every
signed-in user for SEC data, so private data rests on the Query API's owner filter and forced
row-level security ([DESIGN.md](../../DESIGN.md#design-decisions-in-full)). This suite checks
both, with the catalog open and closed.

| Module | What | Tests |
|---|---|---|
| `test_classification.py` | every Query API route classified (public, SEC, owner, account), every owner/account route and each id it takes probed, keys and apps never on account routes, every `create_app` feature on; agent-api refuses keys and app tokens on every route | 8 |
| `test_idor.py` | Ann owns one of everything; Ben signed in, a sandbox, Ben's read key, write key and connected app send each of 119 probes: the same refusal as for a thing nobody has, nothing of Ann's in the answer, nothing of hers changed. Ann's own keys and app reach only their routes; Ann's own request works (the control). SEC routes carry nothing private | 2,402 |
| `test_mcp.py` | every MCP tool, as Ben's key and app over the real Query API, with Ann's ids | 10 |
| `test_schema.py` | `allowlist.toml`: user tables forced and keyed on `app.user_id`, SECURITY DEFINER functions listed, pinned, granted to one role and tested; then RLS alone, per table, in plain SQL as another user | 67 |

Run: `TEST_DATABASE_URL=... APP_TEST_DATABASE_URL=... .venv/bin/pytest tests/access` against
migrated databases (CI's python job; about 45 s). Migrate as a non-superuser, as CI and hermes
do, or FORCE doesn't bind the tables' owner and the RLS-alone tests skip.

## A new route

`test_classification` fails until `access_routes.py` says what it serves:

- **PUBLIC**: no credential (sign-in flows, status, the OAuth server's endpoints).
- **SEC**: company data behind `require()`/`readable()` and RLS.
- **OWNER**: the caller's own saved work or data (keys may reach it: `api_keys.KEY_ROUTES`).
- **ACCOUNT**: the account and what it runs (alerts, webhooks, keys, portfolio, connections,
  agent-api's writes). Never in `KEY_ROUTES`.

An OWNER or ACCOUNT route needs a `P(...)` probe, and every id it takes (path, query or body)
must be named in one as `"@name"` (Ann's thing; `"@ben_*"` for Ben's own beside hers). Set `deny`
when the right refusal isn't 403/404 (a body naming her thing: 422) and `owner` when her own
request doesn't answer 2xx. A new kind of thing: give Ann one in `access_world._anns_things`,
with `MARK` in its text, and add its table to `APP_TABLES`.

## A new table or SECURITY DEFINER function

`test_schema` fails until `allowlist.toml` lists it: a table is `"owner"` (RLS enabled and forced,
a policy on `app.user_id`) or one line saying why not; a DEFINER function has its rule, its one
grantee, a reason and the test that shows it holds. An owner table also needs a row of Ann's in
the world (the RLS-alone test says so).

Also: a new `create_app` parameter is switched on in `access_world.full_app`; a new MCP tool goes
in `test_mcp.py`'s `PRIVATE` or `SEC`; a new agent-api route takes the `token` dependency.
