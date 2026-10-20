# authz

FR8. OpenFGA authorization model for Tributary, as code.

- `model.fga`: the model. Reading a company's data needs a grant for the company (directly or
  through an organization) **and** a subscription to the tier for that data
  (`can_read_financials`, `can_read_filings` on `company`).
- `model.fga.yaml`: model tests (`make authz-test`; CI runs them with the OpenFGA CLI).

Tiers: `financials` (Free and Pro) gates `gold.quarterly_financials`; `filings` (Pro) gates
`gold.filing_sections`. Watchlists are preferences only and never grant access.

Tuples are written only by [authz-sync](../services/authz-sync), from the app database. The
[Query API](../services/query-api) checks them before every data query.

```bash
make authz        # create the "tributary" store if missing and write model.fga (local OpenFGA)
make authz-test   # run model.fga.yaml
```

On k3s, the platform chart's `setup-openfga` hook writes the model when it changes
([deploy/platform-setup](../deploy/platform-setup)).
