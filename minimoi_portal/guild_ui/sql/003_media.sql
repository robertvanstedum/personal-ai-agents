-- Guild 1.1 slice 3 (spec §5.2 and the v0.2 addendum): the shared Media
-- library's index. Additive only: a new schema, media, with three tables.
-- The image files themselves live only on the file system (the bind mount at
-- MINIMOI_MEDIA_DIR, default /app/runtime/media), never in the database;
-- emoji are a Unicode value on the asset row, with no file.
--
-- Applied by hand, on staging only, after review and a backup, as the
-- portal's own database role (it owns the schema it creates):
--
--   docker exec -i postgres-ai-agents psql -v ON_ERROR_STOP=1 -U <role> -d personal_agents \
--     < minimoi_portal/guild_ui/sql/003_media.sql
--
-- Backup boundary: back up and restore this schema, the guild schema and the
-- media folder as one pair (database first, then files); the reconcile job
-- reports any mismatch.
--
-- Rollback: leave the schema in place; older code never reads it. Never
-- delete media files or index rows. 003_media_down.sql is for disposable
-- databases only.

CREATE SCHEMA IF NOT EXISTS media;

CREATE TABLE IF NOT EXISTS media.assets (
    id            UUID PRIMARY KEY,
    owner         TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('photo', 'icon', 'emoji')),
    sha256        TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    mime          TEXT,
    bytes         BIGINT CHECK (bytes >= 0),
    width         INTEGER CHECK (width > 0),
    height        INTEGER CHECK (height > 0),
    storage_key   TEXT,
    emoji         TEXT,
    title         TEXT CHECK (length(title) <= 200),
    created_at    TIMESTAMPTZ NOT NULL,
    trashed_at    TIMESTAMPTZ,
    trashed_by    TEXT,
    purged_at     TIMESTAMPTZ,          -- the tombstone: kept for receipts and 410s; never pins the bytes
    version       INTEGER NOT NULL DEFAULT 1,
    access_scope  TEXT NOT NULL DEFAULT 'owner' CHECK (access_scope IN ('owner')),
    CHECK ((kind = 'emoji' AND emoji IS NOT NULL)
           OR (kind <> 'emoji' AND sha256 IS NOT NULL AND storage_key IS NOT NULL))
);
-- Dedup per owner over the sanitised bytes, among assets not purged.
CREATE UNIQUE INDEX IF NOT EXISTS media_assets_owner_sha_live ON media.assets (owner, sha256)
    WHERE purged_at IS NULL;
CREATE INDEX IF NOT EXISTS media_assets_storage_key ON media.assets (storage_key);

-- Where an asset is used. A reference stays live while its placement is
-- recoverable (Board Trash included) and is released only when the placement
-- is purged.
CREATE TABLE IF NOT EXISTS media."references" (
    id            BIGSERIAL PRIMARY KEY,
    asset_id      UUID NOT NULL REFERENCES media.assets (id),
    domain        TEXT NOT NULL,
    ref_kind      TEXT NOT NULL,
    ref_id        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    released_at   TIMESTAMPTZ,
    UNIQUE (asset_id, domain, ref_kind, ref_id)
);
CREATE INDEX IF NOT EXISTS media_references_live ON media."references" (asset_id, released_at);

-- One row per owner: the library Trash revision, incremented by every library
-- trash, restore and purge. Writers lock the row (SELECT ... FOR UPDATE).
CREATE TABLE IF NOT EXISTS media.library_meta (
    owner              TEXT PRIMARY KEY,
    library_trash_rev  BIGINT NOT NULL DEFAULT 0
);
