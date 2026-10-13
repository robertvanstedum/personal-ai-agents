-- The Board, free placement (11 Oct 2026, docs: _working/guild-beta/BOARD_SIMPLE_NOTEPAD_PROPOSAL_2026-10-11.md). Additive only: two nullable
-- columns on guild.floor_postits holding where a note sits on the board, in logical units (the board is 1200 wide and grows downward). Nothing
-- is dropped, renamed or rewritten; no existing row changes (NULL = "not placed yet", which the page lays out by its list order).
--
-- Applied by hand, on staging only, after review and a backup; nothing runs this automatically. Idempotent (IF NOT EXISTS): safe to run twice.
-- Run it as the portal's own database role, like 002:
--
--   docker exec -i postgres-ai-agents psql -v ON_ERROR_STOP=1 -U <role> -d personal_agents \
--     < minimoi_portal/guild_ui/sql/004_board_place.sql
--
-- Older code keeps working on this schema: it never names the new columns. Rollback: leave the columns in place (the normal, non-destructive
-- rollback). 004_board_place_down.sql is for disposable databases only.

ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS pos_x INTEGER CHECK (pos_x >= 0 AND pos_x <= 1200);
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS pos_y INTEGER CHECK (pos_y >= 0 AND pos_y <= 20000);
