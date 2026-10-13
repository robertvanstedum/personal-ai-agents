-- Voice budget (core/realtime_voice/budget.py): one row per America/Chicago day and an audit entry per reservation.
-- Additive and idempotent. Applied by hand (or `python -m core.realtime_voice.budget --init`) BEFORE voice is switched on; never needed to run existing features.

-- Use the existing application-owned Guild schema; public access is not required.
CREATE SCHEMA IF NOT EXISTS guild;

CREATE TABLE IF NOT EXISTS guild.voice_budget_day (
    day TEXT PRIMARY KEY,
    cap_micro BIGINT NOT NULL CHECK (cap_micro >= 0),
    reserved_micro BIGINT NOT NULL DEFAULT 0 CHECK (reserved_micro >= 0),
    created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS guild.voice_budget_entry (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL REFERENCES guild.voice_budget_day(day),
    at TEXT NOT NULL,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    who TEXT NOT NULL,
    micro BIGINT NOT NULL CHECK (micro > 0),
    state TEXT NOT NULL CHECK (state IN ('reserved', 'released')));

CREATE INDEX IF NOT EXISTS voice_budget_entry_day ON guild.voice_budget_entry (day);
