-- One controlled continuation per claimed owner turn. Additive; Dev first.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_runtime_proof_keys(instance_id text PRIMARY KEY, public_key text NOT NULL);
ALTER TABLE guild.mc_tool_turns ADD COLUMN IF NOT EXISTS depth_nonce text;
ALTER TABLE guild.mc_tool_turns ADD COLUMN IF NOT EXISTS depth_from_run text;
ALTER TABLE guild.mc_tool_turns ADD COLUMN IF NOT EXISTS depth_prepared_at timestamptz;
COMMIT;
