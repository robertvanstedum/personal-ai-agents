-- MC tool admission survives a portal restart only as revoked (Codex review of 29a5ae85, R2; spec v1.2 §7.4).
-- Additive; after 001–010. Each portal process start records a new admission epoch per environment BEFORE its tool
-- listener accepts traffic; every turn recorded under an earlier epoch is revoked then (reason 'portal_restarted'),
-- so a Stop whose durable write failed cannot be forgotten by a fresh process. If the epoch cannot be recorded the
-- listener does not start (tools stay unavailable). Admission also requires the turn's epoch to be the current one.
-- Rollback: previous code ignores the table and column; the revoked_reason check keeps accepting older values.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_tool_portal_epoch (
    environment  text PRIMARY KEY CHECK (environment IN ('dev', 'prod')),
    epoch        uuid NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE guild.mc_tool_turns ADD COLUMN IF NOT EXISTS portal_epoch uuid;
ALTER TABLE guild.mc_tool_turns DROP CONSTRAINT IF EXISTS mc_tool_turns_revoked_reason_check;
ALTER TABLE guild.mc_tool_turns ADD CONSTRAINT mc_tool_turns_revoked_reason_check
    CHECK (revoked_reason IN ('owner_stop', 'runtime_replaced', 'policy_changed', 'portal_restarted'));
COMMIT;
