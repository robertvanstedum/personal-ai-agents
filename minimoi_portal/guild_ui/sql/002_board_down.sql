-- DISPOSABLE DATABASES ONLY. Never run this on staging or production: it
-- removes Done, labels, links, photos, order and versions from every post-it.
-- The normal rollback is to leave 002_board.sql in place (spec §9). On a real
-- database this needs a reviewed plan: export first, then down, then a
-- restore path.

DROP TABLE IF EXISTS guild.floor_board_meta;
DROP INDEX IF EXISTS guild.floor_postits_floor_order;
ALTER TABLE guild.floor_postits DROP CONSTRAINT IF EXISTS floor_postits_kind_shape;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS version;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS sort_key;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS asset_id;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS kind;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS item_ref;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS label;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS done_by;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS done_at;
