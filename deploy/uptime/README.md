# Uptime check

An outside check of the live site, so an outage is noticed even when hermes itself is down
(Prometheus and Alertmanager run on hermes and can't report their own server going away).

`.github/workflows/uptime.yml` runs `check.py` on GitHub Actions about every 5 minutes. It
checks:

| Check | Passes when |
|---|---|
| Web app (`/`) | 200 and the page is Tributary's |
| Query API (`/api/query/health`) | 200 |
| Agent API (`/api/agent/health`) | 200 |
| Pipeline status (`/api/query/v1/status`) | 200 |
| Pipeline freshness | the newest filing was ingested within 3 h during EDGAR business hours (weekdays 10 am–5 pm ET, not federal holidays), or within 4 days otherwise |

A failing request is retried once, 30 s later, before it counts. `/v1/status` returns only two
timestamps, when the newest filing was accepted by EDGAR and when it was ingested
(`ops.feed_status()`, migration 026), so the freshness check works without a login.

When something fails, the workflow opens one issue labelled `outage` that @-mentions the repo
owner (GitHub emails them). While it stays down nothing more is posted. The first passing run
comments with how long it was down and closes the issue, so the closed `outage` issues are the
outage history.

Limits: GitHub may delay scheduled runs by several minutes, and it pauses scheduled workflows
after 60 days without a commit (it emails first; any commit or a manual run re-enables it).
The check uses no accounts or secrets beyond the workflow's own token.

```bash
python3 deploy/uptime/check.py                  # run it by hand (prints the report)
.venv/bin/pytest tests/uptime                   # what counts as down or stale
gh workflow run Uptime                          # run the workflow now
```
