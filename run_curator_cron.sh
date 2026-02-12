#!/bin/bash
# Curator cron job - generates briefing (Mini-moi sends to Telegram)
# To be called by OpenClaw cron

PROJECT_DIR="/Users/vanstedum/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment and run curator
source ai-env/bin/activate
python curator_rss.py --telegram

# Check that message was generated
if [ -f "telegram_message.txt" ]; then
    echo "✅ Curator briefing generated successfully at $(date)"
    echo "📱 Message ready in telegram_message.txt (Mini-moi will send)"
    exit 0
else
    echo "❌ ERROR: telegram_message.txt not found"
    exit 1
fi
