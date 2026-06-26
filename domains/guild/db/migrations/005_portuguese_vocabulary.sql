-- Migration 005 — Portuguese translation cache + vocabulary
-- Run: docker exec postgres-ai-agents psql -U postgres personal_agents < domains/guild/db/migrations/005_portuguese_vocabulary.sql

CREATE TABLE IF NOT EXISTS portuguese.translation_cache (
    id          SERIAL PRIMARY KEY,
    portuguese  VARCHAR(500) UNIQUE NOT NULL,
    english     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portuguese.vocabulary (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES auth.users(id) ON DELETE SET NULL,
    portuguese      VARCHAR(500) NOT NULL,
    english         TEXT,
    source          VARCHAR(50),
    source_sentence TEXT,
    status          VARCHAR(20) DEFAULT 'library',
    added_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, portuguese)
);

CREATE INDEX IF NOT EXISTS idx_pt_vocabulary_user
    ON portuguese.vocabulary(user_id, status);
