-- Earlier versions of a thesis (UI audit M5): every save over a thesis (009) keeps the version it
-- replaced, so a long draft overwritten by a short one (7,269 characters down to 1,297, restored
-- by hand in testing) can be read back and restored. The Query API writes the old row here in
-- the same transaction as the update (query_api.workspaces AppWorkspaces.update_item) and keeps
-- the newest 30 per thesis (MAX_VERSIONS); restoring is the web app loading a version into the
-- editor, which the next save versions in turn. Only a change of state is versioned: a rename
-- alone isn't.
--
-- Owner-only like every saved item: the Query API filters every statement on the JWT `sub`, the
-- row-level security below is FORCEd so it binds the table owner too, and (item_id, user_id)
-- references the thesis (010's UNIQUE), so a version can't name another owner and goes with its
-- thesis (ON DELETE CASCADE; cascades bypass RLS, as in 004). Grants nothing: the user's own
-- text. Not streamed by Debezium (the publication is filtered to the tables that grant access).
-- 128 KiB per row, like a thesis.
--
-- Lock impact (hermes): CREATE TABLE with a foreign key takes a SHARE ROW EXCLUSIVE lock on
-- workspace_items for the moment it takes to create the empty table: saves of any item wait for
-- that instant, reads don't. No existing row is read or rewritten.

CREATE TABLE item_versions (
    version_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id     uuid        NOT NULL,
    user_id     text        NOT NULL,
    name        text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    state       jsonb       NOT NULL CHECK (jsonb_typeof(state) = 'object'
                                            AND octet_length(state::text) <= 131072),
    saved_at    timestamptz NOT NULL,  -- when this version was saved (the item's updated_at)
    replaced_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    FOREIGN KEY (item_id, user_id) REFERENCES workspace_items (item_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX item_versions_item ON item_versions (item_id, replaced_at DESC);

ALTER TABLE item_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE item_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON item_versions
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
