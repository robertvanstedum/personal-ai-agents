-- MC tool path (docs/design/mc_tool_path_design_2026-10-21.md). Additive, operator-applied, dev first.
-- Requires minimoi_portal/guild_ui/sql/007_mc_turn_ledger.sql and core/minimoi_actions/sql/004, 005.
-- Stores ids, digests and content-free outcomes; an invite's name and email are the owner's own request.
BEGIN;

-- The per-turn binding the portal writes at dispatch. It authorizes nothing by itself: a tool call is
-- bound only while its ledger entry is still claimed at this generation with an unexpired lease.
CREATE TABLE IF NOT EXISTS guild.mc_tool_turns (
    ledger_id         bigint NOT NULL REFERENCES guild.mc_turn_ledger (id),
    claim_generation  integer NOT NULL CHECK (claim_generation > 0),
    principal         text NOT NULL,
    conversation_id   text NOT NULL,
    note_request_id   text NOT NULL,
    runtime_user      text NOT NULL CHECK (runtime_user ~ '^guild-mc:[0-9a-f]{32}$'),
    untrusted_read    boolean NOT NULL DEFAULT false,      -- this turn read text anyone can write (GitHub)
    created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (ledger_id, claim_generation)
);
CREATE INDEX IF NOT EXISTS mc_tool_turns_runtime_user ON guild.mc_tool_turns (runtime_user, principal);

-- MC's proposals that wait for the owner's yes in the same conversation: the one register for lessons, guest
-- rejects, issue comments, data-repair tickets, delegation and invites.
CREATE TABLE IF NOT EXISTS guild.mc_tool_proposals (
    proposal_id      uuid PRIMARY KEY,
    principal_id     text NOT NULL,
    environment      text NOT NULL CHECK (environment IN ('dev', 'prod')),
    conversation_id  text NOT NULL,
    owner_turn_id    text NOT NULL,                 -- the owner's kept note whose turn MC proposed in
    proposing_ledger_id bigint NOT NULL,            -- that turn's ledger entry: a plain yes must be the next one
    code             text NOT NULL CHECK (code ~ '^P-[A-Z0-9]{4}$'),   -- what Robert names to approve it
    shown_text       text NOT NULL,                 -- exactly what the platform showed Robert
    untrusted_input  boolean NOT NULL DEFAULT false,-- proposed after reading GitHub: only a named yes approves
    kind             text NOT NULL CHECK (kind IN ('lesson', 'guest_reject', 'issue_comment', 'data_repair',
                                         'delegate_research', 'access_invite')),
    payload          jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    operation_id     uuid NOT NULL UNIQUE,          -- the effect's id, fixed at proposal
    state            text NOT NULL DEFAULT 'pending'
                       CHECK (state IN ('pending', 'confirmed', 'cancelled', 'stale')),
    confirm_turn_id  text,
    created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at       timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS mc_tool_proposals_code
    ON guild.mc_tool_proposals (principal_id, environment, conversation_id, code);
CREATE INDEX IF NOT EXISTS mc_tool_proposals_pending
    ON guild.mc_tool_proposals (principal_id, environment, conversation_id, created_at DESC);

-- Conversations in which MC has read text anyone can write (GitHub). MC's session keeps that text in its context, so
-- from then on every proposal in the conversation needs Robert to name its code (re-review R-1). It never expires.
CREATE TABLE IF NOT EXISTS guild.mc_tool_conversation_taint (
    principal_id     text NOT NULL,
    environment      text NOT NULL CHECK (environment IN ('dev', 'prod')),
    conversation_id  text NOT NULL,
    first_read_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_read_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (principal_id, environment, conversation_id)
);

-- Verified evidence for tool effects; receipts reference evidence_id only.
CREATE TABLE IF NOT EXISTS guild.mc_tool_evidence (
    evidence_id   uuid PRIMARY KEY,
    principal_id  text NOT NULL,
    environment   text NOT NULL CHECK (environment IN ('dev', 'prod')),
    operation_id  uuid NOT NULL,
    kind          text NOT NULL,
    outcome       jsonb NOT NULL CHECK (jsonb_typeof(outcome) = 'object'),
    recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (principal_id, environment, operation_id)
);

COMMIT;
