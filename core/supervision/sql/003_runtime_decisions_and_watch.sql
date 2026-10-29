-- Build supervision, host runtime S1-G (spec v0.5 R3.2-b, R4.6-b, R5.6-b, R7.4-c). Additive and idempotent: three new
-- tables, nothing altered or dropped, no data rewritten. Applying 001, 002 and this again changes nothing. Production's
-- one-decision-per-message path (supervision_decisions.telegram_message_id and its unique index) is untouched; these
-- tables are read and written only when a release turns the runtime link, contact recording or the staging watch on.
-- Lock (review N1): the mapping's foreign key takes SHARE ROW EXCLUSIVE on guild.supervision_decisions for a moment.
-- SET LOCAL bounds that wait to 5 s inside this transaction (or, with BEGIN/COMMIT stripped by mc_prod_db_migrate.sh,
-- inside its one transaction, as the last file); on LockNotAvailable everything rolls back, so apply it again.
BEGIN;
SET LOCAL lock_timeout = '5s';

-- R3.2-b, R7.4-c: one message may carry several decisions (a batched notice, at most five). A decision is in at most
-- one batched message; a reply to that message answers each decision listed in it, each receipt bound to its own
-- words' digest. A decision sent alone keeps using telegram_message_id, as today.
CREATE TABLE IF NOT EXISTS guild.supervision_decision_messages (
    message_id   bigint NOT NULL,
    decision_id  bigint NOT NULL UNIQUE REFERENCES guild.supervision_decisions(id),
    position     smallint NOT NULL CHECK (position BETWEEN 1 AND 5),
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (message_id, decision_id),
    UNIQUE (message_id, position)
);

-- R4.6-b: the last contact of each credential on the supervision endpoint, on every call (pull included), so a rule
-- that reads a view (the Mac sync's mirror) also reads its age. The credential's name, never the token.
CREATE TABLE IF NOT EXISTS guild.supervision_contacts (
    credential   text PRIMARY KEY CHECK (credential ~ '^(worker|watch:[A-Za-z0-9._:-]{1,64})$'),
    first_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_phase   text NOT NULL CHECK (last_phase IN ('report', 'pull', 'ack', 'heartbeat', 'invalid')),
    calls        bigint NOT NULL DEFAULT 1 CHECK (calls >= 1)
);

-- R5.6-b: production's notify-only watch of staging. One row per watched host, written only by that host's heartbeat
-- credential (its heartbeat row and nothing else); armed by its first heartbeat, never disarmed by silence. view is
-- staging's own account (authority answering, sweeper age, lapsed always-on items) with the time it was taken.
-- silent_since is set by the check-in when it told Robert the host went silent, and cleared when it told him it is back.
CREATE TABLE IF NOT EXISTS guild.supervision_watch (
    host_id      text PRIMARY KEY CHECK (host_id ~ '^[A-Za-z0-9._:-]{1,64}$'),
    credential   text NOT NULL CHECK (credential = 'watch:' || host_id),
    first_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    view         jsonb NOT NULL CHECK (jsonb_typeof(view) = 'object'),
    silent_since timestamptz
);
COMMIT;
