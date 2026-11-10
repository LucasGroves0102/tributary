# tributary

Helm chart for the Tributary services on k3s. Kafka, Kafka Connect and OpenFGA run separately
in the `platform` namespace; Postgres runs on the host.

## Prerequisites

- Postgres reachable from pods at `hostAddress` (default `10.42.0.1`, the k3s bridge), with the
  `tributary`, `app` and `openfga` databases, the `query_api`, `ops_reader` and `sec_limiter`
  group roles (migrations 007, 010 and 022 create them only when the login may create roles; on
  hermes the owner made them), and `query_api` granted to the login.
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
kubectl -n tributary create job --from=cronjob/insiders-backfill insiders-backfill-1  # Forms 3/4/5, last 90 days
```

## Deployments and CronJobs

`services` become Deployments and `jobs` become CronJobs ([Components](#components)). On hermes,
with the Flux release's overrides:

| Deployment | Image (command) | Replicas | Does |
|---|---|---|---|
| `poller` | poller | 1 | EDGAR's latest-filings feed every 30 s → `filings.raw` |
| `bronze-loader` | bronze-loader | 1 | `filings.raw` → `bronze.filings` |
| `doc-fetcher` | doc-fetcher | 1 | tracked companies' 10-K/10-Q/8-K, every operating company's new 10-K, 10-Q and material 8-K, and Forms 3/4/5 → bronze, `documents.fetched` |
| `parser` | parser-rs | 1 | `documents.fetched` → `silver.filing_sections` |
| `doc-crawler` | doc-fetcher (`doc-crawler`) | 1 (chart default 0) | the document crawl (below) → bronze, `documents.backfill` |
| `parser-backfill` | parser-rs | 1 (chart default 0) | `documents.backfill` → `silver.filing_sections` |
| `authz-sync` | authz-sync | 1 | the app database's CDC topics → OpenFGA tuples and the `authz` tables |
| `query-api` | query-api | 1 | the Query API, port 8000 |
| `agent-api` | agent (`agent-api`) | 1 | the assistant, port 8001 |
| `web` | web | 1 | nginx with the built web app, port 8080; the tunnel's only target |

CronJob schedules are New York time (`timeZone`). Every job has `concurrencyPolicy: Forbid`,
`backoffLimit: 2` and a deadline of `timeoutSeconds` (default 1 h); finished Jobs are kept 7 days.

| CronJob | Image (command) | Schedule | On hermes | Does |
|---|---|---|---|---|
| `backfill` | poller (`poller-backfill --days 30`) | daily 02:00 | on | completeness check against 30 days of EDGAR's daily indexes; republishes missing filings (027) |
| `companies` | company-loader | daily 03:00 | on | company identity: CIKs, tickers, former names |
| `financials` | facts-fetcher | daily 04:00 | on | tracked companies' XBRL companyfacts → bronze, silver, gold |
| `financials-bulk` | facts-fetcher (`facts-fetcher-bulk`) | daily 05:00 | on | every filer's XBRL facts from `companyfacts.zip`, in batches of 500 changed companies |
| `submissions` | company-loader (`company-loader-submissions`) | daily 05:30 | on | every filer's filing history and metadata from `submissions.zip` |
| `insiders-catchup` | doc-fetcher (`doc-fetcher-insiders`) | daily 08:45 | on | Forms 3/4/5 of the last 7 days the live doc-fetcher missed (059) |
| `filing-facts` | facts-fetcher (`facts-fetcher-filings`) | daily 09:30 | on (Flux release) | filings companyfacts lacks, from their own XBRL (055) |
| `demo-cleanup` | query-api (`query-api-demo-cleanup`) | hourly at :15 | on | deletes expired demo sandboxes |
| `segments` | facts-fetcher (`facts-fetcher-segments`) | hourly at :20 | on (Flux release) | segment and KPI facts from the stored documents' inline XBRL (018) |
| `section-index` | parser (`parser-index`) | hourly at :40 | on (Flux release) | search passages and section diffs (021) |
| `alerts` | query-api (`query-api-alerts`) | every 10 minutes | on | filing alerts and webhooks (app migration 016) |
| `business-peers` | parser (`parser-peers`) | Sundays 05:30 | on | peers by business (047) |
| `financials-rebuild` | facts-fetcher (`facts-fetcher --rebuild`) | suspended | by hand | re-derives silver and gold from bronze |
| `insiders-backfill` | doc-fetcher (`doc-fetcher-insiders`, 90 days) | suspended | by hand | Forms 3/4/5 history |
| `seed-documents` | doc-fetcher (`doc-fetcher-seed`) | suspended | by hand | tracked companies' recent 10-K/10-Q/8-K → `filings.raw` |

A suspended job keeps a placeholder schedule (January 1) and runs only when started with
`kubectl -n tributary create job --from=cronjob/<name> <name>-1`.

`financials-bulk` and `submissions` run nightly; their comments in `values.yaml` describe the
first, long run. `segments` and `section-index`, which read the crawled documents, ship
suspended; on hermes both run hourly (`suspend: false` in the Flux release) since the crawl
finished.

`insiders-catchup` (doc-fetcher image, `doc-fetcher-insiders`, migration 059) runs daily at 08:45,
after the nightly `submissions` load, and is not suspended: the same command as `insiders-backfill`
over the last 7 days (`INSIDERS_DAYS`), so an insider filing the live doc-fetcher missed (an outage,
a feed gap) is stored the next morning. Filings kept as failed in `ops.insider_queue` (gone, no XML,
refused by Postgres) are left by the live queue and retaken once a run, after every filing never
tried, while they are inside the run's window. `insiders-backfill` has a 17 h deadline (a 90-day run
is 2 h with the bulk class idle, up to ~15 h beside the document crawl); one cut off loses nothing,
the next run takes only what is still missing.

`business-peers` (parser image, `parser-peers`, migration 047) runs weekly (Sundays 05:30) and is
not suspended: it reads each company's latest 10-K Item 1 and replaces the peers-by-business tables
in one transaction (79 s, 406 MB on 2026-11-09's 4,888 documents; limit 1 GiB). Until its first run
the Query API suggests peers by SIC and size; start one by hand after the deploy:
`kubectl -n tributary create job --from=cronjob/business-peers business-peers-1`.

`alerts` (query-api image, `query-api-alerts`, app migration 016) runs every 10 minutes and is
not suspended: it makes filing alerts for each user's watched and analysed companies from the last
3 days of the filing index, reads their figures and red lines as that user (OpenFGA, then RLS) and
sends them on to users' webhooks with retries (Query API README, "Filing alerts"). It is idempotent
on the filing, so a missed or overlapping run changes nothing (and `concurrencyPolicy: Forbid`). It
uses `postgres`, `app-postgres`, `openfga`, `jwt` (webhook signing secrets are derived from it) and
`webhooks`: HTTPS to public addresses only, under the code's own checks; no Kafka, no SEC. Its
`PUBLIC_APP_URL` is where alert links point. Email stays off: setting `ALERT_SMTP_HOST`,
`ALERT_SMTP_FROM` (and `ALERT_SMTP_USER` / `ALERT_SMTP_PASSWORD` from the secret) in its env would
turn the SMTP provider on, but nothing is sent until users can verify an address. `allow-internet`
opens port 443 only, so email also needs `alertEmail.enabled: true` (and `alertEmail.port`, 587 by
default, equal to `ALERT_SMTP_PORT`): that renders `allow-smtp`, the SMTP port to public addresses
for pods that `uses: email` (only the alerts job; `tests/check_alert_email.py` checks both renders
in CI). query-api also `uses: webhooks`, for the settings page's "Send a test".

`filing-facts` (facts-fetcher image, `facts-fetcher-filings`, migration 055) reads filings SEC
companyfacts lacks from their own XBRL, daily at 09:30 once enabled (`uses: postgres, sec`: an
instance fetch is two requests at the batch class). It ships suspended; the first run goes back to
2019 and is started by hand with its arguments
([facts-fetcher](../../../services/facts-fetcher/README.md), "Filings companyfacts lacks"), then
`suspend: false` in the Flux release. On hermes that first run was 2026-11-09 (293 filings) and the
daily run is on.

`financials-rebuild` ships suspended and never runs on a schedule: it re-derives silver and gold from
the payloads already in bronze (`facts-fetcher --rebuild`, no SEC requests, only `uses: postgres`),
after a change to `silver.metric_tags` or to gold's SQL rules. Run it once by hand after such a
migration deploys:

```bash
kubectl -n tributary create job --from=cronjob/financials-rebuild financials-rebuild-1
```

A few companies first, to look at before everything (CIKs, comma-separated):

```bash
kubectl -n tributary create job --from=cronjob/financials-rebuild financials-rebuild-try \
  --dry-run=client -o json \
  | jq '.spec.template.spec.containers[0].command += ["--ciks", "0001045810,0000320187"]' \
  | kubectl apply -f -
```

It commits every 50 CIKs, so a stopped run is safe to start again from the top; each company's
quarters, TTM and checks are replaced in one transaction, so readers see either the old rows or
the new ones. `timeoutSeconds` is 6 hours; a 645-company sample (125 MB of payloads) took about
3 minutes on a laptop, and hermes' 24,000 payloads are 11 times that, so expect 35 minutes to 2 hours.
The run of 2026-11-09 (after 038-060) took 2 h 05 min over 412 batches; migration 063 takes about a
quarter off that on the same batches (db/README.md "063" says where the rest goes).
Run `VACUUM ANALYZE` on `gold.quarterly_financials` and `gold.derived_financials` afterwards.

The document crawl (`doc-crawler` + `parser-backfill`, see
[doc-fetcher](../../../services/doc-fetcher/README.md#document-crawl)) ships with `replicas: 0`
(a service's `replicas` defaults to 1). Start it by setting both to 1 under `values.services` in
`deploy/flux/releases/tributary.yaml`; `kubectl scale` would be undone by the next Helm upgrade.
On hermes both run at 1 and stay on after the backfill: the crawler re-plans every
`CRAWL_PLAN_HOURS` and fetches what the live doc-fetcher leaves (amendments, daily-index filings,
8-Ks outside its items, fetch errors). Since 2026-11-09 doc-fetcher itself stores every operating
company's new 10-K, 10-Q and material 8-K from the feed within minutes (`FETCH_OPERATING`).
The crawl's window is `CRAWL_YEARS` in `values.yaml`: 11 since 2026-11-09 (it was 5), so the plan
reaches back to November 2015 for the documents of 2016 onward. `CRAWL_SINCE`, a fixed start date
that can only reach further back, is not set; the measured cost of earlier years is in the
doc-fetcher README, "Documents before 2021". `CRAWL_8K_ITEMS` takes the results, Reg FD and
other-events items (2.02, 7.01, 8.01) plus the material events (deals, debt, leadership,
impairments, auditor changes, non-reliance).

## Components

Each service and job lists what it `uses`; that decides its environment variables, which Secret
keys it can read, and its network access:

| uses | Env | Network |
|---|---|---|
| `postgres` | `DATABASE_URL` | host Postgres |
| `app-postgres` | `APP_DATABASE_URL` (query-api: watchlists, workspaces, demo sandboxes, password accounts, filing alerts; demo-cleanup; alerts) | host Postgres |
| `kafka` | `KAFKA_BOOTSTRAP_SERVERS`; with `kafka.sasl.enabled` also `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME` (`kafkaUser`, default the service/job name) and `KAFKA_SASL_PASSWORD` (`kafka-password-<user>`) | Kafka in `platform` (9092, or the SASL listener 9094) |
| `openfga` | `FGA_API_URL`, `FGA_API_TOKEN` when `openfga.auth.enabled` | OpenFGA in `platform` |
| `sec` | `SEC_USER_AGENT`; `SEC_LIMITER_DATABASE_URL` (main database: `database-url`, or `sec-limiter-database-url` with `secLimiter.dedicatedLogin`) and `SEC_CALLER_CLASS` (`secClass`, default `live` for services, `batch` for jobs) for the shared SEC rate limit | HTTPS to public addresses, host Postgres |
| `anthropic` | `ANTHROPIC_API_KEY` | HTTPS to public addresses |
| `github` | `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` (`github-client-id` / `github-client-secret`, both `optional: true`), `GITHUB_REDIRECT_URI` (`github.redirectUri`) | HTTPS to public addresses |
| `market-data` | `DATA_CONNECTIONS_KEY` (`data-connections-key`, `optional: true`) | HTTPS to public addresses |
| `webhooks` | | HTTPS to public addresses (users' alert webhooks: query-api's test send, the `alerts` job; query_api/webhooks.py refuses private addresses first) |
| `email` | | SMTP (`alertEmail.port`, 587) to public addresses, only when `alertEmail.enabled` (the `alerts` job's email digest; off) |
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

hermes' `tributary` secret has no `data-connections-key`, so the feature is off there.

Then `GET /api/query/v1/connections` with a token answers 200 instead of 404, and Settings →
Connections appears in the web app. Keep the master key backed up with the age key: losing it
makes every stored API key unreadable (users would reconnect; their prices can be refetched).
To turn the feature off again, remove the key (`sops deploy/secrets/tributary.yaml`) and
restart query-api; stored connections stay encrypted in the app DB until users disconnect.

## Read-only operator access

`templates/operator-access.yaml` (`operatorAccess.enabled`, on) creates a ServiceAccount, `operator-readonly`,
that can get, list and watch pods, pod logs, events, jobs, cronjobs, deployments and replicasets in this
namespace, and nothing else: no secrets or configmaps, no `pods/exec` or port-forward, no create, patch or
delete. Creating Jobs is deliberately left out, because a Job can mount any secret in the namespace. It is
for watching a rebuild or a backfill and reading its logs from the server's own shell without `sudo`.

Its token is made by hand on the server and never stored in git. As the owner, on hermes (the token goes
straight into a file only your login can read; nothing is printed):

```bash
sudo -v
mkdir -p ~/.kube
CA=$(sudo kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
TOKEN=$(sudo kubectl -n tributary create token operator-readonly --duration=720h)
umask 077
cat > ~/.kube/operator-readonly.yaml <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
- name: k3s
  cluster:
    server: https://127.0.0.1:6443
    certificate-authority-data: $CA
users:
- name: operator-readonly
  user:
    token: $TOKEN
contexts:
- name: operator-readonly
  context: {cluster: k3s, user: operator-readonly, namespace: tributary}
current-context: operator-readonly
KUBECONFIG
unset CA TOKEN
kubectl --kubeconfig ~/.kube/operator-readonly.yaml get jobs
```

The token lasts 30 days; run the same commands again to renew it. To revoke every token at once:
`sudo kubectl -n tributary delete serviceaccount operator-readonly` (the next Flux reconcile recreates the
account with no valid tokens), or set `operatorAccess.enabled: false`.

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
`tests/check_sec_limiter.py`). hermes' release doesn't set the flag: its pods use `database-url`.

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
