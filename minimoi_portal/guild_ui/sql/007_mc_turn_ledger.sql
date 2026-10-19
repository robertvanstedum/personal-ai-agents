-- Durable Master Craftsman turn ledger (issue #346, P1). Operator-applied; dev first.
-- Records every ledger-enabled turn BEFORE dispatch, with owner-wide exclusion that holds
-- across conversations, restarts and workers. Stores ids, states and content-free outcomes
-- only: never a message, reply, file or topic text.
-- Transactional and repeatable.
BEGIN;

CREATE TABLE IF NOT EXISTS guild.mc_turn_ledger (
    id               bigserial PRIMARY KEY,                    -- owner-wide order
    principal        text NOT NULL,
    conversation_id  text NOT NULL,
    note_request_id  text NOT NULL UNIQUE,                     -- one entry per kept note
    turn_request_id  text NOT NULL UNIQUE,                     -- fixed at entry
    kind             text NOT NULL CHECK (kind IN ('typed', 'voice')),
    attachment_ids   jsonb NOT NULL DEFAULT '[]'::jsonb,       -- doc ids only
    topic_refs       jsonb NOT NULL DEFAULT '[]'::jsonb,       -- {topic_id, item_id, rev} only
    state            text NOT NULL CHECK (state IN
                       ('queued', 'claimed', 'answered', 'failed', 'unknown', 'not_sent', 'cancelled', 'expired')),
    claim_token      text,                                     -- random per claim; binds heartbeat and finish
    claim_generation integer NOT NULL DEFAULT 0,
    claimed_by       text,                                     -- diagnostics only, never authority
    lease_until      timestamptz,
    queued_at        timestamptz,                              -- set only for work that waited in a queue (P2)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    outcome          jsonb,                                     -- content-free: turn id, backend kind, failure class, ids
    CHECK ((state = 'claimed') = (claim_token IS NOT NULL AND lease_until IS NOT NULL))
);

-- At most one running turn per owner, whatever the conversation, worker or code path.
CREATE UNIQUE INDEX IF NOT EXISTS mc_turn_ledger_one_claim_per_owner
    ON guild.mc_turn_ledger (principal) WHERE state = 'claimed';
CREATE INDEX IF NOT EXISTS mc_turn_ledger_owner_order
    ON guild.mc_turn_ledger (principal, state, id);

-- A persisted pause: after an unknown outcome (and after a failed queued turn) nothing more
-- is dispatched for the owner until an authenticated Continue names this entry.
CREATE TABLE IF NOT EXISTS guild.mc_turn_barrier (
    principal   text PRIMARY KEY,
    ledger_id   bigint NOT NULL REFERENCES guild.mc_turn_ledger (id),
    reason      text NOT NULL CHECK (reason IN ('unknown', 'failed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMIT;
