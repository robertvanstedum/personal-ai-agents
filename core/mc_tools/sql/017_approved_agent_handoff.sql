BEGIN;
ALTER TABLE guild.mc_agent_contacts ADD COLUMN IF NOT EXISTS approved_handoff_ref text;
ALTER TABLE guild.mc_agent_contacts ADD COLUMN IF NOT EXISTS authorization_sha256 text;
CREATE UNIQUE INDEX IF NOT EXISTS mc_agent_handoff_original_once ON guild.mc_agent_contacts
 (principal_id,environment,target_alias,approved_handoff_ref) WHERE approved_handoff_ref IS NOT NULL;
ALTER TABLE guild.mc_agent_contacts ADD COLUMN IF NOT EXISTS original_note_request_id text;
CREATE UNIQUE INDEX IF NOT EXISTS mc_agent_handoff_owner_note_once ON guild.mc_agent_contacts
 (principal_id,environment,target_alias,original_note_request_id) WHERE original_note_request_id IS NOT NULL;
COMMIT;
