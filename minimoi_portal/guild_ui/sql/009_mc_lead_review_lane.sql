-- Service-origin helper reviews share MC's existing owner-wide lane.
-- No owner note, action grant or tool binding is created by this migration.
BEGIN;
ALTER TABLE guild.mc_turn_ledger DROP CONSTRAINT IF EXISTS mc_turn_ledger_kind_check;
ALTER TABLE guild.mc_turn_ledger ADD CONSTRAINT mc_turn_ledger_kind_check
    CHECK (kind IN ('typed','voice','lead_review'));
COMMIT;
