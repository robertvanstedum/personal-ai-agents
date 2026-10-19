-- Operator rollback of 007: only an EMPTY ledger may be dropped. The ledger is the durable
-- never-replay evidence (finished, unknown and cancelled ids), so an application rollback turns
-- the feature flag off and keeps these tables; it never deletes turn history.
BEGIN;
LOCK TABLE guild.mc_turn_ledger IN ACCESS EXCLUSIVE MODE;
LOCK TABLE guild.mc_turn_barrier IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM guild.mc_turn_ledger) OR EXISTS (SELECT 1 FROM guild.mc_turn_barrier) THEN
        RAISE EXCEPTION 'the turn ledger holds history; turn the feature off instead of dropping it';
    END IF;
END $$;
DROP TABLE guild.mc_turn_barrier;
DROP TABLE guild.mc_turn_ledger;
COMMIT;
