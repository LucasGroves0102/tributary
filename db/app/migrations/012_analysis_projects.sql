-- Analysis projects (Phase 9 D): a guided research project about one company is an ordinary
-- workspace (004) that also says which company it studies. Its charts, models and thesis stay
-- ordinary workspace items in it (no parallel storage), so the item routes, embeds, thesis
-- checks, caps and cascades all apply unchanged.
--
-- `project` is NULL for every existing workspace (a plain folder; nothing about it changes) and
-- an object for an analysis project: {companyId, peers, notes, done}, validated strictly by the
-- Query API (query_api/workspaces.py ProjectState): the company and up to 20 peers as CIKs, the
-- user's "How it makes money" notes, and the guided steps the user marked done. 16 KiB like
-- other small saved states.
--
-- Owner-only like the rest of the row: the column lives on `workspaces`, whose row-level
-- security (004: ENABLE + FORCE, user_id = app.user_id) already binds every statement, so no
-- new policy is needed and none can be forgotten. Grants nothing: company ids and the user's
-- own text; every page still reads data through the permission-checked Query API as the
-- viewer. Not streamed by Debezium (the publication lists only tables that grant access).

ALTER TABLE workspaces ADD COLUMN project jsonb
    CHECK (project IS NULL OR (jsonb_typeof(project) = 'object'
                               AND octet_length(project::text) <= 16384));
