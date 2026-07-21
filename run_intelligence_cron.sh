#!/bin/bash
# Intelligence cron job — WS5 Phase A daily observation layer
# Runs once daily at 6PM via launchd StartCalendarInterval.
# Requires briefing to have run first — checks curator_latest.json is from today.

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

# ── Persistent data + idempotency ──────────────────────────────────────
TODAY_ISO=$(date +%Y-%m-%d)
TODAY_COMPACT=$(date +%Y%m%d)
export CURATOR_DATA_DIR="${CURATOR_DATA_DIR:-$PROJECT_DIR/data/curator}"
INTEL_FILE="$CURATOR_DATA_DIR/intelligence_${TODAY_COMPACT}.json"
if [ -f "$INTEL_FILE" ]; then
    echo "✅ Intelligence already ran today ($TODAY_ISO) — skipping"
    exit 0
fi

# ── Dependency check: briefing must have run first ───────────────────────────
LATEST="$PROJECT_DIR/curator_latest.json"
if [ -f "$LATEST" ]; then
    FILE_DATE=$(python3 -c "import json; d=json.load(open('$LATEST')); print((d[0].get('briefing_date') or d[0].get('date',''))[:10])" 2>/dev/null)
    if [ "$FILE_DATE" != "$TODAY_ISO" ]; then
        echo "⏳ Briefing not yet run today — intelligence will wait"
        exit 0
    fi
else
    echo "⏳ curator_latest.json not found — intelligence will wait"
    exit 0
fi

echo "🧠 Running intelligence pipeline at $(date)..."

# Activate virtual environment
source venv/bin/activate

export TELEGRAM_CHAT_ID="8379221702"
python curator_intelligence.py --telegram

if [ $? -eq 0 ]; then
    echo "✅ Intelligence run completed successfully at $(date)"
    exit 0
else
    echo "❌ ERROR: Intelligence pipeline failed at $(date)"
    exit 1
fi
