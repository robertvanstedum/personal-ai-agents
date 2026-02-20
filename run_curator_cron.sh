#!/bin/bash
# Curator cron job - generates briefing + sends to Telegram automatically
# Mode: Single-stage AI (Haiku) ~$0.20/day = $6/month
# To be called by OpenClaw cron at 7am daily

PROJECT_DIR="$HOME/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment and run curator with AI mode
source venv/bin/activate
python curator_rss_v2.py --mode=ai --telegram --fallback

# Check that message was generated
if [ -f "telegram_message.txt" ]; then
    echo "✅ Curator briefing generated successfully at $(date)"
    echo "📱 Message sent to Telegram automatically"
    exit 0
else
    echo "❌ ERROR: telegram_message.txt not found"
    exit 1
fi
