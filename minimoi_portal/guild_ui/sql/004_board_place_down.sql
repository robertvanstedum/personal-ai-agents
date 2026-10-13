-- DISPOSABLE DATABASES ONLY. Never run this on staging or production: it removes every note's saved position. The normal rollback is to leave
-- 004_board_place.sql in place.
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS pos_y;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS pos_x;
