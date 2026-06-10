-- migrate_public_to_research.sql
-- Moves research/intelligence tables from public schema to research schema.
-- All 6 tables are empty — no data movement risk.
-- ALTER TABLE ... SET SCHEMA is in-place; no data is copied or deleted.
--
-- Apply:
--   docker exec -i postgres-ai-agents psql -U postgres -d personal_agents \
--     < domains/guild/db/migrate_public_to_research.sql
--
-- Verify:
--   docker exec postgres-ai-agents psql -U minimoi -d personal_agents -c "\dt research.*"

-- 1. Create schema and grant access
CREATE SCHEMA IF NOT EXISTS research;

GRANT ALL ON SCHEMA research TO minimoi;
GRANT USAGE ON SCHEMA research TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA research GRANT ALL ON TABLES    TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA research GRANT ALL ON SEQUENCES TO minimoi;
ALTER DEFAULT PRIVILEGES IN SCHEMA research GRANT SELECT ON TABLES TO robert_ro;

-- 2. Move tables (in-place — no data copied)
ALTER TABLE public.sources     SET SCHEMA research;
ALTER TABLE public.topics      SET SCHEMA research;
ALTER TABLE public.leanings    SET SCHEMA research;
ALTER TABLE public.evidence    SET SCHEMA research;
ALTER TABLE public.groups      SET SCHEMA research;
ALTER TABLE public.tag_aliases SET SCHEMA research;

-- 3. Confirm
SELECT schemaname, tablename
FROM pg_tables
WHERE schemaname = 'research'
ORDER BY tablename;
