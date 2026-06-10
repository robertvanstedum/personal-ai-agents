-- mini-moi Guild spine — PostgreSQL schema
-- Research objects only. German sessions and curator_signals excluded.
-- JSON is source of truth; this is a rebuildable projection.
-- Tables live in the `research` schema (migrated from public 2026-06-10).
--
-- Bootstrap order for a fresh container:
--   1. init_db.sql          — users + schema grants
--   2. schema.sql           — this file (research tables)
--   3. schema_phase4.sql    — guild.cos_agenda, guild.agent_feedback, jobs.career_opportunities
--   4. schema_phase5.sql    — guild.design_log

CREATE SCHEMA IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS research.tag_aliases (
    alias      TEXT PRIMARY KEY,
    canonical  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research.topics (
    slug            TEXT PRIMARY KEY,
    name            TEXT,
    status          TEXT,         -- dormant / active-pull / paused / one-shot / closed / archived
    queries         JSONB,
    motivation      TEXT,
    tags            TEXT[],
    expires         DATE,
    schema_version  TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research.sources (
    id            TEXT PRIMARY KEY,
    type          TEXT,           -- article / post / paper / book
    title         TEXT,
    url           TEXT,
    origin        TEXT,           -- curator-found / pulled / added-by-robert
    date_recency  TEXT,
    tags          TEXT[],
    note          TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research.groups (
    id                   TEXT PRIMARY KEY,
    name                 TEXT,
    member_tags          TEXT[],
    member_topic_slugs   TEXT[],
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research.leanings (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    state       TEXT,             -- question / leaning / hold
    topics      TEXT[],
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research.evidence (
    id          TEXT PRIMARY KEY,
    leaning_id  TEXT REFERENCES research.leanings(id) ON DELETE CASCADE,
    title       TEXT,
    url         TEXT,
    source      TEXT,
    stance      TEXT,             -- supports / complicates / neutral
    added       DATE,
    note        TEXT
);
