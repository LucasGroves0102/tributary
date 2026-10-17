# authz

FR8. OpenFGA authorization model for Tributary, as code.

- `model.fga`: the model. Reading a company's data needs a grant for the company (directly or
  through an organization) **and** a subscription to the tier for that data.
- `model.fga.yaml`: model tests (`make authz-test`).

Tiers: `financials` (Free and Pro) gates `gold.quarterly_financials`; `filings` (Pro) gates
`gold.filing_sections`. Watchlists are preferences only and never grant access.
