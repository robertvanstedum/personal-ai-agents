# Database Schema — mini-moi
**Last updated:** 2026-06-10
**Database:** PostgreSQL (`personal_agents`) via Docker/Colima

---

## Schema naming convention

Every table lives in a named schema. `public` is always empty.

| Schema | Owner | Purpose |
|---|---|---|
| `research` | minimoi | Research intelligence objects — topics, sources, leanings, evidence |
| `guild` | minimoi | Guild agent state — CoS agenda, agent feedback, design log |
| `jobs` | minimoi | Career search results — opportunities found by Loop A |
| `german` | *(future)* | German domain session data, when promoted from JSON |
| `curator` | *(future)* | Curator signal/source data, when promoted from JSON |

**Rule: when a new domain needs a DB table, create a new schema for it.** Never add to `public`. Never add cross-domain tables to another domain's schema.

---

## Current tables

### research.*
Research/intelligence layer. Source of truth is JSON flat files; this is a rebuildable projection.

| Table | Key columns | Notes |
|---|---|---|
| `research.topics` | `slug` PK, `status`, `queries` JSONB, `tags` TEXT[] | Active research threads |
| `research.sources` | `id` PK, `type`, `url`, `tags` TEXT[] | Articles, posts, papers |
| `research.leanings` | `id` PK, `title`, `state` | question / leaning / hold |
| `research.evidence` | `id` PK, `leaning_id` FK → leanings | Supports / complicates / neutral |
| `research.groups` | `id` PK, `member_tags`, `member_topic_slugs` | Tag groupings |
| `research.tag_aliases` | `alias` PK, `canonical` | Synonym mapping |

### guild.*
Guild agent operational data.

| Table | Key columns | Notes |
|---|---|---|
| `guild.cos_agenda` | `id` SERIAL, `domain`, `description`, `status` | CoS recommendations queue |
| `guild.agent_feedback` | `id` SERIAL, `agent_name`, `signal_type` | Future calibration signals |
| `guild.design_log` | `id` SERIAL, `event_type`, `file_path`, `doc_type` | Design/Dev agent event log |

### jobs.*
Career intelligence. Written by CoS Loop A.

| Table | Key columns | Notes |
|---|---|---|
| `jobs.career_opportunities` | `id` SERIAL, `url` UNIQUE, `fit_score`, `status` | suggested / reviewed / applied / dismissed |

---

## Adding a new schema (pattern to follow)

**Hard rules — both must be in the same migration file, same PR:**
- `robert_ro` SELECT grant goes in the migration, not as a follow-up fix
- `test_schema.py` updated with the new schema/tables before the PR merges

The research schema migration (2026-06-10) needed a follow-up fix for the
robert_ro grant — that's the gap this pattern is designed to prevent.

1. **Add to `init_db.sql`** — create schema, grant minimoi + robert_ro:
   ```sql
   CREATE SCHEMA IF NOT EXISTS german;
   GRANT ALL ON SCHEMA german TO minimoi;
   GRANT USAGE ON SCHEMA german TO robert_ro;
   ALTER DEFAULT PRIVILEGES IN SCHEMA german GRANT ALL ON TABLES    TO minimoi;
   ALTER DEFAULT PRIVILEGES IN SCHEMA german GRANT ALL ON SEQUENCES TO minimoi;
   ALTER DEFAULT PRIVILEGES IN SCHEMA german GRANT SELECT ON TABLES TO robert_ro;
   ```

2. **Create a `schema_<domain>.sql`** file — tables prefixed with `schema_name.`,
   and include the `robert_ro` grant for tables that already exist:
   ```sql
   CREATE TABLE IF NOT EXISTS german.sessions (
       id         TEXT PRIMARY KEY,
       ...
   );
   -- Grant read access on any tables created above (covers existing tables;
   -- ALTER DEFAULT PRIVILEGES in init_db.sql covers future tables)
   GRANT SELECT ON ALL TABLES IN SCHEMA german TO robert_ro;
   ```

3. **Update `test_schema.py`** — add the new schema and every new table
   to the relevant check blocks before the PR merges. The test must pass
   at 100% on the first run with the migration applied.

4. **Qualify every SQL reference** in Python — always `schema.table`, never bare:
   ```python
   cur.execute("SELECT * FROM german.sessions WHERE id = %s", (session_id,))
   ```

5. **Document here** — add a row to the tables section above, and update
   the Bootstrap section with the new schema file.

---

## Bootstrap (fresh container)

Run in order:
```bash
docker exec -i postgres-ai-agents psql -U postgres -d personal_agents \
  < domains/guild/db/init_db.sql

docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents \
  < domains/guild/db/schema.sql

docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents \
  < domains/guild/db/schema_phase4.sql

docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents \
  < domains/guild/db/schema_phase5.sql
```

Verify:
```bash
venv/bin/python3 domains/guild/db/test_schema.py
# Expected: 23/23 checks passed
```

---

## Users

| User | Role | Password | Access |
|---|---|---|---|
| `postgres` | Superuser | `simple123` | Everything |
| `minimoi` | App user | `simple123` | Full CRUD on research.*, guild.*, jobs.* |
| `robert_ro` | Read-only | `simple123` | SELECT only on all named schemas |

TablePlus connection: `localhost:5432 / personal_agents / robert_ro / simple123`

---

## What stays as JSON (not promoted to DB)

These are source-of-truth files — DB is a rebuildable projection only where needed:

| Data | File | Reason |
|---|---|---|
| German sessions | `domains/german/data/sessions/` | High write volume, no query need |
| Curator signals | `curator_signals.json` | 425+ entries, append-only, no relational queries |
| CoS memory | `data/guild/memory/cos_memory.md` | Plain text, LLM context — no SQL needed |
| CoS context | `domains/guild/config/cos_context.json` | Config file — read on startup |

**Promote to DB when:** you need JOIN queries, filtering by multiple fields, or aggregation across rows that would require scanning the full JSON file on every request.
