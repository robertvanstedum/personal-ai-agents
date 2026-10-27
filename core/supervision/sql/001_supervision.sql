-- Build supervision (#365): agents' stop lines, decisions waiting on Robert and their receipts, fixed nudges back to
-- agents, and the watcher's check-ins. No model writes here: the Mac sync (worker token), the supervisor process and
-- the portal's owner-command path do. Safe to re-run.
BEGIN;
CREATE SCHEMA IF NOT EXISTS guild;

-- One row per stop line an agent printed (or 'unreported' when a Claude Code turn ended without one).
CREATE TABLE IF NOT EXISTS guild.supervision_stops (
    id           bigserial PRIMARY KEY,
    report_key   text NOT NULL UNIQUE CHECK (report_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    agent        text NOT NULL CHECK (agent IN ('claude-code', 'codex')),
    session      text NOT NULL CHECK (session ~ '^[A-Za-z0-9._:-]{4,96}$'),
    state        text NOT NULL CHECK (state IN ('done', 'blocked', 'awaiting_owner', 'awaiting_review', 'waiting_ci',
                                                 'error', 'unreported')),
    needs        text NOT NULL DEFAULT '' CHECK (length(needs) <= 500),
    next         text NOT NULL DEFAULT '' CHECK (length(next) <= 500),
    decision_key text CHECK (decision_key IS NULL OR decision_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    pr           integer CHECK (pr IS NULL OR pr BETWEEN 1 AND 999999),
    issue        integer CHECK (issue IS NULL OR issue BETWEEN 1 AND 999999),
    branch       text NOT NULL DEFAULT '' CHECK (length(branch) <= 200),
    waiting      boolean NOT NULL DEFAULT false,      -- a `stop.py wait` was running for this session when it reported
    stopped_at   timestamptz NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    nudged_at    timestamptz,
    escalated_at timestamptz
);
CREATE INDEX IF NOT EXISTS supervision_stops_session ON guild.supervision_stops (agent, session, stopped_at DESC);

-- A decision only Robert can make, in the exact words it was asked. Answered only by his own Telegram reply to the
-- message that carried it, or by his own owner turn in the Guild chat naming its code; the receipt records which.
CREATE TABLE IF NOT EXISTS guild.supervision_decisions (
    id            bigserial PRIMARY KEY,
    request_key   text NOT NULL UNIQUE CHECK (request_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    requester     jsonb NOT NULL,                      -- {"agent", "session", "branch"}: who asked; never authority
    text          text NOT NULL CHECK (length(text) BETWEEN 10 AND 1500),
    text_sha256   text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    state         text NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'sent', 'approved', 'declined', 'expired')),
    telegram_message_id bigint,
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at    timestamptz NOT NULL DEFAULT clock_timestamp() + interval '24 hours',
    sent_at       timestamptz,
    decided_at    timestamptz,
    receipt       jsonb,
    announced_at  timestamptz,                         -- the bot said on Telegram how it was answered
    delivered_at  timestamptz,                         -- the Mac sync handed the answer to the waiting agent
    CHECK ((state IN ('approved', 'declined')) = (receipt IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS supervision_decisions_message ON guild.supervision_decisions (telegram_message_id)
    WHERE telegram_message_id IS NOT NULL;

-- Fixed-text nudges to one agent session; the text is chosen by kind, never written by a model.
CREATE TABLE IF NOT EXISTS guild.supervision_nudges (
    id           bigserial PRIMARY KEY,
    agent        text NOT NULL CHECK (agent IN ('claude-code', 'codex')),
    session      text NOT NULL CHECK (session ~ '^[A-Za-z0-9._:-]{4,96}$'),
    kind         text NOT NULL CHECK (kind IN ('continue', 'ci_finished')),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    stop_id      bigint REFERENCES guild.supervision_stops(id),
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    delivered_at timestamptz,
    UNIQUE (stop_id, kind)
);

-- Every scheduled check-in, and what it told Robert (keys of items already sent are not sent again).
CREATE TABLE IF NOT EXISTS guild.supervision_checkins (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT clock_timestamp(),
    summary    jsonb NOT NULL,
    told       text[] NOT NULL DEFAULT '{}',
    telegram   text NOT NULL DEFAULT 'none' CHECK (telegram IN ('none', 'sent', 'failed'))
);
CREATE TABLE IF NOT EXISTS guild.supervision_alerts (
    key        text PRIMARY KEY CHECK (length(key) <= 200),
    first_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- The bot's getUpdates offset: one row.
CREATE TABLE IF NOT EXISTS guild.supervision_telegram (
    id      smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    offset_ bigint NOT NULL DEFAULT 0
);
INSERT INTO guild.supervision_telegram (id) VALUES (1) ON CONFLICT DO NOTHING;
COMMIT;
