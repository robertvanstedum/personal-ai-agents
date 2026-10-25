-- MC tool path: bind each turn to the exact runtime instance and agent it was dispatched to (spec v1.2 §7.1 steps 2, 4,
-- 5; §7.4; Codex M1 review P1-2). Additive; dev first; operator-applied after 001–004, with the code that writes it.
--
-- The minimoi-tools plugin announces a random instance id once per gateway process (authenticated with the executor
-- credential) before its first call; the newest announcement is the current instance for the environment. A
-- replacement revokes every turn bound to an earlier instance at once, so a callback still in flight from a replaced
-- process fails even while its owner's lease is valid. The portal records the exact agent it dispatched to and, when
-- it attaches the run, the instance current at that moment. Rollback: previous code ignores the table and columns.
BEGIN;

CREATE TABLE IF NOT EXISTS guild.mc_tool_runtime (
    environment    text PRIMARY KEY CHECK (environment IN ('dev', 'prod')),
    instance_id    text NOT NULL CHECK (instance_id ~ '^[0-9a-f]{32}$'),
    registered_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE guild.mc_tool_turns
    ADD COLUMN IF NOT EXISTS runtime_agent text CHECK (runtime_agent ~ '^[a-z][a-z0-9-]{0,63}$'),
    ADD COLUMN IF NOT EXISTS runtime_instance text CHECK (runtime_instance ~ '^[0-9a-f]{32}$'),
    -- why admission ended early: Robert's Stop (004) or a replaced runtime process (this migration)
    ADD COLUMN IF NOT EXISTS revoked_reason text CHECK (revoked_reason IN ('owner_stop', 'runtime_replaced'));

COMMIT;
