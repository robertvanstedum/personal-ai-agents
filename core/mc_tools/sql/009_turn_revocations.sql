-- Stop before the turn's tool binding exists (Codex M1 review P1-1 edge; spec v1.2 §7.4). Additive; after 001–008.
-- The owner's Stop is recorded against the ledger claim itself, so a binding written later (the worker's dispatch
-- racing the Stop) starts revoked, and nothing is admitted for that turn. Rollback: previous code ignores it.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_tool_turn_revocations (
    ledger_id         bigint NOT NULL,
    claim_generation  integer NOT NULL CHECK (claim_generation > 0),
    reason            text NOT NULL CHECK (reason IN ('owner_stop')),
    revoked_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ledger_id, claim_generation)
);
COMMIT;
