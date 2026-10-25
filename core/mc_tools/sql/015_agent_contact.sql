BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_agent_contacts (
 operation_id uuid PRIMARY KEY,
 principal_id text NOT NULL, environment text NOT NULL, conversation_id text NOT NULL,
 note_request_id text NOT NULL, target_alias text NOT NULL, target jsonb NOT NULL,
 source jsonb NOT NULL, caller jsonb NOT NULL, action text NOT NULL,
 state text NOT NULL DEFAULT 'pending', native_request_id text,
 outcome jsonb, nonce_sha256 text, challenge_expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 next_observe_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 dispatch_expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '30 seconds',
 watch_expires_at timestamptz, watch_cancelled boolean NOT NULL DEFAULT false,
 notice_text text, notice_sha256 text, notice_delivered_at timestamptz,
 UNIQUE(principal_id,environment,conversation_id,note_request_id,target_alias)
);
COMMIT;
