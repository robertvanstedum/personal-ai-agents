#!/bin/bash
# Curator AI Observations cron job for EC2.
# Runs after the daily briefing is present and skips once today's output exists.
# Sunday runs also generate the weekly synthesis inside curator_intelligence.py.

set -u

LOG_PREFIX="[intelligence-cron]"
CONTAINER="minimoi-curator"
DATA_DIR="/app/data/curator"

# Exit silently unless this is the production node.
ROLE=$(docker exec "$CONTAINER" python -c \
  "from utils.role import role_label; print(role_label())" 2>/dev/null)
if [ "$ROLE" != "production" ]; then
  echo "$LOG_PREFIX Standby node (MINIMOI_ROLE=$ROLE) — skipping"
  exit 0
fi

TODAY_ISO=$(date -u +%Y-%m-%d)
TODAY_COMPACT=$(date -u +%Y%m%d)
OUTPUT_FILE="$DATA_DIR/intelligence_${TODAY_COMPACT}.json"

# Idempotency: never pay for or send the same daily observation twice.
if docker exec "$CONTAINER" test -f "$OUTPUT_FILE"; then
  echo "$LOG_PREFIX Intelligence already ran today ($TODAY_ISO) — skipping"
  exit 0
fi

# The intelligence pass is meaningful only after today's briefing exists.
BRIEFING_DATE=$(docker exec "$CONTAINER" python3 -c "
import json
try:
    with open('data/curator/curator_latest.json') as f:
        data = json.load(f)
    print(data[0].get('briefing_date', data[0].get('date', ''))[:10])
except Exception:
    print('')
" 2>/dev/null)

if [ "$BRIEFING_DATE" != "$TODAY_ISO" ]; then
  echo "$LOG_PREFIX Briefing not ready for $TODAY_ISO — waiting for next run"
  exit 0
fi

echo "$LOG_PREFIX Starting AI Observations at $(date -u)"

if docker exec \
  -e TELEGRAM_CHAT_ID=8379221702 \
  "$CONTAINER" \
  python domains/curator/curator_intelligence.py --telegram --date "$TODAY_ISO"; then
  if docker exec "$CONTAINER" test -f "$OUTPUT_FILE"; then
    echo "$LOG_PREFIX AI Observations complete at $(date -u)"
    exit 0
  fi
  echo "$LOG_PREFIX ERROR: pipeline exited successfully but $OUTPUT_FILE is missing"
else
  echo "$LOG_PREFIX ERROR: AI Observations pipeline failed"
fi

exit 1
