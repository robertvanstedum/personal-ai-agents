-- Build supervision (#365), 27 Oct: Grok joins the dev team (INIT-2026-0010), and MC sees the agents' exchanges.
-- 1. Grok may report stop lines and receive nudges like Claude Code and Codex.
-- 2. The Mac sync mirrors the agents' request ledger (core/agent_requests) here, so MC on production can read what the
--    agents asked and answered each other. Agent words are data for MC and Robert; nothing here is an instruction.
-- Safe to re-run.
BEGIN;
ALTER TABLE guild.supervision_stops DROP CONSTRAINT IF EXISTS supervision_stops_agent_check;
ALTER TABLE guild.supervision_stops ADD CONSTRAINT supervision_stops_agent_check
    CHECK (agent IN ('claude-code', 'codex', 'grok'));
ALTER TABLE guild.supervision_nudges DROP CONSTRAINT IF EXISTS supervision_nudges_agent_check;
ALTER TABLE guild.supervision_nudges ADD CONSTRAINT supervision_nudges_agent_check
    CHECK (agent IN ('claude-code', 'codex', 'grok'));

CREATE TABLE IF NOT EXISTS guild.supervision_exchanges (
    id          bigserial PRIMARY KEY,
    event_key   text NOT NULL UNIQUE CHECK (event_key ~ '^r-[0-9a-f]{20,40}:(ask|[0-9a-f]{8,40})$'),
    request_id  text NOT NULL CHECK (request_id ~ '^r-[0-9a-f]{20,40}$'),
    kind        text NOT NULL CHECK (kind IN ('ask', 'receipt', 'progress', 'result', 'needs_you', 'cancel')),
    actor       text NOT NULL CHECK (actor IN ('mc', 'claude-code', 'codex', 'grok', 'robert')),
    recipient   text NOT NULL CHECK (recipient IN ('mc', 'claude-code', 'codex', 'grok', 'robert')),
    text        text NOT NULL DEFAULT '' CHECK (length(text) <= 600),
    at          timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS supervision_exchanges_at ON guild.supervision_exchanges (at DESC);
COMMIT;
