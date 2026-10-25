-- Metadata-only host read admissions. No file contents, result text, or raw nonces persist here.
BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_host_reads (
    operation_id uuid PRIMARY KEY,
    ledger_id bigint NOT NULL,
    claim_generation integer NOT NULL,
    tool_call_id text NOT NULL,
    tool text NOT NULL,
    policy_version text NOT NULL,
    instance_id text NOT NULL,
    request_sha256 text NOT NULL,
    nonce_sha256 text NOT NULL,
    expires_at timestamptz NOT NULL,
    completed_at timestamptz,
    result_sha256 text,
    source_sha256 text,
    result_code text,
    UNIQUE (ledger_id, claim_generation, tool_call_id),
    FOREIGN KEY (ledger_id, claim_generation, tool_call_id)
      REFERENCES guild.mc_tool_admissions (ledger_id, claim_generation, tool_call_id)
);
COMMIT;
