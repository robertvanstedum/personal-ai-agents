#!/bin/bash
# Lesen RSS refresh — hourly via launchd, with time gate + idempotency.
# Domain engine: german_domain.refresh_lesen_feed()
# Logs: logs/lesen_refresh.log
# Pattern: mirrors run_curator_cron.sh — time gate + idempotency + venv

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

# ── Time gate: 6AM–10PM (language practice window) ───────────────────────────
HOUR=$(date +%H)
if [ "$HOUR" -lt 6 ] || [ "$HOUR" -ge 22 ]; then
    echo "⏭  Outside 6AM–10PM window (hour=$HOUR) — skipping"
    exit 0
fi

# ── Idempotency: skip if already refreshed today ─────────────────────────────
TODAY=$(date +%Y-%m-%d)
LESEN_JSON="$PROJECT_DIR/domains/german/data/config/lesen_articles.json"
if [ -f "$LESEN_JSON" ]; then
    FILE_DATE=$(python3 -c "
import json
with open('$LESEN_JSON') as f:
    d = json.load(f)
print(d.get('last_fetched', '')[:10])
" 2>/dev/null)
    if [ "$FILE_DATE" = "$TODAY" ]; then
        echo "✅ Lesen already refreshed today ($TODAY) — skipping"
        exit 0
    fi
fi

echo "📰 Starting lesen refresh at $(date)..."

# Activate virtual environment
source venv/bin/activate

python3 -c "
import sys; sys.path.insert(0, '$PROJECT_DIR/domains/german')
from german_domain import refresh_lesen_feed
result = refresh_lesen_feed()
print(f'✅ Lesen refresh complete: {result}')
"

if [ $? -eq 0 ]; then
    echo "✅ Lesen refresh completed at $(date)"
    exit 0
else
    echo "❌ ERROR: Lesen refresh failed at $(date)"
    exit 1
fi
