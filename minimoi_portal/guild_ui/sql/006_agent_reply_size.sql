-- Preserve bounded model replies without changing the owner composer limit.
-- Operator-applied migration; no automatic production activation.
-- Transactional and repeatable. Existing rows satisfy the wider constraint.
-- A rollback to 4000 must first account for longer agent rows; do not truncate them.
BEGIN;
ALTER TABLE guild.floor_messages DROP CONSTRAINT IF EXISTS floor_messages_text_check;
ALTER TABLE guild.floor_messages ADD CONSTRAINT floor_messages_text_check
    CHECK (length(text) BETWEEN 1 AND CASE WHEN author_kind = 'agent' THEN 16000 ELSE 4000 END);
COMMIT;
