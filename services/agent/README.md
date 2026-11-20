# agent

FR9, week 2. Claude agent that answers questions about SEC filings through the
[Query API](../query-api) only, citing filing, section or XBRL tag, and freshness. The
[web](../web) chat calls it; `agent-ask` runs it from the terminal.

The agent holds the asking user's Query API token and its tools take no user parameter, so it
sees exactly what that user may read. Tool results are treated as data; denied calls come back
as tool errors the model must report. Design notes, measurements and the full behaviour of
reports and thesis checks: [DESIGN.md](DESIGN.md).

| Tool | Query API route |
|---|---|
| `whoami` | `GET /v1/me` |
| `search_companies` | `GET /v1/companies/search` |
| `get_financials` | `GET /v1/companies/{id}/financials` (metric names from `GET /v1/metrics`; at most 40 quarters whatever the model passes) |
| `list_filings` | `GET /v1/companies/{id}/filings` |
| `get_section` | `GET /v1/companies/{id}/sections/{key}` (`max_chars` default 8,000, up to 20,000) |
| `get_section_changes` | `GET /v1/companies/{id}/filings/{accession}/changes` (keeps the first `max_changes`, default 20, max 50; says how many it left out) |
| `get_material_changes` | `GET /v1/companies/{id}/filings/{accession}/changes?view=ranked` (`topic` → `q`, `new_figures` → `numbers=new`; keeps the `max_changes` highest scores across sections, default 15, max 50, says how many it left out, and drops the echoed filters) |
| `search_filings` | `GET /v1/search` (`limit` up to 20, `offset` 0-1,000 as its schema says, clamped in `_route`) |
| `get_earnings_release` | `GET /v1/companies/{id}/earnings` (`quarters` 1-4, default 1; a quarter's quoted adjustments whole up to 6,000 characters, the rest keep heading and citation and say to read them with `get_section`) |
| `get_events_since_report` | `GET /v1/companies/{id}/events` (an event flagged from its 7.01/8.01 announcement carries `announced`) |

The filings tools need the `filings` tier: the adversarial harness counts a result for a company
the user can't read filings for as a leak. Tools added after the recorded suites
(`search_filings`, `get_section_changes`, `get_material_changes`, the earnings and events tools)
needed no re-recording: the suites don't depend on the tool list
([DESIGN.md](DESIGN.md#tools-added-after-the-recordings)).

`get_financials`' metric names come from `GET /v1/metrics` (`MetricTools`, fetched once; until
the Query API answers, the tool lists no names and the API rejects unknown ones). For a company
reporting in another currency it says so per row (`unit`) and on the answer (`currency`).

At the round limit (6 rounds of tool calls in chat, 12 in a report, 16 in a thesis check) one
more call without tools answers from what was found and says first what it couldn't finish; the
answer's `stop` is `max_rounds`. Each model call is logged with its stop reason and token counts.

## Verification

Before an answer is returned, `verify.py` checks every dollar figure and accession number against
the tool results; the result is the answer's `verification`. A figure is verified when it equals
a returned value at the precision written, derived when it is a sum or difference of two
comparable values of one metric (like with like; one company's adjacent quarters or a year apart;
two companies within 45 days), and quoted when it appears in filing text (table cells at the
scale their section states, never looser than millions). After the recorded suites, and only for
a figure those rules leave unverified: a derived metric's `inputs`, and one side of its inputs
summed (total debt; cash plus short-term investments). A "$" figure resting on a row in another
currency is checked again over dollar rows only. `/v1/search`'s echo of the query is never
evidence. Every recorded verdict replays unchanged. The MCP server's `verify_figures` tool
([mcp-server](../mcp-server)) imports this module, so it uses the standard library only. Details
and known limits: [DESIGN.md](DESIGN.md#verification-verifypy).

## agent-api

`POST /v1/ask` with `{"question": ..., "history": [...]}` and the user's bearer token streams
server-sent events: text deltas, tool calls and a final `done` event with the answer, citations
(each filing a tool returned, with the section keys it returned from it, so the web app can open
its reader there), verification, token usage and estimated cost. `GET /health`; `/metrics` for Prometheus
(`tributary_agent_questions_total`, `_tokens_total`, `_cost_usd_total`, `_tool_calls_total`,
`_rounds`).

Every route takes a sign-in's token only: a personal API key (`trb_...`, Query API README "API
keys") is refused with 403 before anything is charged or asked, on `/v1/ask`, reports, thesis
checks and `/v1/allowance` alike, so a script can't spend a user's questions.

The web app also sends `context` (its page, company, filing, search, chart, screener or comps
state; `ui.UiContext`, strict, at most 4,000 bytes). It reaches the model as data marked
untrusted and never decides who the user is. agent-api adds `propose_ui_action` (chart layers and
options, screener filters, comps, watchlist) and `save_to_workspace` (a chart, comps or screen the
screen shows); each proposal is checked as the user through the Query API and streams as an
`action` event for the app to apply with Undo
([DESIGN.md](DESIGN.md#page-context-and-ui-actions)). The web app's `context.page` is one of home,
companies, company, filing, chart, compare, search, ask, about, workspace, report, thesis, model.

`GET /v1/allowance` says what's left before anything is spent, for the assistant panel's meter:
`per_hour`, `network_per_hour`, `left_this_hour`, `per_day` and `left_today` (from the Query API,
null where it doesn't count them), `paused` (the day's budget is gone), `report_questions`,
`check_questions` and `check_seconds` (how long a check usually runs, 120). No model call.

### Limits

Questions per hour per user and per client IP (`X-Client-IP`, from Cloudflare's
`CF-Connecting-IP` via the web proxy), a daily model budget across all users, and the user's
persistent daily allowance from the Query API (`/v1/demo/agent-quota`: a sandbox's or GitHub
account's caps, a password account's own `questions_per_day`). Over a limit → 429. A report
counts as 3 questions ([Reports](#reports)), a thesis check 4 ([Thesis checks](#thesis-checks)),
taken before the model runs. Every model call that returns is added to the day's budget at once.
Questions are given back only for a model API error before any output, a thesis check cut off
at max_tokens that saved nothing, or a report stopped (the stream closed) before it saved anything,
at most twice per user per UTC day (`REFUNDS_PER_DAY`); the stream then carries `{"type":
"refunded", "questions": n}` (not for a stopped report: nobody is listening). Nothing else is given
back. The hour's
limits and refunds are in-process (they reset on restart). Details:
[DESIGN.md](DESIGN.md#allowance-limits-and-refunds).

## Reports

`POST /v1/reports` with `{"workspace_id": ...}` (and `"report_id"` to write an existing report
again) writes a research note from one of the user's workspaces and saves it there as an item of
kind `report` (`report.py`; Query API README, "Reports"). Streams the same events as `/v1/ask`,
plus a `report` event (the saved item and whether every figure verified) or an `error` event if
it couldn't be saved, before `done`.

- The workspace is read as the user (someone else's is a 404, nothing spent). Its first 8
  charts, comps sets and screens are the items (`REPORT_KINDS`); theses, models and earlier
  reports are skipped.
- The tools are the chat tools as the user, limited to the companies the items name.
- Title, summary, one section per item with its `[[embed:item-N]]` marker, then risks and what
  changed when the user can read filings.
- The same `verify.py` check as chat; unverified figures are listed in an "Unverified" section.
- Counts as 3 questions (`REPORT_QUESTIONS`) against every limit; up to 12 tool rounds.

Full behaviour: [DESIGN.md](DESIGN.md#reports).

## Thesis checks

`POST /v1/thesis-checks` with `{"item_id": ...}` (one of the user's theses) checks it against
the data and saves the result with it (`thesis_check.py`; Query API README, "Thesis checks").
Streams the same events as `/v1/ask`, plus a `check` event (the saved check's summary and its
result) or an `error` event if it couldn't be saved or the model never submitted, before `done`.

- **Read as the user.** The thesis (`GET /v1/workspace-items/{id}`) and its workspace: someone
  else's thesis, a missing one or another item is one 404, nothing spent; an empty thesis is a 422.
- **Models run by the Query API.** Each model it embeds (first 4; in an analysis project, the
  project's other models of its company too) is run by `POST /v1/companies/{id}/model` as the
  user, on its base case only; the numbers (CAGR, margins, flags, both bases where the user has
  one-offs, the TTM column, `user_set` and `defaulted` years) go to the model as data. A failed run
  leaves that model's error; the claims are still checked. Its EPS and diluted shares are on today's
  share basis like the Statements tab, so `/per-share` (annual and TTM, diluted, as the user) is read
  too: the evidence those figures verify against, with the share basis and TTM EPS in the data
  (Morgan 19: `/financials` keeps them as filed, Booking's 2024 shares ~34M before its split).
- **Scope.** The thesis's company, its models' companies, an analysis project's company and peers,
  and tickers it names that the user can read; 12 at most. Every read is still the user's own
  Query API call. Plus `submit_check`.
- **Since the last report.** The latest earnings release and the 8-Ks since the period end; a
  balance-sheet figure an event in `stale` follows is out of date: say so and cite it.
- **Verdicts.** supported, contradicted, unverifiable, or `model` ("Your model": every figure in
  the quote is one the model run shows). A supported or contradicted claim needs a citation of a
  filing the tools returned, and a supported one needs every figure verified, or it becomes
  unverifiable. A supported net debt, cash and investments or free cash flow is also compared with
  the cited filing's own figure (`/filings/{accession}/figures`); beyond rounding and 0.5% it is
  unverifiable. Explanations speak to the author ("you", "your thesis").
- **Saved with the thesis** (`POST /v1/thesis-checks/{id}`, off the public site) with
  `text_hash`, the model, rounds, usage and cost, and per company the newest 10-K/10-Q in the
  index when it ran (`filings[]`: the web app offers a re-check when a newer one is filed).
- **Counts as 4 questions** (`CHECK_QUESTIONS`), up to 16 tool rounds (`CHECK_ROUNDS`), about two
  minutes (`CHECK_SECONDS`), up to 32,000 tokens per reply (`CHECK_MAX_TOKENS`) with one retry
  and a closing call that asks it to submit what it has checked.

Full behaviour, rules and the cases behind them: [DESIGN.md](DESIGN.md#thesis-checks).

Result shape (`query_api.thesis_checks.CheckResult`): `thesis` (item, title, company,
`text_hash`, characters checked, truncated), `summary`, `claims[]` (`quote`, `quote_found`,
`status` (supported, contradicted, unverifiable, model), `explanation`, `citations[]`
{accession, form, filed, company, url, section_key, figure, figure_status, as_of, verified},
`figures[]` (status verified, derived, quoted, unverified or model), `downgraded`, `model` (a
`model` claim's), `filing_figures`), `assumptions[]` (per model:
`stats`, `margin_min`/`max`, `years[]` {fy, growth, margin, growth_vs_cagr, growth_flag,
margin_flag, commentary, citations, figures}, or `error`), `not_mentioned[]`, `notes[]`,
`citations[]`, `verification`, `as_of`, `checked_at`, `model`, `questions`, `stop`, `rounds`,
`usage`, `cost_usd`, `filings[]` {company_id, latest, evidence: {accession, form, filed} or
null} (added for Revisit; optional, `version` stays 1, so checks stored before it still read).
The Query API validates `filings` and the `model` status first, so deploy it before (or with)
agent-api.

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
| `AGENT_DAILY_BUDGET_USD` | 2 | `agent-api`, per UTC day: everyone, or demo users only when the next line is set |
| `AGENT_ACCOUNT_DAILY_BUDGET_USD` | unset | `agent-api`: signed-in accounts (`pw-`, `gh-`) get their own budget per UTC day |
| `AGENT_ACCOUNT_MODEL`, `AGENT_ACCOUNT_EFFORT`, `AGENT_ACCOUNT_MAX_TOKENS` | `AGENT_MODEL`, `AGENT_EFFORT`, 8000 | `agent-api`: the model, effort and room to answer for signed-in accounts (hermes: `claude-opus-5-5`, `high`, 16000); demo sandboxes keep `AGENT_MODEL` |
| `PORT` | 8001 | `agent-api` |

## Tests

`pytest services/agent`: the tool loop runs against a scripted fake model and a mocked Query API transport;
no API key needed. `test_thesis_check.py` includes the adversarial cases (injection in the
thesis, another user's thesis, forged citations and figures; its fake Query API answers the
model route with the Query API's own `model_maths.run`); `test_model_stats.py` checks which
assumptions each scenario uses and how a failed run is worded. The live suites are [tests/adversarial](../../tests/adversarial) (leaks)
and [tests/citations](../../tests/citations) (citation accuracy); CI replays their recordings.
