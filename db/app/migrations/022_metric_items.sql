-- Own metrics (B2d): a saved formula is a workspace item of kind `metric` (a name and the formula,
-- e.g. operating_income / mine.stores.count), usable as a chart layer, a screener column (at most
-- five a screen, evaluated at read time) and on the company page. It holds only the formula text
-- and how it reads; its values are read live through the Query API as the viewer, like every
-- item. At most 20 per user (query_api/workspaces.py MAX_METRICS, counted under the per-user lock
-- every capped save takes). 16 KiB like other items.
--
-- Owner-only like every other item: 004's forced row-level security applies unchanged. A metric
-- grants nothing: a formula naming a company or a dataset reads it only if the viewer could.
--
-- Lock impact (hermes): DROP and ADD CONSTRAINT take an ACCESS EXCLUSIVE lock on workspace_items
-- for the transaction, and the new CHECKs read every row once to validate them (a few hundred
-- rows: milliseconds). Saves and reads of saved work wait for that instant. They only widen what
-- is allowed, so no existing row can fail them. 5 s at most, then the deploy hook fails and is
-- re-run.

SET LOCAL lock_timeout = '5s';

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('chart', 'comps', 'screen', 'report', 'thesis', 'model', 'metric'));

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_state_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_state_check
    CHECK (jsonb_typeof(state) = 'object'
           AND octet_length(state::text)
               <= CASE WHEN kind IN ('report', 'thesis') THEN 131072
                       WHEN kind = 'model' THEN 65536
                       ELSE 16384 END);
