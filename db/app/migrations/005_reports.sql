-- Reports (Phase 6): a workspace item of kind 'report' is a research note the assistant wrote
-- from the workspace's saved items: Markdown, the saved states it embeds (snapshots, so the
-- note shows what it was written about), the filings it cites, its figure verification and
-- as-of times. Owner-only like every other item (004's forced row-level security applies
-- unchanged); it grants nothing: embedded charts read their data through the Query API.
--
-- Only the agent writes report states (the Query API's report routes, kept off the public
-- site by nginx; see services/query-api/README.md "Reports"); the generic item routes refuse
-- them. A report is larger than other items, so its state may be up to 128 KiB; every other
-- kind keeps 004's 16 KiB.

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('chart', 'comps', 'screen', 'report'));

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_state_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_state_check
    CHECK (jsonb_typeof(state) = 'object'
           AND octet_length(state::text) <= CASE WHEN kind = 'report' THEN 131072 ELSE 16384 END);
