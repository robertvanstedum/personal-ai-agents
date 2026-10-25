-- Conversational action identity and clarification are evidence, never execution grants.
CREATE TABLE IF NOT EXISTS guild.mc_board_slots (
 principal TEXT NOT NULL, environment TEXT NOT NULL, conversation_id TEXT NOT NULL,
 note_request_id TEXT NOT NULL, operation_id UUID NOT NULL UNIQUE,
 ledger_id BIGINT NOT NULL, claim_generation BIGINT NOT NULL,
 capability TEXT, payload_json TEXT, payload_sha256 TEXT,
 state TEXT NOT NULL CHECK(state IN ('unresolved','ready','dispatched','verified','not_applied','unknown','cancelled','expired')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), expires_at TIMESTAMPTZ,
 question TEXT, candidate_json TEXT, resolution_reason TEXT, parent_operation UUID,
 PRIMARY KEY(principal,environment,conversation_id,note_request_id)
);
CREATE INDEX IF NOT EXISTS mc_board_slots_conversation ON guild.mc_board_slots (principal,environment,conversation_id,created_at);
CREATE TABLE IF NOT EXISTS guild.mc_board_observations (
 principal TEXT NOT NULL, environment TEXT NOT NULL, conversation_id TEXT NOT NULL,
 note_request_id TEXT NOT NULL, note_ref TEXT NOT NULL, revision BIGINT NOT NULL,
 snapshot_json TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(principal,environment,conversation_id,note_request_id,note_ref)
);
