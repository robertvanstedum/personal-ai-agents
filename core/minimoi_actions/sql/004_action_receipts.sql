-- Additive, explicit operator migration. Does not enable any operation.
CREATE TABLE IF NOT EXISTS guild.mc_action_receipts (
  principal_id text NOT NULL,
  environment text NOT NULL CHECK(environment IN ('dev','prod')),
  operation_id uuid NOT NULL,
  receipt_id uuid NOT NULL UNIQUE,
  actor_id text NOT NULL,
  conversation_id text NOT NULL,
  turn_id text NOT NULL,
  work_id text,
  capability text NOT NULL,
  capability_version bigint NOT NULL CHECK(capability_version > 0),
  request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
  intent_digest text NOT NULL CHECK(intent_digest ~ '^[0-9a-f]{64}$'),
  grant_id text,
  proposal_id uuid,
  policy_version text NOT NULL,
  role_version text NOT NULL,
  expected_target_version text,
  reservation_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  state text NOT NULL DEFAULT 'prepared'
    CHECK(state IN ('prepared','dispatched','verified','failed','unknown','cancelled')),
  cancel_requested boolean NOT NULL DEFAULT false,
  generation bigint NOT NULL DEFAULT 0 CHECK(generation >= 0),
  lease_hash text,
  lease_until timestamptz,
  lease_mode text CHECK(lease_mode IN ('execute','reconcile')),
  worker_id text,
  event_sequence bigint NOT NULL DEFAULT 0 CHECK(event_sequence >= 0),
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(principal_id,environment,operation_id),
  CHECK ((state = 'verified') = (verified_at IS NOT NULL)),
  CHECK ((lease_hash IS NULL AND lease_until IS NULL AND lease_mode IS NULL AND worker_id IS NULL)
      OR (lease_hash IS NOT NULL AND lease_until IS NOT NULL AND lease_mode IS NOT NULL AND worker_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS guild.mc_action_events (
  principal_id text NOT NULL,
  environment text NOT NULL,
  operation_id uuid NOT NULL,
  sequence bigint NOT NULL CHECK(sequence > 0),
  event_type text NOT NULL CHECK(event_type IN
    ('prepared','claimed','dispatched','verified','failed','unknown','cancel_requested','cancelled')),
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(principal_id,environment,operation_id,sequence),
  FOREIGN KEY(principal_id,environment,operation_id)
    REFERENCES guild.mc_action_receipts(principal_id,environment,operation_id)
);
