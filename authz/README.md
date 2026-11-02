# authz

FR8. OpenFGA authorization model for Tributary, as code.

- `model.fga`: the model. Reading a company's data needs access to the company **and** a
  subscription to the tier for that data (`can_read_financials`, `can_read_filings` on
  `company`). Access to a company (`viewer`) comes from either:
  - the **public catalog**: every company with data is in `catalog:sec`
    (`company:<id>#catalog@catalog:sec`), and `catalog:sec#reader@user:*` makes the catalog
    readable by every user. That one tuple is the switch: it exists only while the app
    database's `public_catalogs` row for `sec` is public;
  - a **private grant**: the company granted to the user, or to their organization.
- `model.fga.yaml`: model tests (`make authz-test`; CI runs them with the OpenFGA CLI): closed
  catalog, public catalog (`user:*`), a company outside the catalog, private grants, tiers.

Tiers: `financials` gates gold financials (`gold.quarterly_financials` and friends); `filings`
gates parsed filing text (`gold.filing_sections` and friends). Every plan has both. Watchlists are
preferences only and never grant access.

Tuples are written only by [authz-sync](../services/authz-sync), from the app database (plus the
list of companies with data, from the Tributary database). The [Query API](../services/query-api)
checks them before every data query.

```bash
make authz        # create the "tributary" store if missing and write model.fga (local OpenFGA)
make authz-test   # run model.fga.yaml
```

On k3s, the platform chart's `setup-openfga` hook writes the model when it changes
([deploy/platform-setup](../deploy/platform-setup)). A model that adds relations must be written
before authz-sync writes tuples that use them (OpenFGA rejects unknown relations), so deploy the
platform release first.
