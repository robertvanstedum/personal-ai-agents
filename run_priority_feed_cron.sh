#!/bin/bash
# Priority Feed cron job - runs web search pipeline for all active priorities
# Runs hourly via launchd StartInterval=3600, with idempotency check (6AM–6PM window).
# Results accumulate in priorities.json and display in /curator_priorities.html

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

# ── Time gate: only run between 6AM and 6PM ──────────────────────────────────
HOUR=$(date +%H)
if [ "$HOUR" -lt 6 ] || [ "$HOUR" -ge 18 ]; then
    echo "⏭  Outside 6AM–6PM window (hour=$HOUR) — skipping"
    exit 0
fi

# ── Idempotency check: skip if priority feed already ran today ────────────────
TODAY=$(date +%Y-%m-%d)
PRIORITIES="$PROJECT_DIR/priorities.json"
if [ -f "$PRIORITIES" ]; then
    FILE_DATE=$(python3 -c "import json,os; d=json.load(open('$PRIORITIES')); print(d.get('last_run_date','')[:10])" 2>/dev/null)
    if [ "$FILE_DATE" = "$TODAY" ]; then
        echo "✅ Priority feed already ran today ($TODAY) — skipping"
        exit 0
    fi
fi

echo "🔎 Running priority feed pipeline at $(date)..."

# Activate virtual environment
source venv/bin/activate

python curator_priority_feed.py

if [ $? -eq 0 ]; then
    echo "✅ Priority feed completed successfully at $(date)"
    exit 0
else
    echo "❌ ERROR: Priority feed pipeline failed at $(date)"
    exit 1
fi
