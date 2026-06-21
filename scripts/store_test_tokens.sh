#!/bin/bash
# Store test bot tokens in macOS Keychain.
# Usage: ./scripts/store_test_tokens.sh <system_test_token> <cos_test_token>
# Tokens from BotFather for minimoi_system_test_bot and minimoi_cos_test_bot.

set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <system_test_token> <cos_test_token>"
  exit 1
fi

SYSTEM_TOKEN="$1"
COS_TOKEN="$2"

echo "Storing system test bot token..."
security add-generic-password -s "telegram" -a "system_test_bot_token" -w "$SYSTEM_TOKEN" 2>/dev/null || \
security add-generic-password -s "telegram" -a "system_test_bot_token" -w "$SYSTEM_TOKEN" -U

echo "Storing CoS test bot token..."
security add-generic-password -s "telegram" -a "cos_test_bot_token" -w "$COS_TOKEN" 2>/dev/null || \
security add-generic-password -s "telegram" -a "cos_test_bot_token" -w "$COS_TOKEN" -U

echo "Done. Verifying..."
security find-generic-password -s "telegram" -a "system_test_bot_token" -w | cut -c1-12
security find-generic-password -s "telegram" -a "cos_test_bot_token" -w | cut -c1-12
echo "(showing first 12 chars only)"
