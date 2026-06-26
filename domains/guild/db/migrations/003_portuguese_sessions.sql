-- Migration 003 — Portuguese domain sessions table
-- Run: psql -U postgres -d personal_agents -f 003_portuguese_sessions.sql

CREATE SCHEMA IF NOT EXISTS portuguese;

GRANT USAGE ON SCHEMA portuguese TO minimoi;

CREATE TABLE IF NOT EXISTS portuguese.sessions (
    id                   SERIAL PRIMARY KEY,
    user_id              INTEGER REFERENCES auth.users(id) ON DELETE SET NULL,
    date                 DATE NOT NULL DEFAULT CURRENT_DATE,
    persona              VARCHAR(100),
    scenario             VARCHAR(200),
    source               VARCHAR(50) DEFAULT 'ki_sessao',
    raw_transcript       TEXT,
    reviewer_output      JSONB,
    model                VARCHAR(50),
    duration_estimate_min INTEGER DEFAULT 1,
    created_at           TIMESTAMP DEFAULT NOW()
);

GRANT ALL ON portuguese.sessions TO minimoi;
GRANT USAGE, SELECT ON SEQUENCE portuguese.sessions_id_seq TO minimoi;

CREATE INDEX IF NOT EXISTS idx_pt_sessions_user_id ON portuguese.sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_pt_sessions_date ON portuguese.sessions(date DESC);
