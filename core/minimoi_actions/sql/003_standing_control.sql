-- Additive, operator-applied. No owner/runtime authority is inferred from a row.
CREATE TABLE IF NOT EXISTS guild.mc_standing_control (
  environment text NOT NULL CHECK(environment IN ('dev','prod')),
  principal_id text NOT NULL,
  version bigint NOT NULL DEFAULT 0 CHECK(version >= 0),
  desired_state text NOT NULL DEFAULT 'paused' CHECK(desired_state IN ('paused','enabled')),
  request_id uuid,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(environment,principal_id)
);
CREATE TABLE IF NOT EXISTS guild.mc_standing_control_requests (
  environment text NOT NULL,
  principal_id text NOT NULL,
  request_id uuid NOT NULL,
  action text NOT NULL CHECK(action IN ('pause','resume')),
  expected_version bigint NOT NULL CHECK(expected_version >= 0),
  applied_version bigint NOT NULL CHECK(applied_version > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(environment,principal_id,request_id),
  FOREIGN KEY(environment,principal_id) REFERENCES guild.mc_standing_control(environment,principal_id)
);
