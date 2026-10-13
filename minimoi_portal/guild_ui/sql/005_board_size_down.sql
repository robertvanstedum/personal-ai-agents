-- DISPOSABLE DATABASES ONLY. Never run this on staging or production.
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS size_h;
ALTER TABLE guild.floor_postits DROP COLUMN IF EXISTS size_w;
