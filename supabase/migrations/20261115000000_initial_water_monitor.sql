-- Apply explicitly with a privileged migration connection, never at web startup.
-- This schema is private to the server database role; it is not a public API.
CREATE SCHEMA IF NOT EXISTS water_monitor_private;
REVOKE ALL ON SCHEMA water_monitor_private FROM PUBLIC;

CREATE TABLE IF NOT EXISTS water_monitor_private.users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'analyst')),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS water_monitor_private.history_runs (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES water_monitor_private.users(id),
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    status TEXT NOT NULL CHECK (status IN ('created','running','complete','incomplete','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    state JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
    expert JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(expert) = 'object')
);
CREATE INDEX IF NOT EXISTS history_runs_owner_created
    ON water_monitor_private.history_runs(owner_id, created_at DESC);

-- A completed month holds scalar statistics only; no image/raster columns.
CREATE TABLE IF NOT EXISTS water_monitor_private.monthly_results (
    run_id UUID NOT NULL REFERENCES water_monitor_private.history_runs(id),
    parameter TEXT NOT NULL,
    month DATE NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    status TEXT NOT NULL CHECK (status IN ('complete','no_data','failed','skipped')),
    mean_value DOUBLE PRECISION,
    water_coverage DOUBLE PRECISION CHECK (water_coverage BETWEEN 0 AND 100),
    valid_pixels BIGINT CHECK (valid_pixels >= 0),
    message TEXT,
    stage TEXT,
    CHECK (status <> 'complete' OR (water_coverage IS NOT NULL AND valid_pixels IS NOT NULL)),
    CHECK (mean_value IS NULL OR mean_value NOT IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)),
    PRIMARY KEY (run_id, parameter, month)
);

CREATE TABLE IF NOT EXISTS water_monitor_private.expert_revisions (
    run_id UUID NOT NULL REFERENCES water_monitor_private.history_runs(id),
    revision BIGINT GENERATED ALWAYS AS IDENTITY,
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, revision)
);

-- The scene count is shared by all three indices for a month.
CREATE TABLE IF NOT EXISTS water_monitor_private.shared_months (
    run_id UUID NOT NULL REFERENCES water_monitor_private.history_runs(id),
    month DATE NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    scene_count INTEGER NOT NULL CHECK (scene_count >= 0),
    PRIMARY KEY (run_id, month)
);
REVOKE ALL ON ALL TABLES IN SCHEMA water_monitor_private FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA water_monitor_private FROM PUBLIC;
