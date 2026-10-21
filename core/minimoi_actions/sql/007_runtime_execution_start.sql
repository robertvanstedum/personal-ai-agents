-- Additive, operator-applied. Existing unstarted bindings fail closed.
-- Never clear started_at to retry an uncertain inference or restore an old boot.
BEGIN;
ALTER TABLE guild.mc_runtime_bindings
    ADD COLUMN IF NOT EXISTS started_at timestamptz;
COMMIT;
