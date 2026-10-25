-- MC tool path: the owner's Stop revokes the turn's tool admission at once (spec v1.2 §7.1, §7.4; Codex M1 review P1-1).
-- Additive; dev first; operator-applied after 001–003, with the code that writes it.
--
-- The turn's ledger entry stays claimed until the runtime really ends (the barrier that keeps the next turn waiting),
-- but from the moment Robert presses Stop no new tool call is admitted for it, whether or not the runtime ever
-- acknowledges the abort. Rollback: previous code ignores the column.
BEGIN;

ALTER TABLE guild.mc_tool_turns ADD COLUMN IF NOT EXISTS revoked_at timestamptz;

COMMIT;
