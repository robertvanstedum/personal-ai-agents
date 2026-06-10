-- schema_challenger.sql — Synthesizer + Challenger pattern storage
-- All domains write to this single table.
-- Apply: docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents \
--          < domains/guild/db/schema_challenger.sql

CREATE TABLE IF NOT EXISTS guild.challenger_exchanges (
    id                  SERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ DEFAULT now(),

    -- What domain and feature
    domain              TEXT NOT NULL,  -- curator_deep_dive, german_writing, guild_career_assessment, etc.
    feature             TEXT NOT NULL,  -- deeper_dive, observe, writing_correction, career_fit, etc.
    entity_id           INTEGER,        -- FK to domain entity (topic id, session id, etc.) — nullable
    entity_description  TEXT,           -- human-readable: thread name, session title, etc.

    -- Models used
    primary_model       TEXT,           -- claude-sonnet-4-6
    challenger_model    TEXT,           -- grok-4-1

    -- Exchange (digest — not full text)
    first_pass_summary  TEXT,           -- 1-2 sentence summary of what primary concluded
    challenge_points    JSONB,          -- [{type, description, accepted, impact}]
    key_change          TEXT,           -- one sentence: what changed most in final

    -- Counts (derived from challenge_points — for fast aggregation queries)
    challenged_count    INTEGER DEFAULT 0,
    accepted_count      INTEGER DEFAULT 0,
    rejected_count      INTEGER DEFAULT 0,

    -- Config flags at time of run
    show_process_flag   BOOLEAN DEFAULT FALSE,
    prompt_version      TEXT,           -- e.g. "curator_challenger_v1"

    -- Change detection (no full text stored)
    first_pass_hash     TEXT,
    final_hash          TEXT,
    outputs_differ      BOOLEAN         -- true if first_pass != final
);

CREATE INDEX IF NOT EXISTS idx_challenger_domain    ON guild.challenger_exchanges (domain);
CREATE INDEX IF NOT EXISTS idx_challenger_timestamp ON guild.challenger_exchanges (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_challenger_entity    ON guild.challenger_exchanges (entity_id) WHERE entity_id IS NOT NULL;

-- robert_ro read access — must be in same migration file (per DB_SCHEMA.md pattern)
GRANT SELECT ON guild.challenger_exchanges TO robert_ro;

\echo 'schema_challenger.sql complete — guild.challenger_exchanges created'
