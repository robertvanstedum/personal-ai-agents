-- Explicit operator migration ONLY. No request path executes this file.
-- Add nullable columns, preserve every existing row/state/reservation/hold.
-- Legacy surface=NULL/receipt=NULL remain unknown: never backfill success.
BEGIN;
ALTER TABLE guild.voice_control_session ADD COLUMN IF NOT EXISTS surface TEXT;
ALTER TABLE guild.voice_control_session ADD COLUMN IF NOT EXISTS provider_call_id TEXT;
ALTER TABLE guild.voice_control_session ADD COLUMN IF NOT EXISTS end_receipt TEXT;
COMMIT;
