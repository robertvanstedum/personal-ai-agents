-- Board resizing: apply by hand on staging only after review and a backup.
-- Additive, idempotent. NULL retains the original 180x170 geometry.
-- Rollback leaves columns in place; down is for disposable databases only.
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS size_w INTEGER CHECK (size_w BETWEEN 140 AND 480);
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS size_h INTEGER CHECK (size_h BETWEEN 130 AND 450) CHECK ((size_w IS NULL) = (size_h IS NULL));
