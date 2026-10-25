-- Authoritative prepared bytes remain in the portal database, never in model-writable _working.
-- No Effect lease token is persisted. Unknown effects are recovered by read-only reconciliation.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_host_artifacts (
 operation_id uuid PRIMARY KEY, principal_id text NOT NULL, environment text NOT NULL,
 conversation_id text NOT NULL, capability text NOT NULL,
 payload bytea NOT NULL CHECK (octet_length(payload)<=1048576), payload_sha256 text NOT NULL,
 expected jsonb NOT NULL, summary jsonb NOT NULL, root text NOT NULL,
 state text NOT NULL CHECK (state IN ('prepared','dispatched','unknown','verified')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS guild.mc_host_artifact_challenges (
 challenge_id uuid PRIMARY KEY, operation_id uuid NOT NULL REFERENCES guild.mc_host_artifacts(operation_id),
 ledger_id bigint NOT NULL, claim_generation integer NOT NULL, tool_call_id text NOT NULL, tool text NOT NULL,
 policy_version text NOT NULL, instance_id text NOT NULL, request_sha256 text NOT NULL, nonce_sha256 text NOT NULL,
 mode text NOT NULL CHECK (mode IN ('create','verify')), expires_at timestamptz NOT NULL,
 started_at timestamptz, completed_at timestamptz,
 UNIQUE (ledger_id,claim_generation,tool_call_id),
 FOREIGN KEY (ledger_id,claim_generation,tool_call_id) REFERENCES guild.mc_tool_admissions(ledger_id,claim_generation,tool_call_id)
);
COMMIT;
