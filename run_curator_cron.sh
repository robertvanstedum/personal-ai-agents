#!/bin/bash
# Curator cron job - generates briefing + sends to Telegram automatically
# Model: xAI Grok (--model=xai) ~$0.15/day — fallback to mechanical if API down
# To be called by launchd at 7am daily

PROJECT_DIR="$HOME/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment and run curator
source venv/bin/activate
python curator_rss_v2.py --model=xai --fallback

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
