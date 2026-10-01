-- Guild 1.1 slice 3 (spec §5.1): the Board. Additive only: new columns on
-- guild.floor_postits and one new table, guild.floor_board_meta. Nothing is
-- dropped, renamed or rewritten; no existing row changes.
--
-- Applied by hand, on staging only, after review and a backup; nothing runs
-- this automatically. Idempotent (IF NOT EXISTS, guarded constraints): safe
-- to run twice. Run it as the portal's own database role, like 001:
--
--   docker exec -i postgres-ai-agents psql -v ON_ERROR_STOP=1 -U <role> -d personal_agents \
--     < minimoi_portal/guild_ui/sql/002_board.sql
--
-- Older code keeps working on this schema: it never names the new columns,
-- and their defaults (kind 'note', version 1, everything else NULL) describe
-- an ordinary active note. A NULL sort_key sorts first (newest on top), which
-- is the order the older code shows.
--
-- State precedence (enforced by the application): Trash (binned_at) wins over
-- Done (done_at); otherwise the note is active. There is no purged_at here:
-- Empty trash deletes the binned rows it names, and its receipt is kept in
-- guild.floor_requests.
--
-- Rollback: leave the columns and table in place (the normal, non-destructive
-- rollback). 002_board_down.sql is for disposable databases only.

ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS done_at  TIMESTAMPTZ;
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS done_by  TEXT;
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS label    TEXT
    CHECK (label IN ('decide', 'blocked', 'remember', 'followup', 'idea', 'fyi'));
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS item_ref INTEGER CHECK (item_ref > 0);
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS kind     TEXT NOT NULL DEFAULT 'note'
    CHECK (kind IN ('note', 'photo'));
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS asset_id UUID;
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS sort_key NUMERIC;
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS version  INTEGER NOT NULL DEFAULT 1;

-- A photo needs its asset; a note needs its text (text is already NOT NULL and
-- 1-400 characters; a photo keeps a short caption there, "Photo" by default,
-- so older code always has text to show).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'floor_postits_kind_shape') THEN
        ALTER TABLE guild.floor_postits ADD CONSTRAINT floor_postits_kind_shape CHECK (
            (kind = 'photo' AND asset_id IS NOT NULL) OR (kind = 'note' AND length(text) >= 1));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS floor_postits_floor_order ON guild.floor_postits (floor, sort_key, id);

-- One row per floor. order_rev: every reorder, and every add, restore, done
-- or bin that changes the active order. trash_rev: every bin, restore and
-- purge. Writers lock the row (SELECT ... FOR UPDATE).
CREATE TABLE IF NOT EXISTS guild.floor_board_meta (
    floor       TEXT PRIMARY KEY,
    order_rev   BIGINT NOT NULL DEFAULT 0,
    trash_rev   BIGINT NOT NULL DEFAULT 0
);
