-- Theses and models (Phase 9 B): two more workspace item kinds, both written by the user
-- through the generic item routes (unlike reports, which only the agent writes).
--
-- thesis: a research note the user writes in Markdown about a company (optional), with
--   `[[embed:<item id>]]` lines that draw a chart, comps set, screen or model saved in the same
--   workspace. Up to 128 KiB like a report.
-- model:  a revenue-first forecast: a company, a history window and per-year assumptions
--   (growth, operating margin, a note). It stores only the assumptions; the history is read
--   live through the Query API as the viewer. 16 KiB like other items.
--
-- Owner-only like every other item: 004's forced row-level security applies unchanged. Neither
-- grants anything: a model holds a company id and numbers the user typed, a thesis the user's
-- own text; embedded items read their data through the Query API as the viewer.

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('chart', 'comps', 'screen', 'report', 'thesis', 'model'));

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_state_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_state_check
    CHECK (jsonb_typeof(state) = 'object'
           AND octet_length(state::text)
               <= CASE WHEN kind IN ('report', 'thesis') THEN 131072 ELSE 16384 END);
