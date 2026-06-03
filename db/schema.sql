-- mini-moi Guild spine — PostgreSQL schema
-- Research objects only. German sessions and curator_signals excluded.
-- JSON is source of truth; this is a rebuildable projection.
-- Run: psql postgresql://minimoi:simple123@localhost:5432/personal_agents -f db/schema.sql

CREATE TABLE IF NOT EXISTS tag_aliases (
    alias      TEXT PRIMARY KEY,
    canonical  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
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

CREATE TABLE IF NOT EXISTS sources (
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

CREATE TABLE IF NOT EXISTS groups (
    id                   TEXT PRIMARY KEY,
    name                 TEXT,
    member_tags          TEXT[],
    member_topic_slugs   TEXT[],
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leanings (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    state       TEXT,             -- question / leaning / hold
    topics      TEXT[],
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    leaning_id  TEXT REFERENCES leanings(id) ON DELETE CASCADE,
    title       TEXT,
    url         TEXT,
    source      TEXT,
    stance      TEXT,             -- supports / complicates / neutral
    added       DATE,
    note        TEXT
);
