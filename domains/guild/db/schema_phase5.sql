-- Guild Phase 5 schema additions — Design/Dev agent
-- Apply: psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" -f domains/guild/db/schema_phase5.sql

CREATE TABLE IF NOT EXISTS guild.design_log (
    id           SERIAL PRIMARY KEY,
    timestamp    TIMESTAMPTZ DEFAULT now(),
    event_type   TEXT,
    file_path    TEXT,
    doc_type     TEXT,
    summary      TEXT,
    flagged      BOOLEAN DEFAULT FALSE,
    flag_reason  TEXT,
    agent_source TEXT
);
