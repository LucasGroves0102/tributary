# status

The recorded-suite part of the public status page (web `#/status`, Query API
`GET /v1/status/data`, field `recorded`). The live parts come from the database; these come from
the repository, because the adversarial and citation suites call the model and aren't re-run
live.

| File | Role |
|---|---|
| `recorded_results.py` | writes `services/query-api/src/query_api/data/recorded_results.json` from the committed [adversarial](../adversarial) and [citation](../citations) recordings (counts per run, their report's run date, the models in the recordings) and the [golden 10-K figures](../golden) CSV, each with the commit that last changed its inputs |
| `test_recorded_results.py` | CI: the committed JSON matches what the recordings say now (commits aside), and holds no company ids or accession numbers |

```bash
make status-results   # after committing new recordings or golden figures; commit the JSON
```

It refuses while the inputs have uncommitted changes, so the commit it records is the one that
holds them. Nothing here calls the API or re-runs a suite.
