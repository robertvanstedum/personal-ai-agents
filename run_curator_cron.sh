#!/bin/bash
# Curator cron job - generates briefing + sends to Telegram automatically
# Model: xAI grok-4.1 (--model=grok-4-1) ~$0.30/day — fallback to mechanical if API down
# Runs hourly via launchd StartInterval=3600, with idempotency check (6AM–6PM window).
# Handles Mac sleep/wake — runs as soon as machine is online if briefing was missed.

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

# ── Time gate: only run between 6AM and 6PM ──────────────────────────────────
HOUR=$(date +%H)
if [ "$HOUR" -lt 6 ] || [ "$HOUR" -ge 18 ]; then
    echo "⏭  Outside 6AM–6PM window (hour=$HOUR) — skipping"
    exit 0
fi

# ── Idempotency check: skip if briefing already ran today ────────────────────
TODAY=$(date +%Y-%m-%d)
LATEST="$PROJECT_DIR/curator_latest.json"
if [ -f "$LATEST" ]; then
    FILE_DATE=$(python3 -c "import json; d=json.load(open('$LATEST')); print(d[0].get('date','')[:10])" 2>/dev/null)
    if [ "$FILE_DATE" = "$TODAY" ]; then
        echo "✅ Briefing already ran today ($TODAY) — skipping"
        exit 0
    fi
fi

echo "🚀 Starting curator briefing at $(date)..."

# Activate virtual environment
source venv/bin/activate

# Phase 3C.7: Pull new X bookmarks before curating.
# Failure is isolated — log and continue, never block the briefing.
echo "🔖 Pulling new X bookmarks..."
python x_pull_incremental.py 2>&1 || echo "⚠️  x_pull_incremental.py failed — continuing with existing signals"

# Generate briefing
python curator_rss_v2.py --model=grok-4-1 --fallback --temperature=0.7

# Send briefing via unified Telegram bot
export TELEGRAM_CHAT_ID="8379221702"
python telegram_bot.py --send

if [ $? -eq 0 ]; then
    echo "✅ Curator briefing generated and sent successfully at $(date)"
    exit 0
else
    echo "❌ ERROR: Failed to send Telegram briefing"
    exit 1
fi
