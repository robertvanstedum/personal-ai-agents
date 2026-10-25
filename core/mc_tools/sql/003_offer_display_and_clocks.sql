-- MC first light M1.2 (spec v1.2 §9.1–9.3; A02, A02+, A03, A04). Additive; dev first; operator-applied after 001 and 002,
-- at the same deploy that ships the code that writes these columns. Requires core/mc_domain_actions/sql/002.
--
-- An offer is approvable only once its exact platform note is durably present and read back (display_state
-- 'published'). Its two clocks are fixed: the offer lapses at expires_at (created + 10 min; publication never resets
-- it); an accepted approval fixes execution_deadline = approved_at + 5 min, after which nothing new is dispatched.
-- At most one pending offer per principal, environment and conversation.
--
-- Rollback: the code before M1.2 ignores these columns; DROP INDEX mc_tool_proposals_one_pending restores its
-- behaviour (it cancels older pending offers itself). Drained offers stay as history and are never revived.
BEGIN;

ALTER TABLE guild.mc_tool_proposals
    -- 'legacy': written before display was recorded, never approvable (spec A21: old pending offers are not upgraded
    -- into approval). New offers start 'prepared' and become 'published' only after read-back.
    ADD COLUMN IF NOT EXISTS display_state text NOT NULL DEFAULT 'legacy'
        CHECK (display_state IN ('legacy', 'prepared', 'published', 'failed')),
    ADD COLUMN IF NOT EXISTS display_note_ref text,          -- the deterministic platform note request id
    ADD COLUMN IF NOT EXISTS display_digest text CHECK (display_digest ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS published_at timestamptz,       -- the note's own creation time, from read-back
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS execution_deadline timestamptz,
    ADD COLUMN IF NOT EXISTS closed_reason text CHECK (closed_reason ~ '^[a-z0-9_]{1,64}$');

-- Re-runnable: each constraint is added once.
DO $$ BEGIN
    ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_published_has_note
        CHECK (display_state NOT IN ('prepared', 'published', 'failed')
               OR (display_note_ref IS NOT NULL AND display_digest IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_published_at
        CHECK ((display_state = 'published') = (published_at IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_execution_deadline
        CHECK ((approved_at IS NULL AND execution_deadline IS NULL)
               OR execution_deadline = approved_at + interval '5 minutes');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_approved_in_window
        CHECK (approved_at IS NULL OR approved_at < expires_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- One offer may reject several guest requests (spec §9.3): a batch is one offer with per-item operations.
ALTER TABLE guild.mc_tool_proposals DROP CONSTRAINT IF EXISTS mc_tool_proposals_kind_check;
ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_kind_check
    CHECK (kind IN ('lesson', 'guest_reject', 'guest_reject_batch', 'issue_comment', 'data_repair',
                    'delegate_research', 'access_invite'));

-- Activation drain (spec §9.1): pending offers from before this deploy, in the tool and guest registers, are
-- closed as history. The lesson register is a file ledger; the code drains it at its first read (M1.2b).
-- Re-running touches only legacy rows; re-cancelling a guest offer is harmless (nothing executes, history stays),
-- and with the tool path on the guest command path no longer creates offers (M1.2b).
UPDATE guild.mc_tool_proposals SET state = 'cancelled', closed_reason = 'drained_m1_2'
    WHERE state = 'pending' AND display_state = 'legacy';
UPDATE guild.mc_guest_proposals SET state = 'cancelled'
    WHERE state = 'pending';

-- One pending offer per conversation, enforced by the database as well as by the serialized admission.
CREATE UNIQUE INDEX IF NOT EXISTS mc_tool_proposals_one_pending
    ON guild.mc_tool_proposals (principal_id, environment, conversation_id) WHERE state = 'pending';

COMMIT;
