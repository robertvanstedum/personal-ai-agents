-- Board partnership v0.2 B1. Additive; apply only in reviewed/authorized rollout.
-- No routes, tools, keys or policy are enabled by this schema.
-- Rollback preserves these columns and receipts; never drop live evidence.
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS semantic_revision BIGINT NOT NULL DEFAULT 1
    CHECK (semantic_revision >= 1);

CREATE TABLE IF NOT EXISTS guild.board_operations (
    principal TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('dev', 'prod')),
    floor TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    executor TEXT NOT NULL,
    capability TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    payload_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    target_ref BIGINT,
    undo_of TEXT,
    committed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (principal, environment, floor, operation_id)
);
CREATE INDEX IF NOT EXISTS board_operations_target ON guild.board_operations (principal, environment, floor, target_ref);
