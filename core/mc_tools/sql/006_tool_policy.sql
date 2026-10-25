-- MC tool policy v0 (spec v1.2 §8.1–8.3, §7.4). Additive; dev first; operator-applied after 001–005, with the code.
--
-- guild.mc_tool_policy: the policy version in force per environment. Activating a different version revokes every
--   live turn's tool admission (revoked_reason 'policy_changed') and closes offers made under another version, so no
--   old-policy effect can run (its grants name the old version and are refused anyway).
-- guild.mc_tool_admissions: one row per admitted tool call (turn, host call id, tool, policy version): the per-turn
--   quota counts these, a retried call id is not counted twice, and it is the admission trace.
-- mc_tool_proposals.policy_version: the policy an offer was made under; approval requires the same one.
-- Rollback: previous code ignores the tables and column; the revoked_reason check accepts the older values.
BEGIN;

CREATE TABLE IF NOT EXISTS guild.mc_tool_policy (
    environment     text PRIMARY KEY CHECK (environment IN ('dev', 'prod')),
    policy_version  text NOT NULL CHECK (policy_version ~ '^mc-policy:[a-z0-9][a-z0-9._-]{0,63}:[0-9a-f]{64}$'),
    activated_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS guild.mc_tool_admissions (
    ledger_id         bigint NOT NULL,
    claim_generation  integer NOT NULL,
    tool_call_id      text NOT NULL CHECK (tool_call_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    tool              text NOT NULL CHECK (tool ~ '^minimoi_[a-z][a-z0-9_]{0,63}$'),
    policy_version    text NOT NULL,
    admitted_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ledger_id, claim_generation, tool_call_id),
    FOREIGN KEY (ledger_id, claim_generation) REFERENCES guild.mc_tool_turns (ledger_id, claim_generation)
);
CREATE INDEX IF NOT EXISTS mc_tool_admissions_turn_tool ON guild.mc_tool_admissions (ledger_id, claim_generation, tool);

ALTER TABLE guild.mc_tool_proposals ADD COLUMN IF NOT EXISTS policy_version text;

ALTER TABLE guild.mc_tool_turns DROP CONSTRAINT IF EXISTS mc_tool_turns_revoked_reason_check;
ALTER TABLE guild.mc_tool_turns ADD CONSTRAINT mc_tool_turns_revoked_reason_check
    CHECK (revoked_reason IN ('owner_stop', 'runtime_replaced', 'policy_changed'));

COMMIT;
