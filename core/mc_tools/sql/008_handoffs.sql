-- CoS -> MC handoff, MC's receiving side (spec v1.2 §16.1 "Handoff boundary"; A11a; contract
-- _working/CONTRACT_COS_TO_MC_HANDOFF_v0.1_2026-10-22.md). Additive; after 001–007.
-- One row per transfer: persisted before delivery, acknowledged only after the delivered note is read back.
-- A transfer approves nothing; MC's own actions still need their approvals. Rollback: previous code ignores it.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_handoffs (
    transfer_id        uuid PRIMARY KEY,
    digest             text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    principal_id       text NOT NULL,
    environment        text NOT NULL CHECK (environment IN ('dev', 'prod')),
    source             jsonb NOT NULL CHECK (jsonb_typeof(source) = 'object'),
    approval           jsonb NOT NULL CHECK (jsonb_typeof(approval) = 'object'),
    state              text NOT NULL DEFAULT 'received' CHECK (state IN ('received', 'delivered', 'failed')),
    mc_conversation_id text,
    note_ref           text,
    received_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    acknowledged_at    timestamptz,
    CHECK ((state = 'delivered') = (acknowledged_at IS NOT NULL AND note_ref IS NOT NULL))
);
COMMIT;
