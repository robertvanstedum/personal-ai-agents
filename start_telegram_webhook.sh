#!/bin/bash
# Start Telegram feedback bot (MacBook/polling mode)
# Called by launchd on login
#
# MODE: Polling (for MacBook development)
# - Polls Telegram API every few seconds for button callbacks
# - No tunnel needed, simpler setup
# - Higher API usage than webhooks
#
# Mac Mini production: Switch to webhook mode
# - Requires stable Cloudflare tunnel URL
# - Replace polling with: python telegram_bot.py --webhook
# - See TELEGRAM_WEBHOOK_PLAN.md for architecture

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR" || exit 1

# Export environment
export TELEGRAM_CHAT_ID="8379221702"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Start polling bot (MacBook mode)
echo "$(date): Starting Telegram bot in polling mode..." >> "$LOG_DIR/telegram_bot.log"
source venv/bin/activate
exec python telegram_bot.py >> "$LOG_DIR/telegram_bot.log" 2>&1
