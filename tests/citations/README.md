# citations

Citation-accuracy eval for the agent. 32 questions across the demo users; each answer goes
through the agent's verifier (`services/agent/src/tributary_agent/verify.py`), which traces
every dollar figure to a returned value, a sum or difference of returned values, or returned
filing text, and every accession number to a tool result. Latest result:
[report.md](report.md) (32 of 32 answers fully verified, 128 of 128 figures traced).

| File | Role |
|---|---|
| `citation_cases.py` | `CASES` (id, user, question) |
| `run.py` | live run: `make citations [CASES="fin-01 ..."]` |
| `test_citations.py` | CI: replays `recordings/` and re-verifies every answer |
| `recordings/` | one JSON per question |

Uses the recording and replay helpers in [`../adversarial/adversarial_harness.py`](../adversarial).

## Run

```bash
make citations          # all questions; rewrites recordings and report.md
pytest tests/citations  # replay only, no network
```

The live run needs the Query API on `QUERY_API_URL`, `QUERY_API_JWT_SECRET`,
`ANTHROPIC_API_KEY` and `AGENT_MODEL` from `.env`.
