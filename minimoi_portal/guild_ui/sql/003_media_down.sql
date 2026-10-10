-- DISPOSABLE DATABASES ONLY. Never run this on staging or production: it
-- removes the Media library's whole index (the files on disk would be left
-- without one). The normal rollback is to leave 003_media.sql in place
-- (spec §9). On a real database this needs a reviewed plan: export first,
-- then down, then a restore path.

DROP TABLE IF EXISTS media.library_meta;
DROP TABLE IF EXISTS media."references";
DROP TABLE IF EXISTS media.assets;
DROP SCHEMA IF EXISTS media;
