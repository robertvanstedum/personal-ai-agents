-- Migration 006 — Portuguese writing sessions
-- Run: docker exec postgres-ai-agents psql -U postgres personal_agents < domains/guild/db/migrations/006_portuguese_writing.sql

CREATE TABLE IF NOT EXISTS portuguese.writing_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id) ON DELETE SET NULL,
    mode            VARCHAR(50),
    prompt          TEXT,
    original_text   TEXT NOT NULL,
    corrected_text  TEXT,
    feedback        JSONB,
    article_id      INTEGER REFERENCES portuguese.articles(id) ON DELETE SET NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pt_writing_user
    ON portuguese.writing_sessions(user_id, created_at DESC);
