-- MC tool path, run binding (spec v1.2 §7.1, M1.1 action-admission gate). Additive; dev first; operator-applied
-- after core/mc_tools/sql/001_tool_turns.sql. A tool call is admitted only for the exact OpenClaw run the portal
-- opened for a live, claimed owner turn: the conversation's session stays stable across turns, so the session
-- alone can no longer identify the turn (a delayed call from an earlier run must never bind to a later one).
BEGIN;

ALTER TABLE guild.mc_tool_turns
    ADD COLUMN IF NOT EXISTS runtime_run_id text
        CHECK (runtime_run_id ~ '^chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    ADD COLUMN IF NOT EXISTS run_bound_at timestamptz;

-- One run belongs to one turn, for ever: never reused, never moved.
CREATE UNIQUE INDEX IF NOT EXISTS mc_tool_turns_runtime_run ON guild.mc_tool_turns (runtime_run_id)
    WHERE runtime_run_id IS NOT NULL;

COMMIT;
