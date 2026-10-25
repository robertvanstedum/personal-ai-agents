-- Internal authority records, never writable by a model/runtime service role.
CREATE TABLE IF NOT EXISTS guild.mc_action_grants (
  grant_id uuid PRIMARY KEY,
  principal_id text NOT NULL,
  environment text NOT NULL CHECK(environment IN ('dev','prod')),
  definition jsonb NOT NULL,
  definition_digest text NOT NULL CHECK(definition_digest ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  max_operations integer NOT NULL CHECK(max_operations BETWEEN 1 AND 100),
  consumed_operations integer NOT NULL DEFAULT 0 CHECK(consumed_operations >= 0),
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK(consumed_operations <= max_operations)
);
CREATE TABLE IF NOT EXISTS guild.mc_action_grant_consumptions (
  grant_id uuid NOT NULL REFERENCES guild.mc_action_grants(grant_id),
  principal_id text NOT NULL,
  environment text NOT NULL,
  operation_id uuid NOT NULL,
  intent_digest text NOT NULL CHECK(intent_digest ~ '^[0-9a-f]{64}$'),
  consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(grant_id,operation_id),
  UNIQUE(principal_id,environment,operation_id),
  FOREIGN KEY(principal_id,environment,operation_id)
    REFERENCES guild.mc_action_receipts(principal_id,environment,operation_id)
);
