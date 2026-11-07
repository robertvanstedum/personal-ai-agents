-- Explicit operator migration: what a direct voice session settled at when it ended (core/realtime_voice/budget.py).
-- Additive and idempotent. Existing rows stay as they are: settled_micro NULL means the full reservation still counts, and micro keeps the original.
-- Admission never reads these columns: until this runs, voice works and every session simply keeps its full reservation.
ALTER TABLE guild.voice_budget_entry ADD COLUMN IF NOT EXISTS settled_micro BIGINT;
ALTER TABLE guild.voice_budget_entry ADD COLUMN IF NOT EXISTS settled_at TEXT;
