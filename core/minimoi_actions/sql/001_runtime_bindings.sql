-- MC harness: operator-applied, additive migration; no tool is activated here.
-- Keep terminal bindings as replay tombstones. No automatic expiry deletion.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_runtime_sessions (
    environment text NOT NULL CHECK (environment IN ('dev', 'prod')),
    runtime_id text NOT NULL,
    session_key text NOT NULL,
    principal_id text NOT NULL,
    actor_id text NOT NULL,
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    dispatch_id text,
    PRIMARY KEY (environment, runtime_id, session_key)
);

CREATE TABLE IF NOT EXISTS guild.mc_runtime_bindings (
    dispatch_id text PRIMARY KEY,
    reference_hash text NOT NULL UNIQUE CHECK (length(reference_hash) = 64),
    environment text NOT NULL CHECK (environment IN ('dev', 'prod')),
    runtime_id text NOT NULL,
    instance_id text NOT NULL,
    session_key text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    context jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object'),
    run_id text,
    state text NOT NULL CHECK (state IN ('awaiting_run', 'bound', 'revoked', 'closed')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (environment, runtime_id, session_key)
        REFERENCES guild.mc_runtime_sessions (environment, runtime_id, session_key),
    UNIQUE (environment, runtime_id, session_key, generation),
    UNIQUE (environment, runtime_id, run_id),
    CHECK ((state <> 'bound' OR run_id IS NOT NULL)
       AND (state <> 'awaiting_run' OR run_id IS NULL))
);
CREATE INDEX IF NOT EXISTS mc_runtime_bindings_instance
    ON guild.mc_runtime_bindings (environment, runtime_id, instance_id);
COMMIT;
