BEGIN;
CREATE TABLE IF NOT EXISTS guild.mc_task_subscriptions (
 subscription_id uuid PRIMARY KEY,
 principal_id text NOT NULL, environment text NOT NULL,
 task_id text NOT NULL, generation text NOT NULL,
 declaration jsonb NOT NULL, declaration_sha256 text NOT NULL,
 state text NOT NULL DEFAULT 'armed', initial_checkpoint jsonb, checkpoint jsonb,
 checkpoint_version bigint NOT NULL DEFAULT 0,
 last_proof jsonb, terminal_proof jsonb,
 nonce_sha256 text, challenge_expires_at timestamptz, staged text NOT NULL DEFAULT '',
 next_observe_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), last_observed_at timestamptz,
 notice_text text, notice_sha256 text, notice_delivered_at timestamptz,
 UNIQUE(principal_id,environment,task_id,generation)
);
COMMIT;
