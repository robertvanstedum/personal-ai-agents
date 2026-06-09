-- Guild Phase 4 schema additions
-- Apply: psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" -f domains/guild/db/schema_phase4.sql

-- Job opportunities found by Loop A (career focus scout)
CREATE TABLE IF NOT EXISTS jobs.career_opportunities (
    id                   SERIAL PRIMARY KEY,
    title                TEXT NOT NULL,
    company              TEXT,
    geo                  TEXT,
    url                  TEXT UNIQUE,
    opportunity_type     TEXT,        -- employment / contract / advisory
    fit_score            NUMERIC(4,2),
    fit_narrative        TEXT,
    warm_lead            BOOLEAN DEFAULT FALSE,
    warm_lead_contacts   TEXT,
    cos_notes            TEXT,
    source               TEXT,        -- tavily / rss / direct
    model_used           TEXT,
    status               TEXT DEFAULT 'suggested',  -- suggested / reviewed / applied / dismissed
    created_by           TEXT DEFAULT 'cos_loop_a',
    created_at           TIMESTAMPTZ DEFAULT now()
);

-- CoS recommendations queue — all loops write here, Robert acts in portal
CREATE TABLE IF NOT EXISTS guild.cos_agenda (
    id           SERIAL PRIMARY KEY,
    domain       TEXT,        -- career_focus / german / curator / mini_moi / operations
    description  TEXT,
    confidence   NUMERIC(3,2),
    loop_name    TEXT,
    status       TEXT DEFAULT 'pending',  -- pending / confirmed / dismissed / deferred
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Learning signal: what Robert did with each recommendation
CREATE TABLE IF NOT EXISTS guild.agent_feedback (
    id                SERIAL PRIMARY KEY,
    agent_name        TEXT DEFAULT 'cos',
    recommendation_id INT,
    signal_type       TEXT,   -- confirmed / dismissed / deferred / click_through
    domain            TEXT,
    item_type         TEXT,   -- job_opportunity / tool_found / topic_suggested / competitive_find
    created_at        TIMESTAMPTZ DEFAULT now()
);
