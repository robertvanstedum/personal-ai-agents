-- Apply after the existing Guild floor_messages migration. Additive, disabled
-- until the dedicated listener, producer grants and destination are approved.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_standing_events (
    environment text NOT NULL CHECK (environment IN ('dev','prod')),
    producer_id text NOT NULL,
    event_id uuid NOT NULL,
    principal_id text NOT NULL,
    event_digest text NOT NULL CHECK (length(event_digest) = 64),
    payload_digest text NOT NULL CHECK (length(payload_digest) = 64),
    state text NOT NULL CHECK (state IN ('prepared','accepted','deferred')),
    record_id bigint REFERENCES guild.floor_messages(id),
    conflict_count bigint NOT NULL DEFAULT 0,
    last_conflict_digest text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment, producer_id, event_id),
    CHECK ((state = 'accepted') = (record_id IS NOT NULL))
);
COMMIT;
