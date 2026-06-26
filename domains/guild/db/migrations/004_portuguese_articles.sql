-- Migration 004 — Portuguese articles + leitura notes
-- Run: docker exec postgres-ai-agents psql -U postgres personal_agents < domains/guild/db/migrations/004_portuguese_articles.sql

CREATE TABLE IF NOT EXISTS portuguese.articles (
    id              SERIAL PRIMARY KEY,
    url             VARCHAR(500) UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    excerpt         TEXT,
    full_text       TEXT,
    source          VARCHAR(100),
    category        VARCHAR(50) NOT NULL,
    level           VARCHAR(20) DEFAULT 'intermediario',
    date_fetched    DATE DEFAULT CURRENT_DATE,
    published_at    TIMESTAMP,
    added_at        TIMESTAMP DEFAULT NOW(),
    added_by        INTEGER REFERENCES auth.users(id) ON DELETE SET NULL,
    is_active       BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_pt_articles_category
    ON portuguese.articles(category, is_active, date_fetched DESC);

CREATE TABLE IF NOT EXISTS portuguese.leitura_notes (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id) ON DELETE SET NULL,
    article_id      INTEGER REFERENCES portuguese.articles(id) ON DELETE SET NULL,
    article_title   TEXT,
    original        TEXT NOT NULL,
    corrected       TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pt_leitura_notes_user
    ON portuguese.leitura_notes(user_id, created_at DESC);
