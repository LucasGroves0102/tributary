# deploy/helm/tributary: design notes

Longer notes behind [README.md](README.md): what each job reads and uses, its limits, the measurements
and what happened on hermes. The README keeps the jobs table and what to run by hand.

## Jobs in detail

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
