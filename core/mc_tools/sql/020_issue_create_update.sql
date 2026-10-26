-- Robert's own GitHub issue work through an agent, on his exact yes: a new issue (issue_create) or one change to an
-- existing issue (issue_update: title, body, complete label set, open/closed).
BEGIN;
ALTER TABLE guild.mc_tool_proposals DROP CONSTRAINT IF EXISTS mc_tool_proposals_kind_check;
ALTER TABLE guild.mc_tool_proposals ADD CONSTRAINT mc_tool_proposals_kind_check
    CHECK (kind IN ('lesson', 'guest_reject', 'guest_reject_batch', 'issue_comment', 'data_repair',
                    'delegate_research', 'access_invite', 'delegate_build', 'issue_create', 'issue_update'));
COMMIT;
