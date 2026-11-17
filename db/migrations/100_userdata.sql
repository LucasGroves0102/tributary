-- Bring your own data, part 1 (B2c): a user's own time series by company and date, beside gold so
-- formulas, the screener and (later) a SQL workspace can join them to Tributary's figures.
--
-- userdata.datasets: one per uploaded CSV dataset. `name` is how formulas read it
--   (mine.<name>.<field>), `fields` the value columns' names (simplified from the CSV header) in
--   the order `rows.vals` holds them, `units` how each reads in a formula (number, ratio, money,
--   shares, per_share; money and per_share are in `currency`). `known_from`: 'column' when the
--   upload declared a publication-date column, 'upload' when every row is known from the day it
--   was uploaded (point in time, as B1: never a value before it could have been known). `bytes`
--   counts every upload into the dataset (the quota); the row and value counts and the first and
--   last day are the stored rows'.
-- userdata.rows: one per (company, day, known_at): `vals` in `fields` order, NULL where the cell
--   was blank. Another `known_at` for the same company and day is a later version (a revision the
--   user published later); as of a day D the version known by D's end is the one read.
-- userdata.uploads: when a user uploaded (and previewed) and how many bytes: the hourly limits.
--
-- Owner-only, like the portfolio (app 021): the Query API filters every statement on the JWT `sub`
-- and the row-level security below binds even the tables' owner (FORCE) to the session's
-- app.user_id, so no pipeline job reads a user's data and a missed WHERE can't reach another
-- user's. The policies are for query_api (and its members): a session that hasn't named the user
-- in app.user_id sees and writes nothing, the owner's included; a role outside query_api never
-- does. A row's dataset carries the same user id (the composite foreign key), so a row can't hang
-- off another user's dataset. Nothing is streamed (CDC reads only the app
-- database) and nothing grants access to anything: Tributary's own data stays behind OpenFGA and
-- 030's policies whatever a user uploads. Organisation sharing is a later step (an OpenFGA type
-- `upload` and authz.readable_uploads(): services/query-api/DESIGN.md "Your data").
--
-- Unlike gold, none of this can be rebuilt: it is in every backup (pg-backup.sh excludes nothing
-- here). userdata.field_points labels a day with gold.financials' fiscal and calendar rules (014),
-- so a formula aligns a user's dates with Tributary's quarters the same way: change both together.
--
-- Locks on hermes: new schema and tables only; nothing existing is read or locked.

SET LOCAL lock_timeout = '3s';

CREATE SCHEMA userdata;
GRANT USAGE ON SCHEMA userdata TO query_api;

CREATE TABLE userdata.datasets (
    dataset_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       text        NOT NULL CHECK (user_id <> '' AND char_length(user_id) <= 200),
    name          text        NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,39}$'),
    title         text        NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120
                                              AND title !~ '[[:cntrl:]]'),
    fields        text[]      NOT NULL CHECK (
                      cardinality(fields) BETWEEN 1 AND 49
                      AND array_to_string(fields, ',')
                          ~ '^[a-z][a-z0-9_]{0,39}(,[a-z][a-z0-9_]{0,39})*$'),
    units         text[]      NOT NULL CHECK (
                      cardinality(units) = cardinality(fields)
                      AND units <@ ARRAY['number', 'ratio', 'money', 'shares', 'per_share']),
    currency      text        NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
    known_from    text        NOT NULL CHECK (known_from IN ('column', 'upload')),
    row_count     integer     NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    value_count   bigint      NOT NULL DEFAULT 0 CHECK (value_count >= 0),
    company_count integer     NOT NULL DEFAULT 0 CHECK (company_count >= 0),
    bytes         bigint      NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    first_day     date,
    last_day      date,
    created_at    timestamptz NOT NULL DEFAULT statement_timestamp(),
    uploaded_at   timestamptz NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (user_id, name),
    UNIQUE (dataset_id, user_id)
);

CREATE TABLE userdata.rows (
    dataset_id uuid      NOT NULL,
    user_id    text      NOT NULL,
    company_id text      NOT NULL CHECK (company_id ~ '^\d{10}$'),
    day        date      NOT NULL CHECK (day BETWEEN DATE '1900-01-01' AND DATE '2099-12-31'),
    known_at   date      NOT NULL CHECK (known_at >= DATE '1900-01-01'),
    vals       numeric[] NOT NULL CHECK (cardinality(vals) BETWEEN 1 AND 49),
    PRIMARY KEY (dataset_id, company_id, day, known_at),
    FOREIGN KEY (dataset_id, user_id) REFERENCES userdata.datasets (dataset_id, user_id)
        ON DELETE CASCADE
);

CREATE TABLE userdata.uploads (
    user_id     text        NOT NULL CHECK (user_id <> ''),
    uploaded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    stored      boolean     NOT NULL,  -- false: a preview (dry run)
    bytes       bigint      NOT NULL CHECK (bytes >= 0)
);
CREATE INDEX uploads_user ON userdata.uploads (user_id, uploaded_at);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['datasets', 'rows', 'uploads'] LOOP
        EXECUTE format('ALTER TABLE userdata.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE userdata.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY owner ON userdata.%I FOR ALL TO query_api'
                       ' USING (user_id = current_setting(''app.user_id'', true))'
                       ' WITH CHECK (user_id = current_setting(''app.user_id'', true))', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON userdata.%I TO query_api', t);
    END LOOP;
END
$$;

-- One field of one company's rows as the user knew them at the end of p_as_of (NULL: the latest
-- version of every day), each day labelled with gold.financials' fiscal and calendar rules (014:
-- the fiscal year a period ending that day belongs to, its quarter, and the calendar quarter of
-- day - 35 days), so formulas align a user's dates with Tributary's quarters. Blank cells give no
-- row. Security invoker: the caller's row-level security applies.
CREATE FUNCTION userdata.field_points(p_dataset_id uuid, p_company_id text, p_position int,
                                      p_as_of date DEFAULT NULL)
RETURNS TABLE (day date, known_at date, value numeric, fiscal_year int, fiscal_quarter int,
               calendar_year int, calendar_quarter int)
LANGUAGE sql STABLE AS $$
    SELECT v.day, v.known_at, v.vals[p_position],
           extract(year FROM l.fye - 7)::int,
           CASE WHEN l.fye IS NOT NULL
                THEN greatest(1, least(4, 4 - round((l.fye - v.day) / 91.31)::int)) END,
           extract(year FROM l.cs)::int, extract(quarter FROM l.cs)::int
    FROM (SELECT DISTINCT ON (r.day) r.day, r.known_at, r.vals
          FROM userdata.rows r
          WHERE r.dataset_id = p_dataset_id AND r.company_id = p_company_id
            AND (p_as_of IS NULL OR r.known_at <= p_as_of)
          ORDER BY r.day, r.known_at DESC) v
    LEFT JOIN silver.companies c ON c.company_id = p_company_id
    CROSS JOIN LATERAL (SELECT gold.fiscal_year_end(v.day, c.fiscal_year_end) AS fye,
                               gold.calendar_quarter(v.day) AS cs) l
    WHERE v.vals[p_position] IS NOT NULL
$$;
GRANT EXECUTE ON FUNCTION userdata.field_points(uuid, text, int, date) TO query_api;

-- The same data one value a row (dataset, company, day, field, value, known_at): the shape a
-- later SQL workspace joins to gold. Security invoker, so it shows only the caller's rows.
CREATE VIEW userdata.series WITH (security_invoker = true) AS
SELECT r.dataset_id, r.user_id, r.company_id, r.day, f.field, r.vals[f.n] AS value, r.known_at
FROM userdata.rows r
JOIN userdata.datasets d ON d.dataset_id = r.dataset_id AND d.user_id = r.user_id
CROSS JOIN LATERAL unnest(d.fields) WITH ORDINALITY f(field, n)
WHERE r.vals[f.n] IS NOT NULL;
GRANT SELECT ON userdata.series TO query_api;
