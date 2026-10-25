-- Runtime instances are never reused (Codex P1-2 checklist items 3–4; spec v1.2 §7.1 step 5). Additive; after 001–009.
-- Every announced gateway-process id is kept; once replaced it is retired for good, so a late or replayed
-- announcement from an old process can never make it current again (no last-arrival-wins). The binding takes the
-- instance current at dispatch (record), not at its first tool call. Rollback: previous code ignores the table.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_tool_runtime_history (
    instance_id   text PRIMARY KEY CHECK (instance_id ~ '^[0-9a-f]{32}$'),
    environment   text NOT NULL CHECK (environment IN ('dev', 'prod')),
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retired_at    timestamptz
);
COMMIT;
