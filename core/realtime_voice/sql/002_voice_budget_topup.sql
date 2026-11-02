-- Explicit operator migration before deploying dated-top-up admission code.
-- No web endpoint grants allowance; an owner-approved operator uses grant_today.
CREATE TABLE IF NOT EXISTS guild.voice_budget_topup (
    approval TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    micro BIGINT NOT NULL CHECK (micro > 0),
    created_at TEXT NOT NULL
);
