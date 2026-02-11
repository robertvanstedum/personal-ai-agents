#!/bin/bash
# Curator cron job - runs overnight and sends briefing to Telegram
# To be called by OpenClaw cron

CHAT_ID="8379221702"
PROJECT_DIR="/Users/vanstedum/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment and run curator
source ai-env/bin/activate
python curator_rss.py --telegram

# Read the generated message
if [ -f "telegram_message.txt" ]; then
    MESSAGE=$(cat telegram_message.txt)
    
    # Send via OpenClaw message tool (using curl to gateway API)
    # Get the gateway token from config
    TOKEN=$(grep -A 2 '"auth":' ~/.openclaw/openclaw.json | grep '"token"' | cut -d'"' -f4)
    
    curl -s -X POST "http://localhost:18789/api/message/send" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d "{
            \"channel\": \"telegram\",
            \"target\": \"$CHAT_ID\",
            \"message\": $(jq -Rs . <<< "$MESSAGE")
        }" > /dev/null 2>&1
    
    rm telegram_message.txt
    echo "Curator briefing sent to Telegram at $(date)"
else
    echo "ERROR: telegram_message.txt not found"
    exit 1
fi
