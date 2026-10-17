# propagation

End-to-end tests against the running stack (`make up`, `make cdc`, `make seed-app`,
`make authz`, authz-sync running). Skipped unless `E2E=1`. Run: `make propagation-test`.

`test_revocation.py` measures the BRD success metric: access revoked in the app database →
gone from OpenFGA **and** `authz.user_access` in under 30 seconds. It restores what it
changes.
