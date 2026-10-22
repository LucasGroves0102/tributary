-- Saved work (Phase 6): workspaces are named folders of saved items: a chart builder state, a
-- comps set or a screener screen. recent_filings remembers the filings a user last opened in
-- the reader ("Continue where you left off").
--
-- Owner-only. Nothing here grants access to data: a saved item holds company ids and settings,
-- and the web app reads the data through the Query API (OpenFGA + RLS) like any other screen,
-- so an item for a company the user lost is shown with the same "no access" state as elsewhere.
-- None of these tables is streamed by Debezium; sharing (an OpenFGA owner/viewer type, CDC and
-- authz-sync) is a later step, see services/query-api/README.md "Workspaces".
--
-- Two layers keep users apart: the Query API filters every statement on the JWT `sub`, and the
-- row-level security below, FORCEd so it binds the table owner too (the Query API's app-DB login
-- owns these tables), admits only rows whose user_id equals the transaction's `app.user_id`.
-- Superusers bypass it (backups run as postgres). Foreign-key cascades bypass it as well, so
-- deleting a user (the demo-cleanup job) still deletes their workspaces.

CREATE TABLE workspaces (
    workspace_id uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      text        NOT NULL REFERENCES users ON DELETE CASCADE,
    name         text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, user_id)
);
CREATE INDEX workspaces_user ON workspaces (user_id, created_at);

-- An item always belongs to its workspace's owner: (workspace_id, user_id) references the
-- workspace, so an item can't be filed into someone else's workspace.
CREATE TABLE workspace_items (
    item_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid        NOT NULL,
    user_id      text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('chart', 'comps', 'screen')),
    name         text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    state        jsonb       NOT NULL CHECK (jsonb_typeof(state) = 'object'
                                             AND octet_length(state::text) <= 16384),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    opened_at    timestamptz,
    FOREIGN KEY (workspace_id, user_id) REFERENCES workspaces (workspace_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX workspace_items_workspace ON workspace_items (workspace_id, created_at);
CREATE INDEX workspace_items_recent ON workspace_items (user_id, opened_at DESC)
    WHERE opened_at IS NOT NULL;

-- The latest filings a user opened in the reader (the Query API keeps the newest 20).
CREATE TABLE recent_filings (
    user_id      text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id   text        NOT NULL CHECK (company_id ~ '^\d{10}$'),
    accession_no text        NOT NULL CHECK (accession_no ~ '^\d{10}-\d{2}-\d{6}$'),
    section_key  text        CHECK (section_key ~ '^[A-Z0-9.?-]{1,8}$'),
    read_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id, accession_no)
);
CREATE INDEX recent_filings_user ON recent_filings (user_id, read_at DESC);

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON workspaces
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE workspace_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_items FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON workspace_items
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE recent_filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE recent_filings FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON recent_filings
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
