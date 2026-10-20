-- Operator rollback: never discard a longer stored reply.
BEGIN;
LOCK TABLE guild.floor_messages IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM guild.floor_messages WHERE length(text) > 4000) THEN
        RAISE EXCEPTION 'longer replies exist; preserve and reconcile them before rollback';
    END IF;
END $$;
ALTER TABLE guild.floor_messages DROP CONSTRAINT IF EXISTS floor_messages_text_check;
ALTER TABLE guild.floor_messages ADD CONSTRAINT floor_messages_text_check
    CHECK (length(text) BETWEEN 1 AND 4000);
COMMIT;
