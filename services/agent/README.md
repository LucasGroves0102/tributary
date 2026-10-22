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
| `search_filings` | `GET /v1/search` |

`get_financials`' metric names come from `GET /v1/metrics` (`MetricTools`, fetched once; until
the Query API answers, the tool lists no names and the API rejects unknown ones).

Before an answer is returned, `verify.py` checks every dollar figure against the tool results
(matched, derived as a sum or difference, or quoted from filing text) and every accession number
against what the tools returned; the result is part of the answer as `verification`.

## agent-api

`POST /v1/ask` with `{"question": ..., "history": [...]}` and the user's bearer token streams
server-sent events: text deltas, tool calls and a final `done` event with the answer, citations
(each filing a tool returned, with the section keys it returned from it, so the web app can open
its reader there), verification, token usage and estimated cost. `GET /health`; `/metrics` for Prometheus
(`tributary_agent_questions_total`, `_tokens_total`, `_cost_usd_total`, `_tool_calls_total`,
`_rounds`).

The web app also sends `context` (its page, company, the reader's filing and section, chart
layers, screener filters;
`ui.UiContext`: strict fields, at most 4 KB). It reaches the model as a block of data marked
untrusted and never decides who the user is: that is still only the token. agent-api adds a
`propose_ui_action` tool (set/add/remove chart layers, chart options, screener filters, add or
remove a watchlist company). Each proposal is checked as the user through the Query API
(`/v1/me`, `/v1/companies/search`, `/v1/formulas/validate`): chart companies must be readable,
unknown and ungranted ones get the same refusal. Accepted ones stream as `action` events (also in
`answer.actions`) for the app to apply with Undo; the app reads any data itself, through the
Query API. `save_to_workspace` (item `chart`, `comps` or `screen`, a name, a workspace name)
saves what the screen shows: it is accepted only if the context showed that item or an accepted
action in the same answer built it, and names are trimmed, capped and free of control
characters. The app then saves it through the Query API (which validates the state again, for
this user only) and Undo deletes the saved item. Without the web app (CLI, recorded suites)
requests are unchanged.

Limits: questions per hour per user and per client IP (`X-Client-IP`, set by the web proxy from
Cloudflare's `CF-Connecting-IP`), and a daily model budget across all users. Over a limit → 429.

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
| `AGENT_IP_RATE_PER_HOUR` | 10 | `agent-api`, per client IP |
| `AGENT_DAILY_BUDGET_USD` | 2 | `agent-api`, all users, per UTC day |
| `PORT` | 8001 | `agent-api` |

## Tests

`pytest services/agent`: the tool loop runs against a scripted fake model and a mocked Query API transport;
no API key needed. The live suites are [tests/adversarial](../../tests/adversarial) (leaks)
and [tests/citations](../../tests/citations) (citation accuracy); CI replays their recordings.
