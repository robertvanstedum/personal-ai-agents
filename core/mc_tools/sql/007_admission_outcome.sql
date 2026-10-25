-- MC first light M1.4 (spec v1.2 §16.4 A24; §17 trace). Additive; after 001–006.
-- The outcome code of each admitted tool call, so the trial can count owner requests that an existing unrelated offer
-- blocked (offer_pending), against all action requests, from the records themselves (no analytics service).
-- Rollback: previous code ignores the columns.
BEGIN;
ALTER TABLE guild.mc_tool_admissions
    ADD COLUMN IF NOT EXISTS result_status text,
    ADD COLUMN IF NOT EXISTS result_code text CHECK (result_code ~ '^[a-z0-9_]{1,64}$'),
    ADD COLUMN IF NOT EXISTS finished_at timestamptz;
COMMIT;
