-- MC delegations, protocol version 2: the host runtime authority's records (host runtime spec v0.5 R3.2-b, R7.6-d,
-- R8.3-a). Additive and idempotent on top of 001: new nullable or defaulted columns, two wider checks, two new
-- tables. Applying 001 and 002 again changes nothing. No data is rewritten or removed; the only constraints dropped
-- are 001's narrower state and actor checks, each replaced in the same statement block by a wider one.
-- An item is a version 2 item exactly when it names a route (R6.7-e); version 1 rows keep route NULL.

ALTER TABLE guild.mc_delegations
  ADD COLUMN IF NOT EXISTS card_id               TEXT,
  ADD COLUMN IF NOT EXISTS step                  TEXT,
  ADD COLUMN IF NOT EXISTS grant_id              UUID,
  ADD COLUMN IF NOT EXISTS required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb
                           CHECK (jsonb_typeof(required_capabilities) = 'array'),
  ADD COLUMN IF NOT EXISTS progress_at           TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS progress_phase        TEXT,
  ADD COLUMN IF NOT EXISTS checkpoint            JSONB,
  ADD COLUMN IF NOT EXISTS lapsed_since          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS lapse_cause           TEXT,
  ADD COLUMN IF NOT EXISTS grace_deadline        TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS route                 TEXT CHECK (route ~ '^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$'
                                                             AND length(route) <= 64),
  ADD COLUMN IF NOT EXISTS reservation           JSONB,
  ADD COLUMN IF NOT EXISTS facts                 JSONB,
  ADD COLUMN IF NOT EXISTS created_by            TEXT CHECK (created_by IN ('owner','authority')),
  ADD COLUMN IF NOT EXISTS input_artifacts       JSONB NOT NULL DEFAULT '[]'::jsonb
                           CHECK (jsonb_typeof(input_artifacts) = 'array');

-- 001's checks, widened: five new states (§4.2) and the test worker (§9.3). The old check is dropped only while it
-- lacks the new values, so a second run finds the wide one and does nothing.
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT k.conname, a.attname FROM pg_constraint k
           JOIN pg_attribute a ON a.attrelid = k.conrelid AND k.conkey = ARRAY[a.attnum]
           WHERE k.conrelid = 'guild.mc_delegations'::regclass AND k.contype = 'c'
             AND ((a.attname = 'state' AND pg_get_constraintdef(k.oid) NOT LIKE '%''expired''%')
               OR (a.attname = 'actor' AND pg_get_constraintdef(k.oid) NOT LIKE '%''fakework''%'))
  LOOP
    EXECUTE format('ALTER TABLE guild.mc_delegations DROP CONSTRAINT %I', c.conname);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'guild.mc_delegations'::regclass
                 AND conname = 'mc_delegations_state_check') THEN
    ALTER TABLE guild.mc_delegations ADD CONSTRAINT mc_delegations_state_check CHECK (state IN
      ('proposed','queued','claimed','running','lapsed','paused','unknown','completed','failed','refused','timed_out',
       'cancelled','output_limit','interrupted','expired'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'guild.mc_delegations'::regclass
                 AND conname = 'mc_delegations_actor_check') THEN
    ALTER TABLE guild.mc_delegations ADD CONSTRAINT mc_delegations_actor_check
      CHECK (actor IN ('claude-code','codex','fakework'));
  END IF;
  -- R8.3-b in the database: a version 2 item is owned only with its spend reserved (the claim writes both in one
  -- transaction). It also stops a version 1 claim, which knows nothing of reservations, from taking a version 2 item.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'guild.mc_delegations'::regclass
                 AND conname = 'mc_delegations_v2_owned_reserved') THEN
    ALTER TABLE guild.mc_delegations ADD CONSTRAINT mc_delegations_v2_owned_reserved
      CHECK (route IS NULL OR state NOT IN ('claimed','running') OR reservation ? 'reserved');
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS mc_delegations_owner ON guild.mc_delegations (runner_id, state) WHERE runner_id IS NOT NULL;

-- Each host's facts (R3.2-a heartbeat). seen_at (001) is the host's last contact. protocol says what it last spoke.
ALTER TABLE guild.mc_delegation_runners
  ADD COLUMN IF NOT EXISTS host_id               TEXT,
  ADD COLUMN IF NOT EXISTS environment           TEXT CHECK (environment IN ('dev','prod')),
  ADD COLUMN IF NOT EXISTS roles                 JSONB CHECK (jsonb_typeof(roles) = 'array'),
  ADD COLUMN IF NOT EXISTS bundle_digest         TEXT CHECK (bundle_digest ~ '^sha256:[0-9a-f]{64}$'),
  ADD COLUMN IF NOT EXISTS expected_availability TEXT CHECK (expected_availability IN ('always_on','intermittent')),
  ADD COLUMN IF NOT EXISTS unreachable_grace_s   INTEGER CHECK (unreachable_grace_s > 0),
  ADD COLUMN IF NOT EXISTS announced             TEXT CHECK (announced IN ('online','stopping','stopped')),
  ADD COLUMN IF NOT EXISTS announced_at          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_gap_s            DOUBLE PRECISION CHECK (last_gap_s >= 0),
  ADD COLUMN IF NOT EXISTS protocol              SMALLINT NOT NULL DEFAULT 1 CHECK (protocol IN (1,2));

-- The audit (R7.6-d), and the replay log: a unique event_id applies each event once. kind and body are general, so
-- later kinds (census events among them) need no new column. source: 'host' for what a host reports, 'owner' for an
-- owner's act, 'authority' for what the authority decides. at is when it happened (the host's time for a host report).
CREATE TABLE IF NOT EXISTS guild.mc_delegation_events (
  event_id     UUID PRIMARY KEY,
  seq          BIGINT GENERATED ALWAYS AS IDENTITY,
  schema       TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind ~ '^[a-z][a-z_]*(\.[a-z][a-z_]*)+$' AND length(kind) <= 64),
  principal_id TEXT NOT NULL,
  environment  TEXT NOT NULL CHECK (environment IN ('dev','prod')),
  item_id      UUID,
  generation   INTEGER,
  host_id      TEXT,
  source       TEXT NOT NULL CHECK (source IN ('authority','host','owner')),
  at           TIMESTAMPTZ NOT NULL,
  recorded_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  body         JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(body) = 'object')
);
CREATE INDEX IF NOT EXISTS mc_delegation_events_item ON guild.mc_delegation_events (item_id, seq) WHERE item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS mc_delegation_events_kind ON guild.mc_delegation_events (environment, kind, seq);

-- Spend reserved across hosts (R8.3-a): one row per environment, route and America/Chicago day, in one unit. An
-- amount is only ever added to a row of its own unit, so different units are never summed. usage_hold is set when a
-- result's usage is unknown; while any row of a route holds one, the route admits nothing until a recorded
-- resolution clears it (it survives the day, as the connection manager's hold does).
CREATE TABLE IF NOT EXISTS guild.mc_delegation_reservations (
  environment TEXT NOT NULL CHECK (environment IN ('dev','prod')),
  route       TEXT NOT NULL,
  day         DATE NOT NULL,
  unit        TEXT NOT NULL CHECK (unit IN ('usd','usd_notional','minutes','test_units')),
  cap         NUMERIC(14,4) CHECK (cap >= 0),
  reserved    NUMERIC(14,4) NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  observed    NUMERIC(14,4) NOT NULL DEFAULT 0 CHECK (observed >= 0),
  usage_hold  JSONB,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (environment, route, day)
);
CREATE INDEX IF NOT EXISTS mc_delegation_reservations_held ON guild.mc_delegation_reservations (environment, route)
  WHERE usage_hold IS NOT NULL;
