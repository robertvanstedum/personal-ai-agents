-- Migration 008: Portuguese Conversas — round management + persona progress

-- Add round_number to existing sessions table
ALTER TABLE portuguese.sessions
  ADD COLUMN IF NOT EXISTS round_number INTEGER DEFAULT 1;

-- Persona progress tracking (one row per user per persona)
CREATE TABLE IF NOT EXISTS portuguese.persona_progress (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER REFERENCES auth.users(id) ON DELETE CASCADE,
    persona_slug     VARCHAR(100) NOT NULL,
    current_round    INTEGER DEFAULT 1,
    sessions_in_round INTEGER DEFAULT 0,
    sessions_total   INTEGER DEFAULT 0,
    last_session_at  TIMESTAMP,
    UNIQUE(user_id, persona_slug)
);

CREATE INDEX IF NOT EXISTS idx_persona_progress_user
  ON portuguese.persona_progress(user_id);
