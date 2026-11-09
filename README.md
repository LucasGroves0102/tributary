# Tributary

**A real-time SEC filings research platform where an AI analyst answers with citations, and only from the data you're allowed to see.**

**[Live demo → tributary.lucasgroves.dev](https://tributary.lucasgroves.dev)** · click *Try the demo* for your own
sandbox: every company's financials and filings, with a starter watchlist (PepsiCo, Coca-Cola,
Microsoft, Nvidia, Costco, JPMorgan) and workspace.
**[Data status](https://tributary.lucasgroves.dev/#/status)** (no sign-in): feed freshness, EDGAR-to-database
latency, completeness against EDGAR's daily indexes, coverage, data-quality checks and the last
recorded test-suite runs, live from the production database.

| | |
|---|---|
| **0 of 59** | permission attacks leaked data ([adversarial suite](tests/adversarial/report.md)) |
| **128 of 128** | figures in the agent's answers traced to a filing or XBRL fact ([citation suite](tests/citations/report.md)) |
| **< 0.2 s** | to revoke access everywhere after a grant changes: 0.11–0.17 s over 16 revocations in the recorded run ([report](tests/propagation/report.md)), up to 0.19 s in others; measured on every CI run, which fails above 3 s (BRD target: 30 s) |
| **43 s / 1.8 min** | p50 / p95 from EDGAR acceptance to Postgres during EDGAR business hours (5,312 filings, last 7 days; target 2 min). Overall p95 is 2.9 min: filings accepted after 5:30 pm ET mostly reach EDGAR's feed the next morning |
| **989,666** | SEC filers indexed; 6,024 operating companies with quarterly financials from XBRL |
| **265,625** | 10-K, 10-Q and 8-K filings from 5,342 companies over 5 years, crawled in 27 h at the SEC's 10 requests/s with 0 failures and parsed into 1.78M sections; 272 GB of HTML stored as 17.9 GB |
| **26M + 26M** | filing records and XBRL facts, kept current by nightly batch loads on one self-managed server |

## Architecture

```mermaid
flowchart LR
  subgraph SEC["SEC EDGAR"]
    feed[latest-filings feed]
    idx[daily index + bulk submissions]
    xbrl[XBRL companyfacts]
    docs[10-K / 10-Q / 8-K documents]
  end
  subgraph ingest["Ingestion (Python, shared SEC rate limiter)"]
    poller[poller]
    loaders[bulk loaders]
    fetcher[doc fetcher + crawler]
  end
  subgraph kafka["Kafka"]
    raw[(filings.raw)]
    fetched[(documents.fetched)]
    cdc[(app CDC topics)]
  end
  parser["parser (Rust)"]
  subgraph pg["Postgres medallion"]
    bronze[(bronze: raw)]
    silver[(silver: conformed, one company id)]
    gold[(gold: quarterly financials, sections, provenance)]
  end
  subgraph access["Access path"]
    appdb[(app DB: users, plans, grants)]
    sync[authz-sync]
    fga[OpenFGA]
    rls[(authz tables + RLS)]
  end
  api[Query API]
  agent["Claude agent (tools = Query API)"]
  web[Web app]

  feed --> poller --> raw --> bronze
  idx --> loaders --> bronze
  xbrl --> loaders
  docs --> fetcher --> fetched --> parser --> silver
  bronze --> silver --> gold
  appdb -- Debezium --> cdc --> sync --> fga
  sync --> rls
  web --> api
  web --> agent --> api
  api -- check first --> fga
  api -- then query as the user --> gold
  rls -. filters rows .- gold
```

Everything runs on one server ("hermes"): k3s, Helm charts deployed by Flux (GitOps) from this
repo, secrets encrypted with SOPS/age, Prometheus + Grafana + Loki with alerts, an outside
uptime check from GitHub Actions, nightly backups with a restore check, and a Cloudflare Tunnel
in front (Terraform-managed GitOps bootstrap and Cloudflare edge). GitHub Actions runs every test and builds the images; Flux deploys them.

## Design decisions

- **Two permission layers, not one.** The Query API asks OpenFGA before every query, then runs
  the query as a restricted Postgres role with the user's id set, so row-level security filters
  again. A bug in either layer alone can't leak data. SEC data is open to every signed-in user
  through a public catalog, but it goes through the same two layers: one row in the app database
  opens or closes it, and per-company grants and plan tiers still apply to anything outside it.
  Watching a company never grants access; that trap is in the adversarial suite.
- **Permissions are data, streamed.** Grants live in the app database; Debezium streams them to
  Kafka, and `authz-sync` is the only writer of OpenFGA tuples and RLS rows. It replays the
  compacted topics from the beginning and applies diffs, deletes first, so revocation is never
  lost or reordered.
- **The agent can't choose whose data it reads.** Its tools take no user parameter; it holds
  the asking user's token and calls the same Query API. Every figure in an answer is verified
  against what a tool returned before the answer reaches the user.
- **Medallion in SQL, provenance on every row.** Bronze keeps raw SEC payloads, silver resolves
  every CIK, ticker and former name to one company id, gold carries the filing, section or XBRL
  tag, ingested-at and freshness that the agent cites. Transforms are SQL functions, rebuildable
  from bronze.
- **One SEC rate limit across every pod.** A token bucket in Postgres (one locked row) keeps all
  services together under the SEC's 10 requests/s, with live traffic ahead of bulk crawls.
- **Rust where it pays.** The document parser was rewritten in Rust against a written spec and
  swapped in after producing identical sections to the Python reference on every test filing;
  it has parsed every filing since, including the 5-year crawl ([benchmarks](benchmarks)).
- **Batch, don't flood.** Bulk loads are capped batches with checkpoints and content hashes, so a
  re-run only touches what changed and a crash resumes where it stopped.

## Quickstart

Runs the whole stack locally with Docker Compose.


```bash
cp .env.example .env   # set SEC_USER_AGENT to your name/email
make hooks             # pre-commit secret scan (gitleaks)
make up                # Kafka, Postgres, OpenFGA
make migrate           # create/upgrade the tributary and app databases
make seed-app          # demo users (alice, bob, carol, dave) with plans and grants
make authz             # OpenFGA store + model (authz/model.fga)
make cdc               # Debezium: stream the app database's access tables into Kafka
make companies         # load company identity (FR5); then: SELECT * FROM silver.resolve_company('FB');
make financials        # XBRL financials for tracked companies → gold.quarterly_financials
make seed-documents    # load recent 10-K/10-Q/8-K history (fetched and parsed by up-all)
make exhibits          # 8-K press releases (EX-99.x) for 8-Ks already stored
make up-all            # + poller, bronze-loader, doc-fetcher, parser, authz-sync, query-api,
                       #   agent-api, and the chat UI on http://localhost:3000
make backfill DAYS=7   # fill in filings missing vs EDGAR's daily indexes
make psql              # query it: SELECT * FROM bronze.filings ORDER BY filed_at DESC LIMIT 10;
make consume           # watch filings arrive (or open the Kafka UI at http://localhost:8080)
```

## Access control

Every signed-in user (a demo sandbox, a GitHub account, an owner-created password account, the
named test users) reads every
company's financials and filings, within their plan's tiers: the **public catalog** `catalog:sec`
holds every company with gold financials or a stored filing, and while it is public OpenFGA lets
every user read it (`catalog:sec#reader@user:*`) and row-level security accepts its companies
(`authz.public_access`). A request without a user reads nothing. Per-company grants (to a user
or an organization) still work for anything outside the catalog, and saved work (workspaces,
theses, models, reports, views) and a user's own market-data key and prices stay owner-only.

There is no public signup. Besides the demo and GitHub, the owner can create username/password
accounts for testers with `query-api-account create <username> --questions-per-day N` inside the
Query API pod: one app-DB transaction (user, plan, and the GitHub account's grants while the
catalog is closed) that CDC turns into access like any other, scrypt-hashed passwords, one 401
for every refusal, a lockout after 10 failures, a per-address attempt limit, and each account's
own daily assistant cap (Query API README, "Password accounts").

The switch is one app-database row, streamed like any grant:

```sql
UPDATE public_catalogs SET public = true  WHERE catalog_id = 'sec';  -- open (db/app README)
UPDATE public_catalogs SET public = false WHERE catalog_id = 'sec';  -- close again
```

Change a grant, a plan or the switch in the app database and OpenFGA and the RLS tables follow
in under 0.2 s (0.11–0.17 s for revocations in CI, [report](tests/propagation/report.md);
`make propagation-test`, run in CI on every push, which fails above 3 s; target under 30 s):

```
app DB ─(Debezium CDC)─▶ Kafka ─▶ authz-sync ─┬─▶ OpenFGA (checked by the Query API)
                                              └─▶ authz.user_access, user_tiers, public_access
                                                  (row-level security)
```

### Query API

Every data request is checked twice: OpenFGA before any query, then Postgres row-level
security on the rows themselves (`services/query-api`). Running locally, the interactive API docs
are at `localhost:8000/docs`.

```bash
curl -H "Authorization: Bearer $(make -s token USER=alice)" \
  "localhost:8000/v1/companies/0001045810/financials?metrics=revenue"   # 200, cited
curl "localhost:8000/v1/companies/0001045810/financials?metrics=revenue" # 401: no user, no data
```

### Web app

`services/web` (React, TypeScript, Vite): a research workspace with Home (watchlist, new and
expected filings), guided [Analysis](#analysis) projects from filings to a checked thesis, a screener over every company, company tearsheets (statements, revenue,
segments, ratios, filings, peers, [earnings releases and the 8-Ks since the last report](#earnings-and-events)), a chart builder with formula layers, comps tables, a filing reader
with "what changed" diffs, and full-text search. The assistant sits on every screen, sees what
you're looking at, and can build charts, screens and comps for you, with Undo. Model output is
sanitized and served under a strict Content-Security-Policy. `make web` runs the dev server on
:5173. Besides *Try the demo*, you can *Sign in with GitHub* (no scopes, public profile only) for
a permanent account that keeps your saved work, or *Sign in* with the username and password of
an account the owner created.

#### Thesis and Model

Research ends in your own argument, so a workspace holds what you write next to what you chart.
The [Analysis](#analysis) tab strings all of this into one guided path per company; the pieces
also work on their own:

- **Revenue view** (a company's Revenue tab): revenue by business segment, geography and
  product over time from the filings' XBRL (members missing a period shown as gaps, never zeros;
  bars stack only when the members add up to reported revenue), each segment's contribution to
  revenue growth in percentage points, and receivables against revenue (DSO, flagged when
  receivables grow more than 10 points faster than revenue). Each chart saves to a workspace so
  a thesis can embed it.
- **Model**: a forecast for one company, from revenue down to EPS and free cash flow. Its
  history comes live from the filings (as you, through the permission-checked API), and the
  current fiscal year starts from its reported quarters: Vertiv's FY2026 is Q1-Q3 as reported
  plus the Q4 you set, Deckers' FY2027 is H1 plus H2. Per forecast year (1-10) you type growth
  and operating margin, and optionally interest, tax rate, share change (buybacks) and FCF
  margin; anything left blank follows the company's own history, and one line per default says
  how. Name one-off adjustments (Deckers' $46M of tariff refunds) to see margins reported and
  adjusted, and acquired revenue for organic growth. The company's latest outlook sits beside the
  year it covers, quoted with its 8-K. Type a price and its date to get market cap, P/E,
  EV/Revenue, EV/EBIT and FCF yield on history and every case; it's your price, nothing is
  fetched. **Bear, base and bull** sit side by side in one table, every line, and on one chart.
  Every save keeps the version it replaced. Only your inputs are saved; the maths run in one
  place, the Query API (`POST /v1/companies/{id}/model`), for the page, the thesis check and
  later MCP.
- **Thesis**: your Markdown about a company, with any chart, comps table, screen or model from
  the same workspace embedded where you put it, drawn live. Write, preview or both side by side;
  import a `.md` file and export one (embeds become `[Chart: name]` lines). Rendered through the
  same sanitizer as model output.
- **Check my thesis**: the agent reads your saved thesis as you, marks each factual claim
  Supported, Contradicted (with what the filing says) or Can't verify, each with citations that
  open the filing; compares each embedded model's growth and margin per year (its base case)
  with the company's 5-year history (computed in code, flagged when growth is more than 10 points off the CAGR or a
  margin is outside the 5-year range); and lists what the latest 10-K/10-Q says that you don't.
  Every figure goes through the same verifier as chat; anything it can't trace is flagged.
  Checks are kept with the thesis (history, stale once the text changes) and count as 4
  questions. The thesis reaches the model as escaped data, and its tools stay limited to the
  thesis's companies.
- **Revisit**: each check records the newest 10-K/10-Q in the SEC index for every company it
  covered, and the newest filing its citations rely on. When the company files a newer one, the
  thesis (and its workspace's list) says "New 10-Q filed <date> since your last check —
  re-check?"; after the re-check, "Changes since <date>" lists the claims whose verdict flipped
  (matched by quote), new and dropped claims, and model assumption flags that changed.

Both start from a company page or a workspace ("New thesis", "New model"), are owner-only in
both layers like all saved work (the Query API filters on the token's user; the app database's
row-level security is forced), and grant nothing: an embedded chart or a model's history is read
as the viewer, so it shows only what you may read.

#### Analysis

The Analysis tab is one guided project per company, the way an analyst builds a thesis from
filings. "New analysis" picks a company you can read and its peers (suggested from the same
industry and size); the project then walks eight steps, each with a done marker, under a header
with the company and tabs (Company · Charts · Model · Thesis · Monitor) that switch instantly
without losing anything you typed:

1. **Business**: the latest 10-K's Item 1, the segments, and your "how it makes money" notes.
2. **Numbers**: ten fiscal years of revenue and growth, gross and operating margin, free cash
   flow (operating cash flow − capex), receivables against revenue, and debt; each chart opens
   in the chart builder or saves to the project.
3. **Earnings**: each quarter's earnings release beside its reported figures, the outlook it
   states and management's adjustments ([below](#earnings-and-events)).
4. **What changed**: the 8-Ks filed since the last 10-Q or 10-K (deals, debt, leadership,
   guidance), then new, removed and rewritten Risk Factors and MD&A paragraphs in the latest
   10-K and 10-Q, a 10-Q against the same quarter a year earlier, the most material first, with
   a topic filter ([What changed](#what-changed)).
5. **Peers**: the comps table for the company and its peers.
6. **Model**: the model, this year from its reported quarters, with bear, base and bull side by
   side.
7. **Thesis**: starts from a template: core thesis (3-5 sentences), variant perception
   (what the market misses), pillars with evidence, catalysts (the next expected filings
   prefilled), risks and mitigants, what would prove you wrong, and KPIs to watch.
8. **Monitor**: Check my thesis and the new-filing notice; KPIs with red lines checked after
   every filing come next.

**Add to thesis** on any step pins the evidence into a section you pick: a chart or comps
table is saved in the project and embedded, a filing passage is quoted with its form, the period
it reports on, date, section and an EDGAR link (a release's guidance table keeps its lines). Unsaved thesis edits are kept in the browser until you save. A
project is a workspace with its company and peers on it (app database migration 012), so its
charts, model and thesis are ordinary saved items: owner-only in both layers, granting nothing.

#### Earnings and events

Between 10-Qs the filings an analyst reads are 8-Ks: the quarterly earnings release and whatever
the company did since the quarter ended. Both testers of the guided flow missed one: an
acquisition that closed after Vertiv's balance sheet date, and the guidance and refund figures in
Deckers' release.

- **Earnings tab** (and the Analysis flow's Earnings step): each quarter's release (the 8-K with
  Item 2.02, its first EX-99 exhibit) beside the quarter's figures from its 10-Q against a year
  ago; the outlook quoted from the release's own Outlook or Guidance sections; management's
  adjustments (its non-GAAP reconciliation tables and the notes that state a dollar amount). Each
  passage links to its place in the filing reader and pins into a thesis with its quarter. Found by
  fixed rules, not a model: where a release has no such section the page says so. No beat or miss:
  Tributary holds no consensus estimates.
- **Since the last report** (company Overview, top of What changed): the 8-Ks filed after the
  latest 10-Q/10-K period end with their items (2.01 acquisition completed, 2.03 new debt, 5.02
  officer change, 4.02 non-reliance, ...), each quoting the 8-K's own words. A balance that an
  event after the period end may have changed (cash, debt, net debt, total assets) is flagged as
  of that period end, with the event beside it, on the key stats too: a flag, never a pro forma.
  A closed acquisition or a new financing announced only in a 7.01 or 8.01 press release (Vertiv
  closed KES that way on Oct 26, 2026) is flagged too, read by fixed phrases from the release, with
  the words that triggered it shown; an agreement to acquire, an earnings release or a buyback is
  not.
- The assistant has both as tools (`get_earnings_release`, `get_events_since_report`), and Check
  my thesis reads them for what the thesis leaves out; neither changed the recorded suites (97
  replays pass, 0 leaks), and the adversarial harness counts their results like any filings
  tool's.

Measured on production (2026-11-08): every 8-K filed since October 2024 has its items in the SEC
index here (140,884 of 140,884, all filers); for operating companies over the last two years,
32,188 earnings 8-Ks from 3,994 companies, all stored, 95% with a parsed EX-99.1, and 71% of
40,588 company-quarters with a parsed release. On 45
releases sampled across industries, the outlook rules found 32 of the 33 that have an Outlook or
Guidance section, and nothing in the others. Rules and numbers:
[query-api](services/query-api/README.md#earnings-and-events), [web](services/web/README.md).

#### What changed

Both testers read filing diffs before earnings and both found them noisy: Deckers' Q2 10-Q against
its Q1 10-Q had 121 changes, half of them fragments of paragraphs a page break split, its
three-month text diffed against six-month text; Vertiv's showed "second" becoming "third" quarter.
What changed now compares a 10-Q with the same quarter a year earlier by default and reads the
stored diff a second time, at query time (nothing is rebuilt): split paragraphs are joined, page
furniture dropped, calendar rollovers, figure-only table rows and minor edits hidden and counted,
and a quarter's passage is never paired with a year-to-date one. Each change says its period, the
figures it adds or drops, and why it ranks where it does (new figures, how far a figure moved, new
risk wording, Risk Factors and Liquidity above boilerplate); a topic ("tariffs") and "only passages
with new figures" filter it. Measured on production's texts: Deckers' Q2 FY27 10-Q 147 changes
against a year ago before, 91 after, 10 about tariffs (the $46.0M IEEPA refund second); Vertiv's Q3
10-Q against Q2 39 → 22, nine of the hidden ones calendar rollovers; Deckers' 10-K 379 → 264. The
assistant asks for the same view with `get_material_changes`. Rules and numbers:
[query-api](services/query-api/README.md#what-changed), [web](services/web/README.md).

#### Connect your data

Share prices can't be shown publicly (vendors license them for personal use, not
redistribution), so each user brings their own: in Settings → Connections you add your own
market-data API key (Tiingo first, behind a small adapter interface). Tributary validates it
with one call, stores it encrypted (AES-256-GCM envelope; the master key lives in the
deployment's secrets, never in the database; the ciphertext is bound to your user), and shows
you only the provider and its last four characters. End-of-day prices are fetched with your
key when you open a company, at most once a day, and stored for you alone: no cache is shared
between users. With a connection, a company's Price tab shows the daily close and a valuation
(market cap, P/E, P/S, EV/EBIT from your latest close and the latest filed figures, each with
its date), and the chart builder gets a price layer. Another user never sees your key or your
prices, in either permission layer (OpenFGA on the company, then owner-only row-level
security). Disconnecting deletes the key and every price fetched with it.

**Off until configured:** the feature exists only when the deployment has the
`data-connections-key` secret ([deploy/helm/tributary](deploy/helm/tributary/README.md#connect-your-data));
without it the routes are absent and the UI hides it. The assistant has no price tool yet.

### Agent

A Claude tool loop over the Query API (`services/agent`), served by `agent-api` as
server-sent events with per-user rate limits and a daily spend cap. Its tools take no user
parameter: the agent holds the asking user's token, so it sees exactly what that user may
read, and it cites the filing, section or XBRL tag, and freshness for every figure. Needs
`ANTHROPIC_API_KEY` in `.env`; the model is `AGENT_MODEL`. Tests use a scripted fake model.

```bash
make ask USER=alice Q="How has Nvidia's revenue changed over the last 4 quarters?"
make ask USER=alice Q="What are Nvidia's main risk factors?"
```

### Citation accuracy

Every answer is checked before it reaches the user (`services/agent/.../verify.py`): each
dollar figure must match a value a tool returned, be arithmetic on returned values, or appear
in returned filing text, and each accession number must have come from a tool. The chat shows
the result, and flags any figure it couldn't trace. On 32 questions across the demo users:
**32 of 32 answers fully verified, 128 of 128 figures traced** ([report](tests/citations/report.md)).
`make citations` runs it live; CI replays it.

### Adversarial suite

59 attacks (plan bypass, impersonation, fake system messages, watchlist tricks, indirect
leaks, tool-argument injection, ...) plus controls, run as the demo users against the live
stack with the catalog closed (per-company grants only): **0 leaks**
([report](tests/adversarial/report.md)). `make adversarial` runs it live; CI replays the
recordings. With the catalog open its targets move to what stays private (other users' saved
work, prompt injection in filings); see its [README](tests/adversarial/README.md).

## Layout

| Path | What |
|---|---|
| `services/` | One folder per deployable service or job (poller, bronze-loader, company-loader, facts-fetcher, doc-fetcher, parser, parser-rs, authz-sync, query-api, agent, web) |
| `libs/` | Shared Python packages (`sec`: SEC client with User-Agent and rate limiting; `authz`: OpenFGA client; `kafka`: Kafka client config from the environment, SASL/SCRAM included; `telemetry`: JSON logs and Prometheus metrics) |
| `db/` | Analytics migrations (`db/migrations`) and the app database (`db/app`) |
| `streaming/` | Debezium configs and Kafka topic docs |
| `authz/` | OpenFGA authorization model and its tests (`make authz-test`) |
| `tests/` | Adversarial, citation, integration, propagation, Kafka ACL and golden 10-K figure tests |
| `deploy/` | Helm charts (services, platform, monitoring), Flux, SOPS secrets, Terraform, host backup scripts |
| `benchmarks/` | Python vs Rust parser |

## License

Copyright © 2026 Lucas Groves. All rights reserved. The source is published to be read and
reviewed; it may not be copied, modified, deployed or reused without written permission (see
[LICENSE](LICENSE)). SEC data comes from EDGAR and is not covered by the notice.
