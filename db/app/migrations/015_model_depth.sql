-- Model depth (F2): a model item (009) now carries, per forecast year and case, four more rates
-- (interest and other, tax rate, share change, FCF margin) beside growth, margin and a note, plus
-- up to 20 named adjustments and the user's own price. Ten years of three cases with full notes
-- no longer fit the 16 KiB every other item gets, so a model may hold 64 KiB; reports and theses
-- keep 128 KiB, everything else 16 KiB. The Query API caps a model at 60,000 bytes of compact
-- JSON (workspaces.MAX_MODEL_BYTES), under this limit on jsonb's wider text.
--
-- Models are now versioned like theses: every save over a model keeps the state it replaced in
-- item_versions (014), which needs no change here (it was never limited to a kind and allows
-- 128 KiB). Nothing about who reads what changes: 004's forced row-level security covers both
-- tables, and a model still holds only a company id and numbers the user typed.
--
-- Lock impact (hermes): DROP and ADD CONSTRAINT take an ACCESS EXCLUSIVE lock on workspace_items
-- for the transaction, and the new CHECK reads every row once to validate it (14 rows, 232 kB on
-- 2026-11-09: milliseconds). Saves and reads of saved work wait for that instant. It only widens
-- the limit, so no existing row can fail it. The lock waits for open transactions on the table,
-- and everything queues behind it while it waits: 5 s at most, then the deploy hook fails and is
-- re-run.

SET LOCAL lock_timeout = '5s';

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_state_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_state_check
    CHECK (jsonb_typeof(state) = 'object'
           AND octet_length(state::text)
               <= CASE WHEN kind IN ('report', 'thesis') THEN 131072
                       WHEN kind = 'model' THEN 65536
                       ELSE 16384 END);
