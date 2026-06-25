-- Migration 002: auth schema + guest_requests domain column
-- Apply on EC2:
--   docker exec postgres-ai-agents psql -U minimoi personal_agents \
--     < domains/guild/db/migrations/002_auth_schema.sql
-- Or locally:
--   psql postgresql://minimoi:simple123@localhost:5432/personal_agents \
--     < domains/guild/db/migrations/002_auth_schema.sql

-- 1. Extend guild.guest_requests
ALTER TABLE guild.guest_requests
  ADD COLUMN IF NOT EXISTS domain VARCHAR(50) DEFAULT 'portuguese';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'guest_requests_status_check'
      AND conrelid = 'guild.guest_requests'::regclass
  ) THEN
    ALTER TABLE guild.guest_requests
      ADD CONSTRAINT guest_requests_status_check
      CHECK (status IN (
        'requested', 'pending', 'granted', 'rejected', 'approved_pending_email'
      ));
  END IF;
END $$;

-- 2. auth schema and tables
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(50) DEFAULT 'user',
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS auth.domain_access (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    domain      VARCHAR(50) NOT NULL,
    granted_at  TIMESTAMP DEFAULT NOW(),
    granted_by  INTEGER REFERENCES auth.users(id),
    UNIQUE(user_id, domain)
);

CREATE TABLE IF NOT EXISTS auth.login_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES auth.users(id),
    token       VARCHAR(255) UNIQUE NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,
    used        BOOLEAN DEFAULT FALSE
);

GRANT USAGE ON SCHEMA auth TO minimoi;
GRANT ALL ON ALL TABLES IN SCHEMA auth TO minimoi;
GRANT ALL ON ALL SEQUENCES IN SCHEMA auth TO minimoi;
