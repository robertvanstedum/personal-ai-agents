-- Verified evidence for MC guest-request actions (additive; not applied to any application DB
-- until reviewed integration). One row per finished execution, written in the same transaction
-- as the domain change and the receipt finish. Receipts reference evidence_id only.
CREATE TABLE IF NOT EXISTS guild.mc_guest_action_evidence (
  evidence_id      UUID PRIMARY KEY,
  principal_id     TEXT NOT NULL,
  environment      TEXT NOT NULL CHECK (environment IN ('dev','prod')),
  operation_id     UUID NOT NULL,
  request_id       INTEGER NOT NULL,
  outcome          TEXT NOT NULL CHECK (outcome IN ('rejected','already_rejected','conflict','missing')),
  status_after     TEXT,
  actioned_at      TIMESTAMPTZ,
  recorded_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (principal_id, environment, operation_id)
);
