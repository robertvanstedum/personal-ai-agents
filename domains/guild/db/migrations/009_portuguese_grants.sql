-- Migration 009: Grant minimoi user access to portuguese schema
-- Fixes: permission denied for table articles when leitura_rss.py runs on EC2
-- The minimoi app user owns guild.* and jobs.* but portuguese.* was missing grants.

GRANT USAGE ON SCHEMA portuguese TO minimoi;
GRANT ALL ON ALL TABLES IN SCHEMA portuguese TO minimoi;
GRANT ALL ON ALL SEQUENCES IN SCHEMA portuguese TO minimoi;

-- Ensure future tables in the schema are also covered
ALTER DEFAULT PRIVILEGES IN SCHEMA portuguese
  GRANT ALL ON TABLES TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA portuguese
  GRANT ALL ON SEQUENCES TO minimoi;
