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

# ── Time gate: only run between 12:00 and 20:00 UTC (7AM–3PM CDT) ────────────
HOUR=$(date -u +%H)
if [ "$HOUR" -lt 12 ] || [ "$HOUR" -ge 20 ]; then
  echo "$LOG_PREFIX Outside 12:00–20:00 UTC window (hour=$HOUR UTC) — skipping"
  exit 0
fi

# ── Idempotency: skip if briefing already ran today ───────────────────────────
TODAY=$(date -u +%Y-%m-%d)
# Only briefing_date counts as "sent today" — d[0]['date'] is the top
# article's own publish date, which is almost always today for freshly
# curated news, so falling back to it here always looked like "already
# sent" even when the Telegram send itself had failed and briefing_date
# was never stamped, permanently masking a failed send for the rest of
# the day's hourly retry window (issue #35).
FILE_DATE=$(docker exec minimoi-curator python3 -c "
import json
try:
    d = json.load(open('data/curator/curator_latest.json'))
    print(d[0].get('briefing_date', '')[:10])
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
docker exec minimoi-curator python -m scripts.x.x_pull_incremental 2>&1 || \
  echo "$LOG_PREFIX scripts.x.x_pull_incremental failed — continuing with existing signals"

# ── Run curator pipeline, then send briefing with inline buttons ──────────────
echo "$LOG_PREFIX Running RSS curation (grok-4.3)..."
docker exec minimoi-curator python domains/curator/curator_rss_v2.py \
  --model=grok-4.3 --fallback --temperature=0.7

echo "$LOG_PREFIX Sending Telegram briefing (system bot)..."
docker exec -e TELEGRAM_CHAT_ID=8379221702 minimoi-curator python core/telegram/telegram_bot.py --send

STATUS=$?

if [ $STATUS -eq 0 ]; then
  # Stamp briefing_date so idempotency check holds across midnight
  docker exec minimoi-curator python3 -c "
import json, datetime
with open('data/curator/curator_latest.json') as f:
    data = json.load(f)
data[0]['briefing_date'] = datetime.date.today().isoformat()
with open('data/curator/curator_latest.json', 'w') as f:
    json.dump(data, f)
" 2>/dev/null || true
  echo "$LOG_PREFIX Briefing complete at $(date -u)"
else
  # STATUS is telegram_bot.py --send's exit code (captured immediately
  # above), not curator_rss_v2.py's — the message previously blamed the
  # wrong command, which cost real time diagnosing this.
  echo "$LOG_PREFIX ERROR: telegram_bot.py --send exited with status $STATUS"
  exit 1
fi
