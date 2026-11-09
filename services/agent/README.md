# agent

FR9, week 2. Claude agent that answers questions about SEC filings through the
[Query API](../query-api) only, citing filing, section or XBRL tag, and freshness. The
[web](../web) chat calls it; `agent-ask` runs it from the terminal.

The agent holds the asking user's Query API token and its tools take no user parameter, so it
sees exactly what that user may read. Tool results are treated as data; denied calls come back
as tool errors the model must report.

| Tool | Query API route |
|---|---|
| `whoami` | `GET /v1/me` |
| `search_companies` | `GET /v1/companies/search` |
| `get_financials` | `GET /v1/companies/{id}/financials` |
| `list_filings` | `GET /v1/companies/{id}/filings` |
| `get_section` | `GET /v1/companies/{id}/sections/{key}` |
| `get_section_changes` | `GET /v1/companies/{id}/filings/{accession}/changes` (keeps the first `max_changes`, default 20, max 50; says how many it left out) |
| `get_material_changes` | `GET /v1/companies/{id}/filings/{accession}/changes?view=ranked` (`topic` → `q`, `new_figures` → `numbers=new`; keeps the `max_changes` highest scores across sections, default 15, max 50, says how many it left out, and drops the echoed filters) |
| `search_filings` | `GET /v1/search` |
| `get_earnings_release` | `GET /v1/companies/{id}/earnings` (`quarters` 1-4, default 1; a quarter's quoted adjustments whole up to 6,000 characters, the rest keep heading and citation and say to read them with `get_section`) |
| `get_events_since_report` | `GET /v1/companies/{id}/events` |

`get_earnings_release` and `get_events_since_report` (Query API README, "Earnings and events") give
the quarterly earnings release (its headline, the outlook it states, management's reconciliation
tables and dollar notes, each quoted with its citation) and the 8-Ks filed since the latest
10-Q/10-K period end, with the balance-sheet figures an event after it may have changed (an event
flagged from its 7.01/8.01 announcement carries `announced`: the words that matched, quoted with a
citation, so the model can say so). They were
added without re-recording: the recorded suites don't depend on the tool list, and the chat and
report prompts and existing tools are unchanged (`tests/adversarial`, `tests/citations` still 97
passing). Both need the `filings` tier, so the adversarial harness counts a result for a company
the user can't read filings for as a leak, as it does for `search_filings`.

`get_material_changes` (F3) reads What changed for materiality (Query API README, "Read for
materiality"): a 10-Q against the same quarter a year earlier unless the model asks for `prior`,
split paragraphs, calendar rollovers and figure-only rows left out and counted, each change with
its period, new and removed figures, a score and why. It is a new tool, not new parameters on
`get_section_changes` (whose schema and answer are unchanged): no re-recording (97 replays pass),
in `report.COMPANY_TOOLS` and in the harness's TOOL_TIER as `filings`. The answer drops the
echoed `filters`: a topic is the model's own words, never evidence for a figure it writes
(verify.py). Check my thesis lists it beside `get_section_changes` and, when it calls a risk
factor new, says what the filing was compared with (tester 1 saw the check call a covenant
"new" while What changed, against the prior 10-Q, showed nothing new: against a year ago,
Vertiv's covenant risk factors are new).

`get_financials`' metric names come from `GET /v1/metrics` (`MetricTools`, fetched once; until
the Query API answers, the tool lists no names and the API rejects unknown ones).

At the round limit (6 rounds of tool calls in chat, 12 in a report, 16 in a thesis check) the agent
doesn't give up and discard what it found: one more call (`agent.CLOSING`, `tool_choice` none)
answers from the tool results so far and says first what it couldn't look up or finish; the
answer's `stop` is `max_rounds`. Each model call is logged with its stop reason and token counts
(output tokens include thinking), so a `max_tokens` stop can be traced to the reply that hit it.

Before an answer is returned, `verify.py` checks every dollar figure against the tool results
(matched, derived as a sum or difference of one metric, or quoted from filing text) and every
accession number against what the tools returned; the result is part of the answer as
`verification`. A derived metric's `inputs` count as returned values (net debt's cash and debt, a
growth rate's year-ago value, a TTM's quarters), and a filing table's cells are read at the scale
that governs them, decimals included. Those two came after the recorded suites and only apply to a
figure the original rules left unverified; a figure that isn't in the data still fails. Cells are
read per section: a section stating no scale note ("(In thousands)", "($ in millions)") reads in
millions; one stating exactly one scale reads its cells after that note at that scale (before it,
in millions); one stating several reads in millions only. So no section is read more loosely than
millions alone: a share note "(in thousands)" below statements "(In millions)", a prose "(in
billions)" or a later note no longer makes the statements' 96,221 read as $96.2M or $96.2T. What
it can't do: in a section with several scales, a cell that really is in thousands still reads in
millions, as it always did. A figure written without a unit ("$96,221") isn't quoted from a
section that states a scale.
A sum or difference pairs only values that can be compared: like with like (quarters with
quarters, TTMs with TTMs: a TTM plus one of its own quarters is reported nowhere), one company's
adjacent quarters or the same quarter a year apart, or two companies' periods ending within 45
days (with forty TTM quarters in a thesis check, any two made coincidences: NVIDIA's TTM operating
income of July 2025 less April 2018's "verified" a made-up $99.9B). `/v1/search`'s echo of the
query is never evidence (a searched figure would quote itself); its snippets are filing text,
read without the « » marks. Every verdict in the recordings is unchanged, so they replay as
recorded.
Currencies (F8b): since migration 056 a foreign filer's figures are in its own currency, and
`get_financials` says so per row (`unit` "EUR", "EUR/shares") and on the answer (`currency`, only
when not USD). The system prompt still says to write dollars (changing it needs a re-record), so
`verify.py` guards the other side: a figure written with "$" that the rules verified or derived from
a row or input in another currency is checked again with dollar rows only, and is unverified unless
those verify it ("$2,097M" about Birkenstock's €2,097M fails; "2,097 million" still matches). Every
row in the recordings is in USD (a test reads them), so they replay as recorded. Not covered: a "$"
figure quoted from a euro filing's text ("2,097 million" in a 20-F) still counts as quoted.
The thesis check's H2 guard (`filing_checks`) reads the cited filing's figures with their `unit`
(057): a "$" figure never agrees with a figure in another currency, and its note words the
filing's figure in it ("net debt of €10.9B"); the stored filing figure keeps its fields (no unit:
query_api's `FilingFigure` is strict). Every amount the model reads for a non-USD company says its
currency where it reads it: the `get_financials` answer (above), the thesis check's prefetched
history (the same answer) and each model's history in `<check_data>` (`currency` beside the
amounts, from the model run's own, only when not USD so a dollar company's block is unchanged).
The other tools return no amounts of Tributary's (events, filings, search) or quote the filing's
own text, its currency sign included (sections, changes, earnings releases).

## agent-api

`POST /v1/ask` with `{"question": ..., "history": [...]}` and the user's bearer token streams
server-sent events: text deltas, tool calls and a final `done` event with the answer, citations
(each filing a tool returned, with the section keys it returned from it, so the web app can open
its reader there), verification, token usage and estimated cost. `GET /health`; `/metrics` for Prometheus
(`tributary_agent_questions_total`, `_tokens_total`, `_cost_usd_total`, `_tool_calls_total`,
`_rounds`).

The web app also sends `context` (its page, company and company tab, the reader's filing and
section, the search page's words, chart layers, screener filters, the comps set;
`ui.UiContext`: strict fields, at most 4,000 bytes). It reaches the model as a block of data
marked untrusted and never decides who the user is: that is still only the token. agent-api adds
a `propose_ui_action` tool (set/add/remove chart layers, chart options, screener filters, the
comps table's company and peers, add or remove a watchlist company). Each proposal is checked as
the user through the Query API (`/v1/me`, `/v1/companies/search`, `/v1/formulas/validate`): chart companies must be readable,
unknown and ungranted ones get the same refusal (with `/v1/me`'s public catalog open with
financials, every company the search resolves is readable). Accepted ones stream as `action` events (also in
`answer.actions`) for the app to apply with Undo; the app reads any data itself, through the
Query API. `save_to_workspace` (item `chart`, `comps` or `screen`, a name, a workspace name)
saves what the screen shows: it is accepted only if the context showed that item or an accepted
action in the same answer built it, and names are trimmed, capped and free of control
characters. The app then saves it through the Query API (which validates the state again, for
this user only) and Undo deletes the saved item. Without the web app (CLI, recorded suites)
requests are unchanged.

`GET /v1/allowance` says what's left before anything is spent, for the assistant panel's meter:
`per_hour`, `network_per_hour`, `left_this_hour` (the tighter of the two), `per_day` and
`left_today` (from the Query API, null where it doesn't count them), `paused` (the day's budget is
gone) and what a report and a thesis check take. No model call, takes nothing.

Limits: questions per hour per user and per client IP (`X-Client-IP`, set by the web proxy from
Cloudflare's `CF-Connecting-IP`), and a daily model budget across all users. Over a limit → 429.
Before each question it also takes the user's persistent daily allowance from the Query API
(`/v1/demo/agent-quota`): a demo sandbox's or GitHub account's demo caps, or an owner-created
password account's own `questions_per_day` (Query API README, "Password accounts"); the
Query API decides from the token, and its 429 message is shown as is. Production raises
`AGENT_IP_RATE_PER_HOUR` to 40 (Flux release): a tester on the owner's network shares the
owner's address, and the per-account daily caps still hold.
A report counts as 3 questions ([Reports](#reports)), a thesis check 4
([Thesis checks](#thesis-checks)). Questions are taken before the model runs (so parallel requests
can't overspend). Every model call that returns is added to the day's model budget as it returns
(the agent's internal `usage` events, never sent to the browser), so a run that fails later, or a
client that goes away, still counts what it cost. Questions are given back only for a failure that
is ours and can't be caused on purpose, and at most twice per user per UTC day (`REFUNDS_PER_DAY`,
in-process like the hour's limits): a model API error (outage, overload) before any model call
returned and before any text streamed, or a thesis check that saved nothing because its replies
were cut off at max_tokens (`server.Charge`; the Query API's `POST /v1/demo/agent-quota/refund`
for the daily caps). The stream then carries `{"type": "refunded", "questions": n}`, and the web
app says the run didn't count only then. Nothing else is given back: an error after a paid round
(a long enough tool result can push the next call past the model's context), a model that chose
not to submit, a save refused because the thesis or workspace was deleted mid-run, a client that
disconnects. A result counts, partial ones included. A refused daily quota gives the hour's
allowance back. `get_financials` asks for at most 40 quarters whatever the model passes (the
schema's limit; the Query API itself takes 120 for the web app).

The web app's `context.page` is one of home, companies, company, filing, chart, compare,
search, ask, about, workspace, report, thesis, model.

## Reports

`POST /v1/reports` with `{"workspace_id": ...}` (and `"report_id"` to write an existing report
again) writes a research note from one of the user's workspaces and saves it there as an item of
kind `report` (`report.py`; Query API README, "Reports"). Streams the same events as `/v1/ask`,
plus a `report` event (the saved item and whether every figure verified) or an `error` event if
it couldn't be saved, before `done`.

- **The user's own workspace, read as the user.** The workspace comes from the Query API with
  the user's token (`GET /v1/workspaces/{id}`): someone else's is the same 404 as a missing one,
  and nothing is spent. Its first 8 charts, comps sets and screens are the report's items
  (`REPORT_KINDS`; the user's theses and models, app migration 009, and earlier reports are
  skipped: never sent to the model, never embedded, and a workspace holding only those is a
  422 "save a chart … first"); the model gets them as a `<workspace>` data block (names are labels, `<`/`>` escaped so a name
  can't close the block) with what the user can read of each company (`/v1/me`: its listed
  companies' tiers, plus the public catalog's tiers for every company while it is open).
- **Only the workspace's companies.** The tools are the chat tools as the user, but limited to the
  companies the items name (chart layers and formula tickers, comps target and peers, each saved
  screen's top 8 rows from the screener run as the user); anything else is refused before any
  request. Ungranted companies get the Query API's permission denied, as in chat.
- **Structure.** Title, summary, one section per item with its embed marker
  (`[[embed:item-N]]`, where the web app draws the saved chart or table from its saved state),
  then risks and what changed when the user can read filings. Markers for unknown items are
  dropped; an item the model skipped still gets its own section.
- **Every number cited, every unverified figure flagged.** The answer goes through the same
  `verify.py` check as chat; any figure or accession number it can't trace to a tool result is
  listed in an "Unverified" section of the report (and highlighted inline by the web app), never
  silently kept. Citations, the newest `as_of` the tools returned and the writing time are stored
  with it.
- **Counts as 3 questions** (`REPORT_QUESTIONS`) against every limit: per user and per IP per
  hour, the daily budget, and a demo sandbox's daily caps (taken all at once, or refused with a
  message saying how many are left). Up to 12 tool rounds.

## Thesis checks

`POST /v1/thesis-checks` with `{"item_id": ...}` (one of the user's theses) checks it against
the data and saves the result with it (`thesis_check.py`; Query API README, "Thesis checks").
Streams the same events as `/v1/ask`, plus a `check` event (the saved check's summary and its
result) or an `error` event if it couldn't be saved or the model never submitted, before `done`.

- **The user's own thesis, read as the user.** `GET /v1/workspace-items/{id}` with the user's
  token: someone else's thesis, a missing one and an item that isn't a thesis are the same 404,
  and nothing is spent; an empty thesis is a 422. The models it embeds (`[[embed:<id>]]` lines
  naming `model` items of its workspace, first 4) come from `GET /v1/workspaces/{id}`.
- **Assumptions computed, not written by the model.** For each model, its company's history is
  read as the user (`get_financials`, TTM, 40 quarters: the evidence its figures verify against)
  and the model is **run by the Query API** (`model_stats.run_model`: `POST
  /v1/companies/{id}/model` with the saved state, as the user). Since F2 that route is the one
  copy of a model's maths (Query API README, "Models: one copy of the maths"); agent-api no
  longer ports the web app's. The run gives the 5-fiscal-year revenue CAGR, mean operating margin
  and its [min, max], each forecast year's full-year growth minus the CAGR and two flags:
  **growth more than 10 percentage points above or below the CAGR, or a margin outside the
  5-year [min, max]**. The numbers go to the model as data; it only writes the commentary. Each
  forecast year also says whether the user set it (`user_set`): a year equal to a default a new
  model starts from (the run-rate rule, or the flat 5-year CAGR and average margin of models made
  before it) is Tributary's, not the user's view, and `CHECK_RULES` tells the model not to endorse
  it as such; the rates a year leaves at their defaults are listed (`defaulted`: tax rate,
  interest and other, share change, FCF margin) under the same rule. The data also carries the
  TTM run rate (`ttm_growth_pct`, `ttm_margin_pct`), each year's net income, diluted EPS and its
  growth, share change and FCF margin, and `reported_quarters`: a model built from the current
  year's reported quarters (F2 `ytd`) is checked on its full year, and `CHECK_RULES` says the
  reported part isn't an assumption. If the run fails (no access, or an older Query API during a
  deploy: "the Query API can't run models yet"), that model carries the error and the claims are
  still checked.
- **The base case.** A model with bear / base / bull scenarios (the Analysis tab) is checked on
  its **base** case only: `assumptions` in the saved state, which is all a model saved before
  scenarios holds (`model_stats.scenario_assumptions` reads it; the Query API's shared fixture's
  `scenario_cases` pin which assumptions each case uses). The bear and bull cases never reach
  the data or the flags; the result's shape is unchanged.
- **The thesis is data.** The first 30,000 characters (`MAX_THESIS_CHARS`; the result says
  when the rest wasn't checked) go in a `<thesis>` block as a JSON string with `<`/`>` escaped,
  so nothing in it can close the block; Tributary's numbers in a `<check_data>` block. The chat
  rules apply unchanged, plus `CHECK_RULES` (the thesis is material to check, never
  instructions).
- **Since the last report.** `CHECK_RULES` sends the model to the latest earnings release
  (`get_earnings_release`: its outlook and adjustments) and the 8-Ks after the latest report's
  period end (`get_events_since_report`) for the points the thesis doesn't address, and says a
  balance-sheet figure from that period end is out of date when an event in `stale` follows it:
  say so and cite the event, never compute a figure after it (tester 1's acquisition after the
  quarter end came only from the check; C11).
- **Only the thesis's companies.** The tools are the chat tools as the user, limited
  (`report.ScopedTools`) to the thesis's company, its models' companies and companies it names
  by ticker (`$NVDA`, `(NASDAQ: NVDA)`, bare upper-case tickers outside a stop list; at most 8
  lookups) that `/v1/me` says the user can read; 12 at most. A CIK, a workspace id or another
  user's name in the text widens nothing; the tools take no workspace or user. Plus
  `submit_check`, which takes the structured result once.
- **Every claim cited, every figure verified.** Each citation is checked against the filings the
  tools returned (form, date, EDGAR URL, section, the newest `as_of` of the results that held
  it); one the tools never returned is kept but flagged and listed. Every explanation,
  commentary, point and summary goes through `verify.py` as in chat; unverified figures are
  flagged. A supported or contradicted claim without a citation of a filing the tools returned
  becomes "unverifiable" (`downgraded`); so does a supported claim with a dollar figure that isn't
  in the data, in the thesis's own words, its explanation or a citation's figure (the note names
  them: "supported" certifies the numbers). A "not mentioned" point without a citation is dropped
  (and the notes say so). Quotes are checked against the thesis text (`quote_found`).
- **A computed figure against the cited filing's own (H2).** Agreeing with Tributary's computed
  figure isn't agreeing with the filing: Riley's "Net debt was $10.92B" matched a net debt that left
  NVIDIA's $99.37B of securities out and was certified. A supported claim whose figure (in the quote
  or a citation's `figure`) is one of the computed figures the tools returned (`GUARDED`: net debt,
  cash and investments, free cash flow; quarter view) is compared with the same figure computed from
  each verified citation's own facts (`GET /v1/companies/{id}/filings/{accession}/figures`, read as
  the user, never a tool result, cached per check). A difference beyond the claim's rounding and
  0.5% makes it unverifiable with what the filing gives ("the 10-Q filed ... gives net debt of
  -$66.0B at ... from its own figures, not $10.92B"); each comparison is kept on the claim
  (`filing_figures`). A filing that doesn't give the figure isn't compared; an older Query API
  without the route changes nothing. `CHECK_RULES` tells the model so (cite the filing whose
  statement you checked the inputs against); `CheckResult` refuses a supported claim with a
  disagreeing filing figure.
- **Saved with the thesis.** Through the Query API with the user's token
  (`POST /v1/thesis-checks/{id}`, off the public site), with the SHA-256 of the company id and
  the text it checked (`text_hash`), the model, rounds, token usage and estimated cost.
- **What it knew of the filings (Revisit).** Before the loop, `read_filings` asks
  `/v1/companies/{id}/filing-index?form=10-K&form=10-Q&limit=1` as the user for each company in
  the scope (public SEC index metadata; a failure leaves it unknown). The result's `filings[]`
  holds per company `latest` (the newest 10-K/10-Q in the index when the check ran) and
  `evidence` (the newest filing among the result's verified citations of that company). The
  index answer is never a tool result: it reaches neither the model nor the evidence, so it
  can't verify a citation. The web app offers a re-check when the index later has a newer
  10-K/10-Q than `latest`.
- **Counts as 4 questions** (`CHECK_QUESTIONS`) against every limit, like a report's 3: a check
  runs up to 16 tool rounds (`CHECK_ROUNDS`), about 4 rounds per question as reports (12 rounds,
  3 questions); refused with a message saying so when it doesn't fit. A check that saved
  nothing counts, unless it failed in one of the two ways that are given back (see the limits
  above): a refused save, a deleted thesis or a model that didn't submit are not.
- **Room to finish** (C1). `submit_check` for a full-length thesis, after thinking, ran past the
  chat's 8,000 tokens per reply on every run; a check now has `CHECK_MAX_TOKENS` (32,000, thinking
  included, streamed). A reply cut off while thinking or calling a tool is dropped and the model
  asked once to submit more briefly (`CHECK_RETRY`); at the round limit a closing call
  (`CHECK_CLOSING`, tools kept) asks it to submit what it has checked. A result submitted that
  way is saved like any other, with the stop in `stop` and a note; one that never comes is an
  error that says why. Cut off at max_tokens even after the retry, the check is given back
  (`cut_off` on the error event, which the server strips); stopping any other way, it counts.

Result shape (`query_api.thesis_checks.CheckResult`): `thesis` (item, title, company,
`text_hash`, characters checked, truncated), `summary`, `claims[]` (`quote`, `quote_found`,
`status`, `explanation`, `citations[]` {accession, form, filed, company, url, section_key,
figure, figure_status, as_of, verified}, `figures[]`, `downgraded`), `assumptions[]` (per model:
`stats`, `margin_min`/`max`, `years[]` {fy, growth, margin, growth_vs_cagr, growth_flag,
margin_flag, commentary, citations, figures}, or `error`), `not_mentioned[]`, `notes[]`,
`citations[]`, `verification`, `as_of`, `checked_at`, `model`, `questions`, `stop`, `rounds`,
`usage`, `cost_usd`, `filings[]` {company_id, latest, evidence: {accession, form, filed} or
null} (added for Revisit; optional, `version` stays 1, so checks stored before it still read).
The Query API validates `filings` first, so deploy it before (or with) agent-api.

## Run

```bash
make up-all                                       # agent-api in docker-compose, on :8001
make ask USER=alice Q="How has Nvidia's revenue changed over the last 4 quarters?"
```

Config (env):

| Variable | Default | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | both |
| `QUERY_API_URL` | `http://localhost:8000` | both |
| `AGENT_MODEL` | `claude-haiku-5-5` | both |
| `AGENT_EFFORT` | unset | both |
| `AGENT_TOKEN` | required | `agent-ask` (`make ask` mints it) |
| `AGENT_MAX_ROUNDS` | 6 | `agent-ask` |
| `AGENT_RATE_PER_HOUR` | 30 | `agent-api`, per user |
| `AGENT_IP_RATE_PER_HOUR` | 10 | `agent-api`, per client IP (production: 40, Flux release) |
| `AGENT_DAILY_BUDGET_USD` | 2 | `agent-api`, all users, per UTC day |
| `PORT` | 8001 | `agent-api` |

## Tests

`pytest services/agent`: the tool loop runs against a scripted fake model and a mocked Query API transport;
no API key needed. `test_thesis_check.py` includes the adversarial cases (injection in the
thesis, another user's thesis, forged citations and figures; its fake Query API answers the
model route with the Query API's own `model_maths.run`); `test_model_stats.py` checks which
assumptions each scenario uses and how a failed run is worded. The live suites are [tests/adversarial](../../tests/adversarial) (leaks)
and [tests/citations](../../tests/citations) (citation accuracy); CI replays their recordings.
