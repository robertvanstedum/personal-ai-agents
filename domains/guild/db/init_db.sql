-- init_db.sql — Bootstrap personal_agents database
-- Run as superuser (postgres) on a fresh container.
-- Safe to re-run: all CREATE statements use IF NOT EXISTS / DO $$ idioms.
-- Usage: docker exec -i postgres-ai-agents psql -U postgres < domains/guild/db/init_db.sql

-- ── Schemas ───────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS guild;
CREATE SCHEMA IF NOT EXISTS jobs;

-- ── App user ──────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'minimoi') THEN
        CREATE USER minimoi WITH PASSWORD 'simple123';
    END IF;
END$$;

GRANT ALL PRIVILEGES ON DATABASE personal_agents TO minimoi;
GRANT ALL ON SCHEMA guild TO minimoi;
GRANT ALL ON SCHEMA jobs  TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA guild GRANT ALL ON TABLES    TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA guild GRANT ALL ON SEQUENCES TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA jobs  GRANT ALL ON TABLES    TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA jobs  GRANT ALL ON SEQUENCES TO minimoi;

-- ── Read-only user ────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'robert_ro') THEN
        CREATE USER robert_ro WITH PASSWORD 'simple123';
    END IF;
END$$;

GRANT USAGE ON SCHEMA guild TO robert_ro;
GRANT USAGE ON SCHEMA jobs  TO robert_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA guild TO robert_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA jobs  TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA guild GRANT SELECT ON TABLES TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA jobs  GRANT SELECT ON TABLES TO robert_ro;

\echo 'init_db.sql complete — users and schemas created'
