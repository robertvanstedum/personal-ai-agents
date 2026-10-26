-- MC's build proposal (docs/plans/PLAN_mc_chat_yes_build_handoff_2026-10-26.md): Robert's yes starts a Claude Code build
-- job from his own note (scope build: a fresh checkout, edits only, no push or GitHub), like delegated research.
BEGIN;
ALTER TABLE guild.mc_tool_proposals DROP CONSTRAINT IF EXISTS mc_tool_proposals_kind_check;
ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_kind_check
    CHECK (kind IN ('lesson', 'guest_reject', 'guest_reject_batch', 'issue_comment', 'data_repair',
                    'delegate_research', 'access_invite', 'delegate_build'));
COMMIT;
