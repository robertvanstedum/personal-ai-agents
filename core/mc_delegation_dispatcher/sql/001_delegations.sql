-- MC delegations (additive; not applied to any application DB until reviewed integration).
-- One row per delegated job. The Mac runner claims under a lease and generation; a stale
-- generation can never complete someone else's claim. Results are idempotent by event_id.
CREATE TABLE IF NOT EXISTS guild.mc_delegations (
  request_id       UUID PRIMARY KEY,
  job_id           TEXT NOT NULL UNIQUE,
  principal_id     TEXT NOT NULL,
  environment      TEXT NOT NULL CHECK (environment IN ('dev','prod')),
  actor            TEXT NOT NULL CHECK (actor IN ('claude-code','codex')),
  scope            TEXT NOT NULL CHECK (scope IN ('research','build')),
  repository       TEXT NOT NULL,
  ref              TEXT NOT NULL,
  brief            TEXT NOT NULL,
  cost_cap_usd     NUMERIC(8,2) NOT NULL CHECK (cost_cap_usd > 0),
  deadline         TIMESTAMPTZ NOT NULL,
  state            TEXT NOT NULL DEFAULT 'queued' CHECK (state IN
                   ('queued','claimed','running','completed','failed','refused','timed_out','cancelled',
                    'output_limit','interrupted')),
  generation       INTEGER NOT NULL DEFAULT 0,
  runner_id        TEXT,
  lease_until      TIMESTAMPTZ,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  result_event_id  UUID,
  result_detail    JSONB,
  artifacts        JSONB,
  output_text      TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ,
  heartbeat_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS mc_delegations_queue ON guild.mc_delegations (state, created_at);
CREATE TABLE IF NOT EXISTS guild.mc_delegation_runners (
  runner_id     TEXT PRIMARY KEY,
  state         TEXT NOT NULL,
  capabilities  JSONB NOT NULL,
  seen_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
