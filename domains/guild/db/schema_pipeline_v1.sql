-- pipeline schema v1 — rename jobs.career_opportunities → pipeline.items
-- Adds: context, close_reason, priority, closed_at columns
-- Apply: psql "postgresql://postgres:simple123@localhost:5432/personal_agents" -f domains/guild/db/schema_pipeline_v1.sql

-- 1. Create pipeline schema
CREATE SCHEMA IF NOT EXISTS pipeline;

-- 2. Add new columns to existing table before moving it
ALTER TABLE jobs.career_opportunities
    ADD COLUMN IF NOT EXISTS context      TEXT        DEFAULT 'career',
    ADD COLUMN IF NOT EXISTS close_reason TEXT,
    ADD COLUMN IF NOT EXISTS priority     BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS closed_at    TIMESTAMPTZ;

-- 3. Backfill context for all existing records
UPDATE jobs.career_opportunities SET context = 'career' WHERE context IS NULL;

-- 4. Move table into pipeline schema
ALTER TABLE jobs.career_opportunities SET SCHEMA pipeline;

-- 5. Rename to items
ALTER TABLE pipeline.career_opportunities RENAME TO items;

-- 6. Grant permissions
GRANT USAGE ON SCHEMA pipeline TO minimoi, robert_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pipeline TO minimoi;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline TO robert_ro;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA pipeline TO minimoi;
