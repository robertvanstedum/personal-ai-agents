#!/bin/bash
# Curator cron job for EC2 — mirrors run_curator_cron.sh but runs via docker exec.
# Scheduled hourly via crontab (see setup_ec2_cron.sh). Idempotent — skips if
# briefing already ran today or node is not production.

LOG_PREFIX="[curator-cron]"

# ── Role guard: exit silently if this node is not production ──────────────────
ROLE=$(docker exec minimoi-curator python -c \
  "from utils.role import role_label; print(role_label())" 2>/dev/null)
if [ "$ROLE" != "production" ]; then
  echo "$LOG_PREFIX Standby node (MINIMOI_ROLE=$ROLE) — skipping"
  exit 0
fi

# ── Time gate: only run between 11:00 and 17:00 UTC (6AM–12PM CDT) ───────────
HOUR=$(date -u +%H)
if [ "$HOUR" -lt 11 ] || [ "$HOUR" -ge 17 ]; then
  echo "$LOG_PREFIX Outside 11:00–17:00 UTC window (hour=$HOUR UTC) — skipping"
  exit 0
fi

# ── Idempotency: skip if briefing already ran today ───────────────────────────
TODAY=$(date -u +%Y-%m-%d)
FILE_DATE=$(docker exec minimoi-curator python3 -c "
import json
try:
    d = json.load(open('curator_latest.json'))
    print(d[0].get('briefing_date', d[0].get('date', ''))[:10])
except Exception:
    print('')
" 2>/dev/null || true)

if [ "$FILE_DATE" = "$TODAY" ]; then
  echo "$LOG_PREFIX Briefing already ran today ($TODAY) — skipping"
  exit 0
fi

echo "$LOG_PREFIX Starting briefing at $(date -u)"

# ── Pull new X bookmarks ──────────────────────────────────────────────────────
echo "$LOG_PREFIX Pulling X bookmarks..."
docker exec minimoi-curator python x_pull_incremental.py 2>&1 || \
  echo "$LOG_PREFIX x_pull_incremental.py failed — continuing with existing signals"

# ── Run curator pipeline + send Telegram briefing ────────────────────────────
echo "$LOG_PREFIX Running RSS curation (grok-4.3)..."
docker exec minimoi-curator python curator_rss_v2.py \
  --model=grok-4.3 --fallback --temperature=0.7 --telegram

STATUS=$?

if [ $STATUS -eq 0 ]; then
  # Stamp briefing_date so idempotency check holds across midnight
  docker exec minimoi-curator python3 -c "
import json, datetime
with open('curator_latest.json') as f:
    data = json.load(f)
data[0]['briefing_date'] = datetime.date.today().isoformat()
with open('curator_latest.json', 'w') as f:
    json.dump(data, f)
" 2>/dev/null || true
  echo "$LOG_PREFIX Briefing complete at $(date -u)"
else
  echo "$LOG_PREFIX ERROR: curator_rss_v2.py exited with status $STATUS"
  exit 1
fi
