#!/bin/bash
# Intelligence cron job — WS5 Phase A daily observation layer
# Runs at 7:30 AM, 30 minutes after the morning briefing (7:00 AM)
# Reads curator_history.json + curator_sources.json, sends Telegram summary

PROJECT_DIR="$HOME/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment
source venv/bin/activate

export TELEGRAM_CHAT_ID="8379221702"

echo "🧠 Running intelligence pipeline at $(date)..."
python curator_intelligence.py --telegram

if [ $? -eq 0 ]; then
    echo "✅ Intelligence run completed successfully at $(date)"
    exit 0
else
    echo "❌ ERROR: Intelligence pipeline failed at $(date)"
    exit 1
fi
