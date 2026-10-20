-- Durable execution payload/evidence for an explicitly authorized owner edit.
-- Operator-applied migration only; no route, tool or capability is enabled here.
CREATE TABLE IF NOT EXISTS guild.mc_owner_annotation_intents (
  principal_id text NOT NULL,
  environment text NOT NULL CHECK(environment IN ('dev','prod')),
  operation_id uuid NOT NULL,
  receipt_id uuid NOT NULL UNIQUE REFERENCES guild.mc_action_receipts(receipt_id),
  payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
  payload_digest text NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
  evidence_id uuid NOT NULL UNIQUE,
  dispatch_transaction bigint,
  verified_sequence bigint CHECK(verified_sequence > 0),
  verified_payload_hash text CHECK(verified_payload_hash ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY(principal_id,environment,operation_id),
  FOREIGN KEY(principal_id,environment,operation_id)
    REFERENCES guild.mc_action_receipts(principal_id,environment,operation_id),
  CHECK ((verified_sequence IS NULL) = (verified_payload_hash IS NULL))
);
