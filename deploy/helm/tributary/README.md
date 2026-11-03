# tributary

Helm chart for the Tributary services on k3s. Kafka, Kafka Connect and OpenFGA run separately
in the `platform` namespace; Postgres runs on the host.

## Prerequisites

- Postgres reachable from pods at `hostAddress` (default `10.42.0.1`, the k3s bridge), with the
  `tributary`, `app` and `openfga` databases and a `query_api` role granted to the login.
- In the release namespace:
  - Secret `tributary` with keys `database-url`, `app-database-url`, `jwt-secret`,
    `anthropic-api-key`, `sec-user-agent`, `openfga-api-token` when `openfga.auth.enabled`,
    `sec-limiter-database-url` when `secLimiter.dedicatedLogin` (below),
    `kafka-password-<user>` for every Kafka client when `kafka.sasl.enabled` (below), and
    optionally `github-client-id` + `github-client-secret` for GitHub sign-in (below) and
    `data-connections-key` for "Connect your data" (below).
  - Secret `ghcr` (type `kubernetes.io/dockerconfigjson`) with a token that can read packages.

## Install

Flux installs it from [deploy/flux/releases/tributary.yaml](../../flux/releases) after the
platform release, with `image.tag` set by image automation to the newest green CI build
(`main-<run>-<sha>`). By hand:

```bash
helm upgrade --install tributary deploy/helm/tributary \
  --namespace tributary --set image.tag=<commit sha>
```

Each install or upgrade first runs the `migrate` Job (`db/Dockerfile`), which applies `db/`
migrations and, when `demo.enabled`, seeds the demo users. A failed migration stops the release.

Fill a new database once the services are up:

```bash
kubectl -n tributary create job --from=cronjob/companies companies-1
kubectl -n tributary create job --from=cronjob/financials financials-1
kubectl -n tributary create job --from=cronjob/seed-documents seed-documents-1
```

`financials-bulk` (every filer's XBRL facts) and `submissions` (every filer's filing history)
also ship suspended; their comments in `values.yaml` describe the first run. So do `segments`
and `section-index`, which read the crawled documents; on hermes both run hourly
(`suspend: false` in the Flux release) since the crawl finished.

The document crawl (`doc-crawler` + `parser-backfill`, see
[doc-fetcher](../../../services/doc-fetcher/README.md#document-crawl)) ships with `replicas: 0`
(a service's `replicas` defaults to 1). Start it by setting both to 1 under `values.services` in
`deploy/flux/releases/tributary.yaml`; `kubectl scale` would be undone by the next Helm upgrade.
On hermes both run at 1 and stay on after the backfill: the crawler re-plans every
`CRAWL_PLAN_HOURS` and is what fetches new filings of untracked companies, which doc-fetcher
doesn't cover.

## Components

`services` become Deployments and `jobs` become CronJobs. Each lists what it `uses`; that
decides its environment variables, which Secret keys it can read, and its network access:

| uses | Env | Network |
|---|---|---|
| `postgres` | `DATABASE_URL` | host Postgres |
| `app-postgres` | `APP_DATABASE_URL` (query-api: watchlists, workspaces, demo sandboxes, password accounts; demo-cleanup) | host Postgres |
| `kafka` | `KAFKA_BOOTSTRAP_SERVERS`; with `kafka.sasl.enabled` also `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME` (`kafkaUser`, default the service/job name) and `KAFKA_SASL_PASSWORD` (`kafka-password-<user>`) | Kafka in `platform` (9092, or the SASL listener 9094) |
| `openfga` | `FGA_API_URL`, `FGA_API_TOKEN` when `openfga.auth.enabled` | OpenFGA in `platform` |
| `sec` | `SEC_USER_AGENT`; `SEC_LIMITER_DATABASE_URL` (main database: `database-url`, or `sec-limiter-database-url` with `secLimiter.dedicatedLogin`) and `SEC_CALLER_CLASS` (`secClass`, default `live` for services, `batch` for jobs) for the shared SEC rate limit | HTTPS to public addresses, host Postgres |
| `anthropic` | `ANTHROPIC_API_KEY` | HTTPS to public addresses |
| `github` | `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` (`github-client-id` / `github-client-secret`, both `optional: true`), `GITHUB_REDIRECT_URI` (`github.redirectUri`) | HTTPS to public addresses |
| `market-data` | `DATA_CONNECTIONS_KEY` (`data-connections-key`, `optional: true`) | HTTPS to public addresses |
| `jwt` | `QUERY_API_JWT_SECRET` | |
| `query-api` | `QUERY_API_URL` | |
| `demo` | `DEMO_USERS` when `demo.enabled` (off on hermes: the named users are test personas); `DEMO_SANDBOX_*`, `DEMO_AGENT_*` when `demo.sandbox.enabled` | |

A service with a `port` gets a Service, health probes, and an ingress policy admitting only the
components in its `from` list and pods in its `fromNamespaces`. Everything else is denied.

Every service also gets a PodMonitor and a policy letting Prometheus (`metrics.podLabels` in
`metrics.namespace`) reach its `/metrics`: on `port` for HTTP services, and on `metrics.port`
(passed as `METRICS_PORT`) for workers. `metrics: false` opts a service out (`web`). The
monitoring chart's Prometheus selects PodMonitors from every namespace and release, so they
need no `release` label. CronJobs aren't scraped; kube-state-metrics reports their job status.

Every pod runs as non-root with a read-only root filesystem, no capabilities, the
`RuntimeDefault` seccomp profile and no service account token.

## Demo sandboxes

"Try the demo" on the web app calls the Query API's `POST /v1/demo/sandbox`, which creates a
temporary real user in the app database (plan pro, a seeded watchlist and workspace; while the
public catalog is closed, also grants on the 20 companies in
`services/query-api/src/query_api/sandbox.py` `DEMO_TICKERS`) and returns a
JWT for it that expires with the sandbox. Access then propagates like any user's: Debezium →
Kafka → authz-sync → OpenFGA + `authz.user_access`. The `demo-cleanup` CronJob (hourly, at :15)
deletes expired sandbox users, which revokes their access the same way.

Knobs, all under `demo.sandbox` (set them in `deploy/flux/releases/tributary.yaml` values):

| Value | Default | Effect |
|---|---|---|
| `enabled` | `true` | `false`: no "Try the demo" (the route answers 404) |
| `ttlHours` | `24` | sandbox lifetime (1-168) |
| `createPerIpPerHour` | `3` | sandboxes one client address may start per hour (stored as an HMAC, not the address) |
| `maxLive` | `200` | unexpired sandboxes at once; above it visitors are told the demo is full |
| `agent.enabled` | `true` | `false`: the assistant tells demo sandboxes it's off; the rest of the app works |
| `agent.questionsPerUserPerDay` | `10` | assistant questions per sandbox per UTC day |
| `agent.questionsPerDay` | `200` | assistant questions across all sandboxes per UTC day |

The question counts live in the app database, so they hold across agent-api restarts.
agent-api's own `AGENT_RATE_PER_HOUR`, `AGENT_IP_RATE_PER_HOUR` and `AGENT_DAILY_BUDGET_USD`
(in-process, all users) still apply on top. To change a cap, edit the value in the Flux release
and let Flux upgrade; to stop demo spend at once, set `demo.sandbox.agent.enabled: false`.

## GitHub sign-in

"Sign in with GitHub" (Query API README, "GitHub sign-in") gives a visitor a permanent account
with a sandbox's access (plan pro; the same 20 companies while the catalog is closed, every
company once it is public) and the same assistant caps
(`demo.sandbox.agent.*`, which apply to GitHub accounts even with sandboxes off). query-api
`uses: github`:

- Secret keys `github-client-id` and `github-client-secret` (the OAuth app's client id and a
  client secret), read with `optional: true`: until both are in the SOPS secret the pod starts
  without them and the GitHub routes answer 404 (the web app hides the button).
- `github.redirectUri` → `GITHUB_REDIRECT_URI`, which must equal the OAuth app's
  "Authorization callback URL" (`https://<host>/api/query/v1/auth/github/callback`). Empty:
  not sent, and GitHub uses the app's registered URL.
- Egress: query-api calls only `github.com` (token exchange) and `api.github.com` (`GET /user`)
  over HTTPS, but a NetworkPolicy can't match hostnames, so `github` gets the same rule as
  `sec` and `anthropic`: TCP 443 to any public address, private ranges excluded
  (`allow-internet`). Narrowing it to GitHub's published ranges (api.github.com/meta) would need
  an IP list kept up to date or an egress proxy.

Owner steps: create a GitHub OAuth app (Settings → Developer settings → OAuth Apps), homepage
`https://<host>`, callback `https://<host>/api/query/v1/auth/github/callback`, Device Flow off;
generate a client secret; `sops deploy/secrets/tributary.yaml` and add `github-client-id` and
`github-client-secret` under `stringData`; commit, and Flux applies it (restart query-api if
the secret changed after the pod started: env from a Secret is read at start).

## Password accounts

Username/password accounts for testers (Query API README, "Password accounts"). There is no
signup and nothing to configure: the sign-in route exists whenever query-api has the app
database, and the web app shows "Sign in" on the front door. The owner creates each account
inside the running query-api pod (it has `APP_DATABASE_URL` and `DATABASE_URL`); the password is
prompted for twice and never echoed, printed or passed as an argument:

```bash
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account create <username> --questions-per-day 100
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account list
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account set-limit <username> --questions-per-day 50
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account set-password <username>
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account disable <username>
sudo k3s kubectl -n tributary exec -it deploy/query-api -- query-api-account enable <username>
```

Usernames are 3-32 of `a-z 0-9 _ . -`; passwords 12-256 characters. `create` writes one
app-DB transaction (user `pw-<username>`, plan pro, the demo's watchlist and workspace, and the
GitHub account's 20 company grants only while the catalog is closed); authz-sync grants the
access within seconds, so send the tester the site address, the username and (over another
channel) the password. `disable` and `set-password` end the account's sessions within 10 s;
`enable` and `set-password` also lift a lockout (10 wrong passwords: 15 minutes). Each account's
`--questions-per-day` (0-1000) is its own assistant cap, separate from `demo.sandbox.agent.*`;
the Flux release raises agent-api's `AGENT_IP_RATE_PER_HOUR` to 40 so a tester on the owner's
network isn't stopped by the shared address, and `AGENT_DAILY_BUDGET_USD` still caps spend.

## Connect your data

"Connect your data" (Query API README, "Connect your data") lets each user add their own
market-data API key; query-api fetches prices with it and keeps them for that user only.
query-api `uses: market-data`:

- Secret key `data-connections-key` → `DATA_CONNECTIONS_KEY`, read with `optional: true`: the
  master key that encrypts users' API keys (envelope AES-256-GCM; the database holds only
  ciphertext). Until it is in the SOPS secret the pod starts without it and the feature is off:
  its routes answer 404 and the web app hides it. A malformed value stops query-api at start.
  Format: `k1:<base64 of 32 random bytes>`; to rotate, prepend a new key
  (`k2:<new>,k1:<old>`) and keep the old one until no row uses it.
- Egress: query-api calls only the provider adapters' hosts (today `api.tiingo.com`) over
  HTTPS, but a NetworkPolicy can't match hostnames, so `market-data` gets the same rule as
  `github`, `sec` and `anthropic`: TCP 443 to any public address, private ranges excluded
  (`allow-internet`). query-api already had it through `uses: github`; `market-data` keeps it
  independent of GitHub sign-in.

Owner steps. The age key lives only on hermes and hermes has no repo checkout, so the encrypted
file goes there and back (it stays encrypted on the way; the value is generated inside the
command and never printed, the same way `scripts/kafka-passwords.sh` writes keys):

```bash
# laptop: send the current encrypted file to hermes
scp deploy/secrets/tributary.yaml hermes:~/tributary-secrets/tributary.yaml
# hermes: add the key
cd ~/tributary-secrets && sops set tributary.yaml '["stringData"]["data-connections-key"]' \
  "\"k1:$(openssl rand -base64 32)\""
# laptop: bring it back, check one key was added and every value is encrypted, commit, push
scp hermes:~/tributary-secrets/tributary.yaml deploy/secrets/tributary.yaml
python3 scripts/check-secrets-encrypted.py && git diff --stat deploy/secrets/tributary.yaml
git commit -m "Secrets: data-connections-key" deploy/secrets/tributary.yaml && git push
# hermes, after Flux applies the secret: env is read at start
sudo k3s kubectl -n tributary rollout restart deployment/query-api
```

Back the key up in the password manager next to the age key: losing it makes every stored API
key unreadable (users would have to reconnect).

Then `GET /api/query/v1/connections` with a token answers 200 instead of 404, and Settings →
Connections appears in the web app. Keep the master key backed up with the age key: losing it
makes every stored API key unreadable (users would reconnect; their prices can be refetched).
To turn the feature off again, remove the key (`sops deploy/secrets/tributary.yaml`) and
restart query-api; stored connections stay encrypted in the app DB until users disconnect.

## Kafka authentication

`kafka.sasl.enabled` (default false; on in hermes' Flux release) is phase B of the Kafka SASL/SCRAM rollout, described with
its secrets, order and rollback in the
[platform chart's README](../platform/README.md#kafka-authentication-saslscram). On, every
`uses: kafka` pod connects to `kafka.sasl.bootstrap` (port 9094) over SASL_PLAINTEXT with
SCRAM-SHA-512 as its own user: `kafkaUser`, default the service or job name (`backfill` is
`poller-backfill`, `seed-documents` is `doc-fetcher-seed`), with the password from
`kafka-password-<user>` in `secretName`. The allowed topics and groups per user are in
`deploy/helm/platform/files/kafka-acls.yaml`; `tests/check_kafka_auth.py` (CI) checks both
renders and that every client's user is listed there. Python services build their client config
with [libs/kafka](../../../libs/kafka), the Rust parser with the same variables.

## SEC limiter login

Every `uses: sec` pod takes tokens from the shared SEC rate limit in Postgres
([libs/sec](../../../libs/sec), migration 019). By default it does that with the full
`database-url` login, which also gives the poller (`kafka`, `sec`) credentials it otherwise
wouldn't have (the `backfill` job uses `postgres` anyway, for the completeness check). Migration 022 adds `sec_limiter`, a NOLOGIN group that
can only `EXECUTE` `ops.sec_take_token` / `ops.sec_report_throttle` (now `SECURITY DEFINER`, with
`search_path = pg_catalog, pg_temp`, and no longer executable by `PUBLIC`) and read no table
(`libs/sec/tests/test_sec_limiter_role.py`). With `secLimiter.dedicatedLogin: true`,
`SEC_LIMITER_DATABASE_URL` comes from the `sec-limiter-database-url` key instead, and pods that
use `sec` without `postgres` get no `database-url` at all (CI checks both renders with
`tests/check_sec_limiter.py`).

Owner steps, in this order (each step keeps the current deploy working):

1. Deploy a release that includes migration 022 (the `migrate` hook applies it). Until the flag
   flips, services still use `database-url`, which owns the functions and keeps `EXECUTE`.
2. On hermes, create the login in the group (the password never goes in a migration or the
   repository):

   ```bash
   sudo -u postgres psql -d tributary <<'SQL'
   CREATE ROLE tributary_sec LOGIN PASSWORD '<generated password>' IN ROLE sec_limiter
       CONNECTION LIMIT 20;
   ALTER ROLE tributary_sec SET statement_timeout = '2s';
   SQL
   ```

   Generate the password with `openssl rand -hex 24`; `\password tributary_sec` in psql avoids
   it landing in shell history.
3. Let pods reach it: in `/etc/postgresql/16/main/pg_hba.conf`, next to the existing line for
   the `tributary` login from the k3s pod network, add

   ```
   hostssl  tributary  tributary_sec  10.42.0.0/16  scram-sha-256
   ```

   then `sudo -u postgres psql -c "SELECT pg_reload_conf()"`. Check from the host:
   `psql "postgresql://tributary_sec@<hostAddress>/tributary?sslmode=require" -c "SELECT * FROM
   ops.sec_take_token('bulk')"` works and `SELECT * FROM ops.sec_rate_limit` is denied.
4. Add the key with SOPS (never in plaintext): `sops deploy/secrets/tributary.yaml`, under
   `stringData` add
   `sec-limiter-database-url: postgresql://tributary_sec:<password>@<hostAddress>:5432/tributary`,
   save, commit, and wait for Flux to apply the `secrets` Kustomization
   (`kubectl -n tributary get secret tributary -o jsonpath='{.data}' | grep -c sec-limiter`).
5. Flip the flag in [deploy/flux/releases/tributary.yaml](../../flux/releases):
   `secLimiter: {dedicatedLogin: true}`. Verify: `kubectl -n tributary get deploy poller -o
   jsonpath='{..secretKeyRef.key}'` lists `sec-user-agent sec-limiter-database-url` and no
   `database-url`; `tributary_sec_limiter_fallback` stays 0 and
   `tributary_sec_limiter_fallbacks_total` doesn't rise (a failing login drops callers to the
   local 2 req/s fallback, it doesn't stop them).

Rollback: set the flag back to false. Remove the login only after that rollout
(`DROP ROLE tributary_sec`).
