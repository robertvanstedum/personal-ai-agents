-- Pending owner proposals for guest actions (additive; not applied anywhere until integration).
-- A proposal is MC's exact suggested effect, shown to the owner; only the owner's explicit
-- confirmation in the SAME conversation turns it into a one-time exact-approval grant.
CREATE TABLE IF NOT EXISTS guild.mc_guest_proposals (
  proposal_id      UUID PRIMARY KEY,
  principal_id     TEXT NOT NULL,
  environment      TEXT NOT NULL CHECK (environment IN ('dev','prod')),
  conversation_id  TEXT NOT NULL,
  owner_turn_id    TEXT NOT NULL,
  ref              TEXT NOT NULL,
  target_version   TEXT NOT NULL,
  operation_id     UUID NOT NULL UNIQUE,
  state            TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','confirmed','cancelled','stale')),
  confirm_turn_id  TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS mc_guest_proposals_pending
  ON guild.mc_guest_proposals (principal_id, environment, conversation_id, created_at DESC);
