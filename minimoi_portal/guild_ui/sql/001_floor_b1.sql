-- Shop floor B1 (c): notes, post-its with a kept bin, Continue, and the
-- idempotency keys of their writes.
--
-- Applied by Robert, by hand, on the staging database only; nothing runs this
-- automatically and nothing creates these tables in production during B1.
-- Additive and idempotent (IF NOT EXISTS): safe to run twice.
--
--   docker exec -i postgres-ai-agents psql -v ON_ERROR_STOP=1 -U <role> -d personal_agents \
--     < minimoi_portal/guild_ui/sql/001_floor_b1.sql
--
-- Run it as the portal's own database role, so the portal owns the tables; if
-- it is run as postgres, the guild schema's default privileges
-- (domains/guild/db/init_db.sql) grant them to minimoi.
--
-- Scoping: `floor` names whose floor a row belongs to; reads filter by it,
-- never by the writer. `author` is the writing principal (a username,
-- master_craftsman or guild_platform). Continue is one row per
-- (floor, principal). Nothing is ever deleted by the application: a removed
-- post-it keeps its row, with binned_at set, and restore clears it.
--
-- Rollback (Robert's choice only): 001_floor_b1_down.sql.

CREATE SCHEMA IF NOT EXISTS guild;

CREATE TABLE IF NOT EXISTS guild.floor_messages (
    id              BIGSERIAL PRIMARY KEY,
    floor           TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    author          TEXT NOT NULL,
    author_kind     TEXT NOT NULL CHECK (author_kind IN ('owner', 'agent', 'platform')),
    author_label    TEXT NOT NULL,
    text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
    area            TEXT,
    item_ref        INTEGER,
    page            TEXT,
    record_mode     TEXT NOT NULL CHECK (record_mode = 'on_record'),
    created_at      TIMESTAMPTZ NOT NULL,
    UNIQUE (floor, request_id)
);
CREATE INDEX IF NOT EXISTS floor_messages_floor_id ON guild.floor_messages (floor, id);

CREATE TABLE IF NOT EXISTS guild.floor_postits (
    id              BIGSERIAL PRIMARY KEY,
    floor           TEXT NOT NULL,
    text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 400),
    author          TEXT NOT NULL,
    author_kind     TEXT NOT NULL CHECK (author_kind IN ('owner', 'agent', 'platform')),
    author_label    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    binned_at       TIMESTAMPTZ,
    binned_by       TEXT,             -- who last binned it; kept after a restore
    binned_by_label TEXT,
    restored_at     TIMESTAMPTZ,
    restored_by     TEXT,
    restored_by_label TEXT
);
-- For a database that ran an earlier draft of this file (no-ops otherwise).
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS restored_by TEXT;
ALTER TABLE guild.floor_postits ADD COLUMN IF NOT EXISTS restored_by_label TEXT;
CREATE INDEX IF NOT EXISTS floor_postits_floor_binned ON guild.floor_postits (floor, binned_at, id);

CREATE TABLE IF NOT EXISTS guild.floor_continue (
    floor           TEXT NOT NULL,
    principal       TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('item')),
    ref             TEXT NOT NULL,
    label           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (floor, principal)
);

-- One row per post-it or Continue write's idempotency key (a note's key is its
-- own request_id, unique above), claimed in the same transaction as the
-- change: a retried request is applied once, a replayed old one changes
-- nothing, and one key used for two different changes is refused.
CREATE TABLE IF NOT EXISTS guild.floor_requests (
    floor           TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    principal       TEXT NOT NULL,
    op              TEXT NOT NULL,
    target          TEXT,
    outcome         TEXT,
    result_ref      TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (floor, idempotency_key)
);
